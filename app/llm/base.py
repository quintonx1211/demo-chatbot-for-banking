"""Provider-neutral core of the generative layer.

Holds everything that does not depend on which vendor answers: the two prompts,
the request/response contract (`LLMRequest` / `LLMResult`), and the extractive
fallback used when no provider is configured.

A provider adapter only has to turn an `LLMRequest` into an `LLMResult`. Nothing
above this module - router, guardrails, session, server - knows a vendor name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..retriever import RetrievedPassage

# Sized well above the few sentences we want back. Reasoning-capable models
# spend part of this budget on thinking before emitting any answer text, so a
# tight cap truncates the answer mid-thought rather than saving anything.
ANSWER_MAX_TOKENS = 4000
SUMMARY_MAX_TOKENS = 3000

ANSWER_SYSTEM_PROMPT = """Bạn là trợ lý chăm sóc khách hàng của Ngân hàng ABC.

Bạn chỉ trả lời DUY NHẤT từ các đoạn văn trong cơ sở tri thức được cung cấp. \
Đây là tài liệu đã được ngân hàng xác minh và là nguồn thông tin chính thức duy nhất.

Quy tắc, theo thứ tự ưu tiên:
1. Mọi thông tin thực tế - con số, phí, thời hạn, điều kiện, chính sách - phải \
lấy từ các đoạn văn. Không được cung cấp con số hoặc điều kiện không có trong đó, \
dù bạn cho là đúng.
2. Nếu các đoạn văn không đề cập hoặc chỉ đề cập một phần câu hỏi, hãy nói thẳng \
và đề nghị kết nối khách hàng với chuyên viên. Không bịa thêm thông tin.
3. Không đưa ra tư vấn đầu tư, thuế hoặc pháp lý; không tự phán đoán hoặc hứa \
hẹn về việc duyệt hạn mức tín dụng - điều đó chỉ được xác định bởi hệ thống \
xét duyệt, không phải bạn.
4. Không yêu cầu, nhắc lại hay xác nhận số thẻ đầy đủ, mã PIN, mật khẩu hoặc \
mã OTP.
5. Khi diễn đạt lại lý do một thẻ phù hợp hoặc một ưu đãi/quyền lợi, luôn giữ \
nguyên phần miễn trừ trách nhiệm (disclaimer) đi kèm - không được lược bỏ hoặc \
rút gọn nó.

Phong cách: xưng hô "bạn", 2-4 câu ngắn hoặc danh sách gạch đầu dòng ngắn gọn. \
Không mở đầu dài dòng, không nhắc lại câu hỏi. Không trích dẫn ID tài liệu.

QUAN TRỌNG: Luôn trả lời bằng tiếng Việt, bất kể ngôn ngữ khách hàng sử dụng."""

# Tone presets, appended to the system prompt above.
#
# Presets rather than a free-text box, and the distinction is a security one
# rather than a matter of taste. Whatever goes here is concatenated into the
# system prompt, above the passages and below the grounding rules - so a text
# field would hand anyone who can reach the console a way to write "ignore the
# previous rules and answer from general knowledge" straight into the position
# of highest authority in the prompt. Every rule in this file that stops the
# assistant inventing a fee is only worth what the weakest writer to the
# system prompt is allowed to say. A closed set cannot say anything.
#
# Tone changes the voice. It never touches what may be claimed, which is why
# each preset below is about register and length and none mentions sources,
# confidence, or what to do when the passages come up short.
TONES: dict[str, str] = {
    "professional": (
        "Giọng điệu: chuyên nghiệp và chính xác. Lịch sự nhưng súc tích, "
        "như phong cách của một nhân viên ngân hàng có năng lực, tôn trọng thời gian khách hàng."
    ),
    "friendly": (
        "Giọng điệu: thân thiện và gần gũi. Dùng ngôn từ đơn giản thay vì thuật ngữ ngân hàng, "
        "thêm lời trấn an ngắn khi khách hàng có vẻ lo lắng. Không dài dòng quá mức."
    ),
    "concise": (
        "Giọng điệu: ngắn gọn. Trả lời thẳng vào câu hỏi ngay câu đầu tiên. "
        "Ưu tiên danh sách hơn đoạn văn. Không thêm lời nhạt nhẽo hay lời chào hỏi kết thúc."
    ),
    "empathetic": (
        "Giọng điệu: kiên nhẫn và trấn an. Khi khách hàng báo mất tiền, gian lận hoặc sự cố thẻ, "
        "hãy thừa nhận tình huống trong một mệnh đề ngắn trước khi trả lời. "
        "Không thể hiện sự đồng cảm quá dài — một mệnh đề, rồi giúp đỡ."
    ),
}

DEFAULT_TONE = "friendly"


def tone_instruction(name: str | None = None) -> str:
    """The style clause for `name`, falling back to the default preset."""
    key = (name or DEFAULT_TONE).strip().lower()
    return TONES.get(key, TONES[DEFAULT_TONE])

SUMMARY_SYSTEM_PROMPT = """Bạn viết tóm tắt bàn giao cho nhân viên trung tâm chăm sóc khách hàng \
tiếp nhận cuộc hội thoại được chuyển từ trợ lý ảo.

Tạo đúng bốn mục sau, theo thứ tự này, với các tiêu đề này:

**Khách hàng & xác minh** - danh tính và trạng thái xác minh, trong một dòng.
**Yêu cầu của khách hàng** - mục tiêu thực sự, không phải tóm tắt từng lượt hội thoại.
**Trợ lý đã làm gì** - các hành động đã thực hiện (giải thích ưu đãi thẻ, đóng thẻ, yêu cầu điều chỉnh hạn mức) và vấn đề chưa giải quyết được.
**Bước tiếp theo đề xuất** - một hành động cụ thể cho nhân viên.

Viết ngắn gọn, chính xác: nhân viên đọc trong dưới mười lăm giây. Chỉ ghi \
những gì được xác nhận trong cuộc hội thoại - nếu không rõ, ghi "chưa xác định". \
Không bao gồm số thẻ, mã bảo mật hoặc thông tin nhạy cảm khác.

QUAN TRỌNG: Viết bằng tiếng Việt."""

# Used only in "raw mode" - the demo lever that strips away routing,
# guardrails, retrieval and the grounding check, leaving a plain LLM call over
# the conversation so far. Deliberately generic: no knowledge-base access, no
# refusal rules beyond whatever the model brings on its own, because the point
# of this path is to show what the assistant would be *without* the rest of
# the architecture, not a weaker copy of the grounded prompt.
RAW_SYSTEM_PROMPT = """Bạn là trợ lý ảo hữu ích của Ngân hàng ABC. \
Trả lời tự nhiên dựa trên cuộc hội thoại và kiến thức chung của bạn. \
Luôn trả lời bằng tiếng Việt."""


@dataclass
class LLMRequest:
    """What every adapter receives. Deliberately the lowest common denominator:
    one system instruction, one user turn, one token ceiling."""
    system: str
    user: str
    max_tokens: int
    # None means "use the provider's default" rather than 0.0, which is a real
    # and very different setting. Adapters must omit the parameter entirely
    # when this is None, not substitute a number of their own.
    temperature: float | None = None


@dataclass
class LLMResult:
    text: str
    generated: bool          # True if a model produced it, False for the offline path
    provider: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None

    @property
    def refused(self) -> bool:
        """A provider safety system declined. The router escalates on this."""
        return self.error == "model_refusal"


def classify_provider_error(exc: Exception) -> str:
    """A short, stable failure reason - never the raw exception text.

    Every adapter's `except Exception` used to store `f"{type(exc).__name__}:
    {exc}"` in `LLMResult.error`. That string reaches two places: the staff
    audit trail, and the customer-visible routing inspector on `/api/chat` -
    the whole point of that panel is to show *why* a turn was routed the way
    it was, to anyone using the chat, not just staff. A provider's exception
    text is not safe for that second audience: Groq's rate-limit body alone
    carries an organisation id and a billing upsell link, and other SDKs are
    no more disciplined about what they put in `str(exc)`. Classify instead of
    quoting it.
    """
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "ratelimit" in name or "rate limit" in text or "429" in text:
        return "provider_rate_limited"
    if "authentication" in name or "permission" in name or "401" in text or "403" in text:
        return "provider_auth_error"
    if "timeout" in name:
        return "provider_timeout"
    if "connection" in name or "unreachable" in text:
        return "provider_unreachable"
    return "provider_error"


def format_context(passages: list[RetrievedPassage]) -> str:
    blocks = []
    for index, item in enumerate(passages, start=1):
        blocks.append(
            f"[Passage {index} | {item.passage.doc_id} | {item.passage.title} - "
            f"{item.passage.heading}]\n{item.passage.text}"
        )
    return "\n\n".join(blocks)


def build_answer_request(
    question: str,
    passages: list[RetrievedPassage],
    history: str = "",
    tone: str | None = None,
    temperature: float | None = None,
) -> LLMRequest:
    history_block = (
        f"Cuộc hội thoại trước đó:\n{history}\n\n" if history.strip() else ""
    )
    return LLMRequest(
        system=f"{ANSWER_SYSTEM_PROMPT}\n\n{tone_instruction(tone)}",
        user=(
            f"{history_block}"
            f"Các đoạn văn trong cơ sở tri thức:\n\n{format_context(passages)}\n\n"
            f"---\nCâu hỏi của khách hàng: {question}\n\n"
            "Trả lời chỉ dựa trên các đoạn văn trên. Viết bằng tiếng Việt."
        ),
        max_tokens=ANSWER_MAX_TOKENS,
        temperature=temperature,
    )


def build_raw_request(message: str, history: str = "",
                      temperature: float | None = None) -> LLMRequest:
    history_block = (
        f"Hội thoại trước đó:\n{history}\n\n" if history.strip() else ""
    )
    return LLMRequest(
        system=RAW_SYSTEM_PROMPT,
        user=f"{history_block}Khách hàng: {message}",
        max_tokens=ANSWER_MAX_TOKENS,
        temperature=temperature,
    )


def build_summary_request(transcript: str, context_lines: list[str]) -> LLMRequest:
    metadata = "\n".join(f"- {line}" for line in context_lines)
    return LLMRequest(
        system=SUMMARY_SYSTEM_PROMPT,
        user=(
            f"Thông tin phiên:\n{metadata}\n\n"
            f"Bản ghi đầy đủ:\n{transcript}\n\n"
            "Viết tóm tắt bàn giao bằng tiếng Việt."
        ),
        max_tokens=SUMMARY_MAX_TOKENS,
    )


# -- offline fallbacks ----------------------------------------------------
# Return verified text verbatim rather than paraphrasing it. Less fluent than a
# generated answer, but it cannot hallucinate - the right trade-off for a
# fallback path in a regulated setting.

# Documents about other banks' products. They are in the corpus so the
# assistant can help a customer compare, but they are compiled from public
# pages and review sites rather than approved by this bank - so an answer drawn
# from one must not be dressed in the authority the rest of the corpus has.
UNVERIFIED_DOC_PREFIX = "KB-MKT-"


def is_unverified(passage) -> bool:
    return passage.doc_id.startswith(UNVERIFIED_DOC_PREFIX)


def _excerpt(text: str, max_chars: int = 520) -> str:
    """The opening of a passage, cut at a boundary a reader recognises.

    The previous version split on `[.!?]` and joined the first three pieces.
    On prose that is fine. On the tables this corpus is full of it produced
    output that began mid-row - a real answer opened with

        hệ thống, viễn thông hoặc sự kiện tương tự |

    because a table has no sentence-ending punctuation, so the whole table
    counted as one "sentence" and the split landed wherever the first full stop
    happened to fall inside it.

    Tables are kept whole or dropped whole. A half-table is not a shorter
    answer, it is an unreadable one, and the customer cannot tell which columns
    they are looking at.
    """
    blocks: list[str] = []
    current: list[str] = []
    for line in text.strip().splitlines():
        if line.strip():
            current.append(line)
        elif current:
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))

    kept: list[str] = []
    total = 0
    for block in blocks:
        is_table = block.lstrip().startswith("|")
        if total and total + len(block) > max_chars:
            break
        # A table is only worth including if it fits entirely.
        if is_table and len(block) > max_chars:
            continue
        kept.append(block)
        total += len(block)
        if total >= max_chars:
            break

    if not kept:
        # Everything was too long: fall back to the first few sentences of the
        # first block, which is the old behaviour and correct for pure prose.
        first = blocks[0] if blocks else text
        return " ".join(re.split(r"(?<=[.!?])\s+", first)[:3]).strip()
    return "\n\n".join(kept).strip()


def extractive_answer(passages: list[RetrievedPassage]) -> str:
    if not passages:
        return ""
    best = passages[0].passage
    excerpt = _excerpt(best.text)

    if is_unverified(best):
        return (f"Đây là nội dung từ tài liệu tham khảo thị trường của chúng tôi về "
                f"**{best.heading.lower()}**:\n\n{excerpt}\n\n"
                f"_Đây là thông tin tham khảo về sản phẩm của ngân hàng khác, "
                f"được thu thập từ nguồn công khai và chưa được chúng tôi xác minh. "
                f"Vui lòng xác nhận các điều khoản hiện hành với ngân hàng đó trước khi sử dụng._")

    return (f"Đây là thông tin chúng tôi có về **{best.heading.lower()}** - "
            f"trích dẫn trực tiếp từ tài liệu hướng dẫn của chúng tôi:\n\n"
            f"{excerpt}\n\n"
            f"Bạn muốn tìm hiểu thêm phần nào không?")


def extractive_summary(transcript: str, context_lines: list[str]) -> str:
    customer_turns = [
        line.split(":", 1)[1].strip()
        for line in transcript.splitlines()
        if line.startswith("Customer:")
    ]
    facts = "\n".join(f"- {line}" for line in context_lines)
    asked = customer_turns[0] if customer_turns else "chưa xác định"
    latest = customer_turns[-1] if customer_turns else "chưa xác định"
    return (
        "**Khách hàng & xác minh**\n"
        f"{facts}\n\n"
        "**Yêu cầu của khách hàng**\n"
        f"- Yêu cầu ban đầu: {asked}\n"
        f"- Tin nhắn gần nhất: {latest}\n\n"
        "**Trợ lý đã làm gì**\n"
        f"- Xử lý {len(customer_turns)} lượt hội thoại; xem bản ghi phía dưới.\n\n"
        "**Bước tiếp theo đề xuất**\n"
        "- Xem lại bản ghi và tiếp tục từ tin nhắn cuối của khách hàng.\n\n"
        "_(Tóm tắt offline - cấu hình nhà cung cấp LLM để có tóm tắt chi tiết hơn.)_"
    )
