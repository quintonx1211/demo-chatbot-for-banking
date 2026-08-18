"""Deterministic scripted flows.

No model output reaches the customer from this module. Every response is
assembled from templates and data read out of the system of record - the
customer's own profile, the rule engine's output, or the product catalogue -
never invented. Flows that read a specific customer's profile first drive a
verification sub-flow via slot filling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import cards, memory
from .campaigns import CampaignBook
from . import db
from .nlu import PERSONAL_INTENTS
from .rules_engine import ENGINE
from .session import Session

CAMPAIGNS = CampaignBook()

# Flows that read a specific customer's profile must not run for an
# unverified caller - same set the classifier already treats as "personal".
PROTECTED_FLOWS = PERSONAL_INTENTS


@dataclass
class FlowResult:
    text: str
    handled: bool = True
    escalate: bool = False
    escalation_reason: str | None = None
    note: str = ""


def _first_name(customer: dict) -> str:
    """How to address this customer, read from the record, never inferred."""
    return customer.get("given_name") or customer["name"].split()[-1]


def _money(amount: float) -> str:
    """VND has no minor unit in practice, so printing two decimal places on
    an income of 25,000,000 is not just noise - it reads as a different kind
    of number."""
    return f"{amount:,.0f} VND"


# -- verification ---------------------------------------------------------

def _verification_prompt(session: Session, intent: str, text: str = "") -> FlowResult:
    """Start (or continue) the identity check before a personalised flow runs.

    Two factors, asked for and checked together: the phone number alone was a
    4-digit space with no lockout wider than one session. A second,
    independent factor - the national ID - takes the search space from
    "guess one 4-digit code" to "guess two 4-digit codes for the same person
    at once", the cheapest real improvement available without a true step-up
    channel standing in for a demo.
    """
    session.pending_flow = "verify"
    session.slots["target_intent"] = intent
    # The customer's original message ("...vay mua nhà không?") carries
    # information the resumed flow needs (which product they meant) that a
    # bare intent name does not - so it has to survive the identity check,
    # not just the fact that a flow was in progress.
    session.slots["target_text"] = text
    return FlowResult(
        text=(
            "Tôi sẵn sàng hỗ trợ. Trước tiên, cần xác minh nhanh để đảm bảo đây là bạn.\n\n"
            "Bạn có thể gửi **4 số cuối số điện thoại đã đăng ký** và "
            "**4 số cuối CMND/CCCD**, cách nhau bằng dấu cách không?"
        ),
        note="verification_started",
    )


def _handle_verification(session: Session, text: str) -> FlowResult:
    # The message has to *be* the two codes, not merely contain them - see
    # the anti-pattern this guards against: a customer pasting a card number
    # (the first 8 digits of which are shared by everyone holding the same
    # product) must not be treated as having verified anything.
    digits = re.findall(r"\d{4}", re.sub(r"[^0-9]+", " ", text))
    if len(digits) != 2 or re.search(r"\d{5,}", re.sub(r"[^0-9]+", " ", text)):
        return FlowResult(
            text=("Chưa đúng - tôi cần hai mã gồm 4 chữ số: "
                  "4 số cuối số điện thoại, rồi 4 số cuối CMND/CCCD. "
                  "Ví dụ: `1234 5678`.\n\n"
                  "Vui lòng không gửi số thẻ đầy đủ hoặc mã PIN - tôi không bao giờ cần những thông tin đó."),
            note="verification_retry",
        )

    phone, national_id = digits[0], digits[1]
    match = db.find_by_credentials(phone, national_id)
    if not match:
        attempts = int(session.slots.get("verify_attempts", "0")) + 1
        session.slots["verify_attempts"] = str(attempts)
        if attempts >= 3:
            session.reset_flow()
            return FlowResult(
                text=("Xin lỗi - tôi không thể xác minh thông tin của bạn, "
                      "và không thể tiếp tục thử thêm. Để tôi chuyển bạn đến "
                      "chuyên viên để hỗ trợ bạn trực tiếp."),
                escalate=True,
                escalation_reason="Xác minh danh tính thất bại ba lần",
                note="verification_failed",
            )
        return FlowResult(
            text=(f"Thông tin không khớp với hồ sơ của chúng tôi (lần thử {attempts}/3). "
                  "Vui lòng thử lại với cả hai mã."),
            note="verification_retry",
        )

    session.customer_id = match["customer_id"]
    session.verified = True
    target = session.slots.get("target_intent")
    target_text = session.slots.get("target_text", "")
    session.reset_flow()

    recalled = memory.store.summary(match["customer_id"])
    greeting = f"Cảm ơn, {_first_name(match)} - bạn đã được xác minh. "
    if recalled:
        greeting += recalled + " "

    # Scenario 1 (Automated Cross-Selling): the CRM's overnight batch, not
    # this module, decided who gets a proactive pitch - offers_for() only
    # reads that decision. Shown at most once per session
    # (`campaign_offered`), and attached to the post-verification greeting
    # regardless of what the customer originally asked for - the client's own
    # framing is that this pitch is proactive, not conditional on the
    # customer asking for it.
    pitch = ""
    offer = None
    if not getattr(session, "campaign_offered", False):
        offers = CAMPAIGNS.offers_for(match["customer_id"])
        if offers:
            offer = offers[0]
            session.campaign_offered = True
            pitch = "\n\n" + offer.body

    if target:
        follow_up = handle(session, target, target_text)
        return FlowResult(text=greeting + pitch + ("\n\n" if pitch else "") + follow_up.text,
                          escalate=follow_up.escalate,
                          escalation_reason=follow_up.escalation_reason,
                          note="verified_then_" + target)

    if offer:
        session.pending_flow = "cross_sell_interest"
        greeting += (
            pitch +
            "\n\nBạn có quan tâm không? Nếu có, bạn quan tâm nhất điều gì: "
            "mua sắm online, ăn uống, du lịch, hay chơi golf?"
        )
        return FlowResult(text=greeting, note="proactive_offer:" + offer.campaign_id)

    return FlowResult(text=greeting + "Tôi có thể giúp gì cho bạn?", note="verified")


# -- product comparison (no identity needed - catalogue data is public) ----

def _product_comparison(text: str) -> FlowResult:
    """>=2 cards named in one message -> a comparison table read straight
    from products.json. The LLM is never asked to compare figures - a wrong
    number here would be an invented one, not a paraphrased one."""
    mentioned = ENGINE.mentions(text)
    if len(mentioned) < 2:
        return FlowResult(text="", handled=False)

    mentioned = mentioned[:3]
    lines = [f"So sánh {' và '.join(p['name'] for p in mentioned)}:", ""]
    for product in mentioned:
        fee = product.get("annual_fee", {})
        lines.append(f"**{product['name']}**")
        lines.append(f"- {product['value_proposition']}")
        lines.append(f"- Phí thường niên: {fee.get('amount', '')} "
                     f"({fee.get('waiver_condition', '')})")
        lines.append("")
    return FlowResult(text="\n".join(lines).strip(), note="product_comparison")


# -- personalised flows (require verification) -----------------------------

_YES_RE = re.compile(r"^\s*(có|đồng ý|ok|okay|đúng|xác nhận|yes|confirm|sure)\b", re.IGNORECASE)
_NO_RE = re.compile(r"^\s*(không|khỏi|thôi|no|cancel|đừng)\b", re.IGNORECASE)

# "500 triệu", "500tr", or a bare figure like "500000000" - the customer
# names the number, this only recognises the shape of it.
_AMOUNT_RE = re.compile(r"(\d[\d.,]*)\s*(triệu|tr\b|million)?", re.IGNORECASE)


def _parse_amount(text: str) -> float | None:
    match = _AMOUNT_RE.search(text or "")
    if not match:
        return None
    digits = match.group(1).replace(".", "").replace(",", "")
    if not digits.isdigit():
        return None
    value = float(digits)
    if match.group(2):
        value *= 1_000_000
    return value


def _render_match(card: dict, match) -> FlowResult:
    lines = [f"{card['name']} phù hợp với điều bạn vừa nói:", ""]
    for line in match.matched_lines:
        lines.append(f"- {line.text}")
    lines.append("")
    lines.append(f"_{match.disclaimer}_")
    return FlowResult(text="\n".join(lines), note="cross_sell_matched:" + card["id"])


def _cross_sell_interest(session: Session, text: str) -> FlowResult:
    """Scenario 2: the customer states what they care about, the bot explains
    why their one assigned card (`card_for_segment`) fits it.

    Asks the one-question slot-fill only when there is nothing specific to
    match yet (no stated_interests on file, no real match in the triggering
    message itself, and it has not already asked once this turn cycle) -
    otherwise a generic "any offers for me?" with an empty profile would loop
    forever instead of answering with the best available evidence.
    """
    profile = session.customer
    card = ENGINE.card_for_segment(profile.get("segment"))
    if not card:
        return FlowResult(
            text=f"Tôi chưa xác định được thẻ phù hợp với hồ sơ của {_first_name(profile)}.",
            note="cross_sell_no_card",
        )

    already_asked = session.slots.get("cross_sell_asked") == "1"
    interest_text = text.strip() or " ".join(profile.get("stated_interests") or [])

    if interest_text:
        match = ENGINE.explain_fit(card, interest_text)
        specific = any(line.source != "value_proposition" for line in match.matched_lines)
        if specific or already_asked or profile.get("stated_interests"):
            session.reset_flow()
            return _render_match(card, match)

    session.pending_flow = "cross_sell_interest"
    session.slots["cross_sell_asked"] = "1"
    return FlowResult(
        text=(f"{_first_name(profile)} quan tâm nhất điều gì: mua sắm online, "
              "ăn uống, du lịch, hay chơi golf?"),
        note="cross_sell_ask_interest",
    )


def _reward_inquiry(session: Session) -> FlowResult:
    """Scenario 3: "what do I get" without naming a new interest - lists the
    card's reward lines directly (data read, not a matched-and-ranked
    explanation, since there is nothing to rank against)."""
    profile = session.customer
    card = ENGINE.card_for_segment(profile.get("segment"))
    if not card:
        return FlowResult(
            text=f"Tôi chưa xác định được thẻ phù hợp với hồ sơ của {_first_name(profile)}.",
            note="reward_inquiry_no_card",
        )
    lines = [f"Quyền lợi hiện có trên {card['name']}:", ""]
    for line in card.get("reward_scheme", []):
        lines.append(f"- {line}")
    lines.append("")
    lines.append(f"_{ENGINE._disclaimer_for(card)}_")
    return FlowResult(text="\n".join(lines), note="reward_inquiry:" + card["id"])


def _card_close(session: Session, text: str) -> FlowResult:
    """Scenario 3: close the card. One-way, so it is confirmed before it runs."""
    stage = session.slots.get("card_close_stage")
    if stage == "await_confirm":
        session.reset_flow()
        if _NO_RE.search(text):
            return FlowResult(text="Đã huỷ yêu cầu - thẻ của bạn vẫn hoạt động bình thường.",
                              note="card_close_cancelled")
        if not _YES_RE.search(text):
            return FlowResult(text="Bạn xác nhận đóng thẻ không? Vui lòng trả lời có hoặc không.",
                              note="card_close_unclear")
        try:
            result = cards.close_card(session.customer_id, session.session_id)
        except cards.TransitionError as exc:
            return FlowResult(text=str(exc), note="card_close_error")
        return FlowResult(text=f"Đã đóng thẻ của bạn (mã tham chiếu {result['reference']}).",
                          note="card_close_done")

    card = cards.get_card(session.customer_id)
    if not card or card["status"] != "active":
        return FlowResult(text="Tôi không tìm thấy thẻ đang hoạt động nào trên hồ sơ của bạn.",
                          note="card_close_none")
    session.pending_flow = "card_close"
    session.slots["card_close_stage"] = "await_confirm"
    return FlowResult(text="Bạn có chắc muốn đóng thẻ này không? Vui lòng xác nhận có/không.",
                      note="card_close_confirm")


def _submit_limit_request(session: Session, amount: float | None) -> FlowResult:
    if amount is None:
        return FlowResult(text="Tôi chưa nhận được số hạn mức hợp lệ, bạn vui lòng thử lại.",
                          note="card_limit_invalid")
    try:
        result = cards.request_limit_adjustment(session.customer_id, amount, session.session_id)
    except cards.TransitionError as exc:
        return FlowResult(text=str(exc), note="card_limit_error")
    return FlowResult(
        text=(f"Đã ghi nhận yêu cầu điều chỉnh hạn mức lên {_money(amount)} "
              f"(mã tham chiếu {result['reference']}). Đây là yêu cầu chờ duyệt - "
              "hạn mức hiện tại chưa thay đổi cho tới khi có kết quả xét duyệt."),
        note="card_limit_requested",
    )


def _card_limit_adjust(session: Session, text: str) -> FlowResult:
    """Scenario 3: records a limit-change request. Never auto-approved - see
    `app/cards.py::request_limit_adjustment`."""
    stage = session.slots.get("limit_stage")
    if stage == "await_amount":
        amount = _parse_amount(text)
        session.reset_flow()
        return _submit_limit_request(session, amount)

    amount = _parse_amount(text)
    if amount is not None:
        return _submit_limit_request(session, amount)

    session.pending_flow = "card_limit_adjust"
    session.slots["limit_stage"] = "await_amount"
    return FlowResult(text="Bạn muốn hạn mức mới là bao nhiêu?", note="card_limit_ask_amount")


# -- dispatch -------------------------------------------------------------

def handle(session: Session, intent: str, text: str) -> FlowResult:
    """Run the scripted flow for `intent`, gating personal ones on verification."""
    if intent in PROTECTED_FLOWS and not session.verified:
        return _verification_prompt(session, intent, text)

    if intent == "product_comparison":
        return _product_comparison(text)
    if intent == "cross_sell_interest":
        return _cross_sell_interest(session, text)
    if intent == "reward_inquiry":
        return _reward_inquiry(session)
    if intent == "card_close":
        return _card_close(session, text)
    if intent == "card_limit_adjust":
        return _card_limit_adjust(session, text)
    if intent == "greeting":
        return FlowResult(
            text=("Xin chào! Tôi là trợ lý ảo của Ngân hàng ABC. Tôi có thể "
                  "trả lời câu hỏi về thẻ, so sánh thẻ, giải thích ưu đãi phù hợp với bạn, "
                  "và hỗ trợ đóng thẻ / điều chỉnh hạn mức. Bạn cần hỗ trợ gì?"),
            note="greeting",
        )
    if intent == "smalltalk":
        # Acknowledgements and "are you there?" are not questions. Sending them
        # through retrieval produced the worst turn in the demo: a customer
        # typing "ok thanks" was offered a human agent.
        return FlowResult(
            text="Tôi đây. Bạn cần hỏi thêm điều gì không?",
            note="smalltalk",
        )
    if intent == "goodbye":
        return FlowResult(
            text="Rất vui được hỗ trợ bạn. Chúc bạn một ngày tốt lành!", note="goodbye")
    if intent == "human_agent":
        return FlowResult(
            text="Được, để tôi kết nối bạn ngay.",
            escalate=True,
            escalation_reason="Customer explicitly asked for a human agent",
            note="explicit_handoff_request",
        )

    return FlowResult(text="", handled=False)


CANCEL_RE = re.compile(
    r"\b(cancel|stop|never ?mind|nevermind|forget it|no thanks|"
    r"skip|quit|exit|go back|start over|something else)\b",
    re.IGNORECASE,
)

# A flow that re-prompts this many times in a row is not going to succeed. The
# customer is answering a different question than the one being asked.
MAX_FLOW_MISSES = 2


def wants_out(text: str) -> bool:
    return bool(CANCEL_RE.search(text))


def continue_pending(session: Session, text: str) -> FlowResult | None:
    """Resume a mid-conversation flow before the classifier gets a say.

    Slot answers like "9411 3147" or "25000000" carry no intent signal, so an
    in-progress flow must take priority over classification.
    """
    if session.pending_flow == "verify":
        return _handle_verification(session, text)
    if session.pending_flow == "cross_sell_interest":
        return _cross_sell_interest(session, text)
    if session.pending_flow == "card_close":
        return _card_close(session, text)
    if session.pending_flow == "card_limit_adjust":
        return _card_limit_adjust(session, text)
    return None
