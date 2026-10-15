"""The orchestrator that ties the hybrid architecture together.

Routing order for every customer turn:

    1. pending flow      - a half-finished scripted flow owns the turn
    2. guardrails        - restricted topics are refused before any model runs
    3. NLU               - high confidence goes to a deterministic flow
    4. RAG + LLM         - anything else is answered from the knowledge base
    5. escalation        - low confidence, no evidence, or ungrounded output

Every branch writes an audit entry, so the route taken for any answer is
recoverable after the fact.
"""

from __future__ import annotations

import difflib
import re
import time
from dataclasses import dataclass, field

from . import agents, demo_script, flows, guardrails, llm, playbook, policy
from . import canned_responses as R
from . import memory as memory_mod
from .llm import rerank, route
from .nlu import HIGH_CONFIDENCE, LOW_CONFIDENCE, IntentClassifier, IntentPrediction
from .retriever import MARKET_DOC_PREFIX, MIN_RELEVANCE, KnowledgeBase
from .session import Session, SessionStore
from .textmodel import tokenize
from .trace import Trace

MAX_LOW_CONFIDENCE_STREAK = 2

MARKET_MIN_GROUNDING = 0.35

_MARKET_DISCLAIMER = (
    "\n\n_Lưu ý: thông tin trên tham khảo từ nguồn công khai về sản phẩm của ngân "
    "hàng khác, chưa được ABC Bank xác minh - quý khách vui lòng xác nhận lại với "
    "ngân hàng phát hành._"
)


def _ensure_market_disclaimer(text: str) -> str:
    """Chèn disclaimer 'chưa xác minh' nếu model chưa tự nêu (tránh chèn đúp)."""
    low = text.lower()
    if any(
        k in low
        for k in (
            "chưa được xác minh",
            "chưa xác minh",
            "chưa xác nhận",
            "ngân hàng phát hành",
            "xác nhận lại với",
            "tham khảo thị trường",
            "tham khảo từ nguồn",
        )
    ):
        return text
    return text.rstrip() + _MARKET_DISCLAIMER


_CARD_KEYWORDS = ("classic", "platinum", "signature", "infinite")


def _fix_card_typos(text: str) -> str:
    """Sửa token sai chính tả rất gần một tên thẻ (vd 'platinun' -> 'platinum').
    Chỉ đổi khi token đủ dài và trùng khớp cao, để không đụng từ bình thường."""
    out = []
    changed = False
    for token in text.split():
        bare = token.lower().strip(".,?!;:")
        if len(bare) >= 6 and bare not in _CARD_KEYWORDS:
            match = difflib.get_close_matches(bare, _CARD_KEYWORDS, n=1, cutoff=0.82)
            if match:
                out.append(match[0])
                changed = True
                continue
        out.append(token)
    return " ".join(out) if changed else text


_MENTION_RE = re.compile(
    r"^\s*@(agent|human|staff|bot|assistant|ai)(?=[\s:,]|$)[:,]?\s*", re.IGNORECASE
)
_AGENT_MENTIONS = {"agent", "human", "staff"}

_LEAVE_RE = re.compile(
    r"^\s*(/leave|/bot|/back|end chat|leave chat)\s*$", re.IGNORECASE
)

_AFFIRM_RE = re.compile(
    r"^\s*(yes|yeah|yep|yup|ok(ay)?|sure|please|go ahead|do it|connect me|"
    r"put me through|transfer me|i do|"
    r"có|được|đồng ý|kết nối|vâng|ừ|oke|ok)(?=[\s.!,]|$)",
    re.IGNORECASE,
)
_DECLINE_RE = re.compile(
    r"^\s*(no|nope|not now|no thanks|nah|don'?t|cancel|"
    r"không|thôi|không cần|không muốn|tiếp tục)(?=[\s.!,]|$)",
    re.IGNORECASE,
)

_HANDOFF_OFFER_RE = re.compile(
    r"kết nối[^.]{0,40}(chuyên viên|nhân viên|tư vấn viên)"
    r"|chuyển[^.]{0,40}(chuyên viên|bộ phận chăm sóc|nhân viên hỗ trợ|tư vấn viên)"
    r"|gặp[^.]{0,20}(chuyên viên|nhân viên|tư vấn viên)",
    re.IGNORECASE,
)


def split_mention(text: str) -> tuple[str | None, str]:
    """Split a leading @mention off a message. Returns (target, remainder)."""
    match = _MENTION_RE.match(text or "")
    if not match:
        return None, text
    target = match.group(1).lower()
    who = "agent" if target in _AGENT_MENTIONS else "bot"
    return who, text[match.end() :].strip()


_FLOW_LABELS = {
    "verify": "xác minh danh tính",
    "block_card": "khóa thẻ",
    "freeze_card": "tạm khóa thẻ",
    "unfreeze_card": "mở khóa thẻ",
    "cross_sell_interest": "tìm ưu đãi phù hợp",
    "card_close": "đóng thẻ",
    "card_limit_adjust": "điều chỉnh hạn mức",
    "reward_inquiry": "xem quyền lợi thẻ",
    "product_comparison": "so sánh sản phẩm",
}


def _flow_label(name: str | None) -> str:
    return _FLOW_LABELS.get(name or "", "yêu cầu trước đó")


ESCALATION_MESSAGE = R.ESCALATION_MESSAGE
REQUEUED_MESSAGE = R.REQUEUED_MESSAGE


@dataclass
class TurnResult:
    text: str
    route: str
    intent: str
    confidence: float
    sources: list[dict] = field(default_factory=list)
    generated: bool = False
    grounding: float | None = None
    escalated: bool = False
    escalation_reason: str | None = None
    latency_ms: int = 0
    offer_escalation: bool = False
    with_agent: bool = False
    trace: list = field(default_factory=list)
    debug: dict = field(default_factory=dict)


class Router:
    def __init__(self) -> None:
        self.classifier = IntentClassifier()
        self.kb = KnowledgeBase()
        self.sessions = SessionStore()

    def handle_turn(self, session: Session, text: str) -> TurnResult:
        started = time.perf_counter()
        self.trace = Trace()
        session.add_message("customer", text)

        if session.script_name:
            script_name = session.script_name
            step = demo_script.next_reply(session)
            if step is not None:
                self.trace.decide(
                    "demo_script",
                    f"scripted reply ({script_name})",
                    "routing, guardrails, retrieval and the model were all bypassed",
                )
                result = TurnResult(
                    text=step.get("reply", ""),
                    route=step.get("route", "scripted"),
                    intent=step.get("intent", "scripted"),
                    confidence=1.0,
                    sources=step.get("sources", []),
                    generated=False,
                    debug={
                        "note": f"scripted step {session.script_step}/"
                        f"{demo_script.step_count(script_name)} "
                        f"({script_name})"
                    },
                )
                result.latency_ms = int((time.perf_counter() - started) * 1000)
                result.trace = self.trace.to_list()
                session.add_message("assistant", result.text)
                session.record(
                    utterance=text,
                    route=result.route,
                    intent=result.intent,
                    confidence=1.0,
                    generated=False,
                    latency_ms=result.latency_ms,
                    note=result.debug["note"],
                )
                return result
            self.trace.add(
                "demo_script",
                f"'{script_name}' script ended",
                "falling back to normal routing for this turn",
            )

        if session.raw_mode:
            result = self._raw_turn(session, text)
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            result.trace = self.trace.to_list()
            session.add_message("assistant", result.text)
            session.record(
                utterance=text,
                route=result.route,
                intent=result.intent,
                confidence=result.confidence,
                generated=result.generated,
                latency_ms=result.latency_ms,
                note="raw mode: no routing, guardrails, retrieval or grounding check",
            )
            return result

        mention, body = split_mention(text)

        if (session.handled_by or session.escalated) and _LEAVE_RE.match(text):
            agent_name = session.handled_by
            session.handled_by = None
            session.escalated = False
            session.escalation_reason = None
            session.pending_escalation = None
            session.record(
                utterance=text,
                route="deterministic",
                intent="left_agent",
                confidence=1.0,
                note=(
                    f"customer left the chat with {agent_name}"
                    if agent_name
                    else "customer left the handoff queue before it was claimed"
                ),
            )
            reply = (
                f"Quý khách đã quay lại với trợ lý ảo - {agent_name} đã kết thúc cuộc trò chuyện này. "
                "Tôi có thể hỗ trợ gì tiếp theo cho quý khách?"
                if agent_name
                else R.LEFT_AGENT_NO_NAME
            )
            session.add_message("assistant", reply)
            return TurnResult(
                text=reply,
                route="deterministic",
                intent="left_agent",
                confidence=1.0,
                latency_ms=int((time.perf_counter() - started) * 1000),
                debug={"note": "customer ended the agent conversation"},
            )

        if _LEAVE_RE.match(text):
            reply = R.LEAVE_NOOP
            session.add_message("assistant", reply)
            session.record(
                utterance=text,
                route="deterministic",
                intent="left_agent",
                confidence=1.0,
                note="/leave with no agent attached",
            )
            return TurnResult(
                text=reply,
                route="deterministic",
                intent="left_agent",
                confidence=1.0,
                latency_ms=int((time.perf_counter() - started) * 1000),
                debug={"note": "no agent was attached"},
            )

        if (session.handled_by or session.escalated) and mention != "bot":
            waiting_for = session.handled_by or "the next available agent"
            session.record(
                utterance=text,
                route="agent",
                intent="awaiting_agent",
                confidence=1.0,
                note=f"queued for {waiting_for}",
            )
            return TurnResult(
                text="",
                route="agent",
                intent="awaiting_agent",
                confidence=1.0,
                with_agent=True,
                escalated=session.escalated,
                latency_ms=int((time.perf_counter() - started) * 1000),
                debug={"note": f"assistant stood down; queued for {waiting_for}"},
            )

        if mention == "agent" and not session.handled_by:
            result = self._escalate(
                session,
                "Customer addressed a human agent directly",
                intent="human_agent",
                confidence=1.0,
            )
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            session.add_message("assistant", result.text)
            session.record(
                utterance=text,
                route=result.route,
                intent=result.intent,
                confidence=1.0,
                latency_ms=result.latency_ms,
                note=result.debug.get("note", ""),
            )
            return result

        result = self._route(session, body if mention else text)

        result.latency_ms = int((time.perf_counter() - started) * 1000)
        if (
            not session.pending_escalation
            and not session.handled_by
            and result.route not in ("escalation", "agent", "escalation_offered")
            and _HANDOFF_OFFER_RE.search(result.text or "")
        ):
            session.pending_escalation = "Trợ lý đề nghị kết nối chuyên viên"
        result.offer_escalation = bool(session.pending_escalation)
        result.with_agent = bool(session.handled_by)
        result.trace = self.trace.to_list()
        session.add_message("assistant", result.text)
        self._remember(session, text, result)
        session.record(
            utterance=text,
            route=result.route,
            intent=result.intent,
            confidence=result.confidence,
            sources=[s["citation"] for s in result.sources],
            grounding=result.grounding,
            generated=result.generated,
            latency_ms=result.latency_ms,
            note=" · ".join(
                filter(None, [self.trace.summary, result.debug.get("note", "")])
            ),
        )
        return result

    def _raw_turn(self, session: Session, text: str) -> TurnResult:
        """Answer with nothing but the model and the conversation so far.

        No guardrail, no intent classifier, no retrieval, no grounding check -
        this is the baseline the rest of the router exists to improve on. It
        exists to be switched on live next to the grounded path, not to be a
        second production mode: it has no PII redaction and no compliance
        gate, so a raw-mode answer to a regulated question or a customer's
        pasted card number goes to the model exactly as typed.
        """
        history = session.transcript(limit=12)
        result = llm.raw_chat(text, history=history)
        self.trace.decide(
            "raw_mode",
            "architecture disabled for this conversation",
            "sent directly to the model with conversation history as the only "
            "context - no guardrail, retrieval, or grounding check ran",
        )
        return TurnResult(
            text=result.text,
            route="raw_llm",
            intent="raw_llm",
            confidence=0.0,
            generated=result.generated,
            debug={
                "note": result.error or ("llm" if result.generated else "offline"),
                "tokens": {"in": result.input_tokens, "out": result.output_tokens},
            },
        )

    def _remember(self, session: Session, text: str, result: TurnResult) -> None:
        """Carry a topic, never a sentence, into the customer's next conversation.

        The label is a bag of the question's content words, not the question:
        stemmed, stopped, capped at four, and only written for a verified
        customer. That is enough for "you asked about travel insurance last
        time" and not enough to reconstruct what they typed - which matters,
        because whatever is stored here outlives the session that produced it.
        """
        if not session.customer_id:
            return
        topic = " ".join(dict.fromkeys(tokenize(text)))[:60].strip()
        if result.route in ("escalation", "escalation_offered"):
            memory_mod.store.remember(
                session.customer_id,
                "outcome",
                "unresolved",
                detail=topic or "chủ đề chưa xác định",
            )
        elif result.route == "rag" and result.sources:
            memory_mod.store.remember(
                session.customer_id,
                "topic",
                result.sources[0]["heading"].lower(),
                detail=result.sources[0]["citation"],
            )
        elif result.route == "deterministic" and result.intent in (
            "card_offers",
            "activate_card",
            "cross_sell_interest",
            "reward_inquiry",
        ):
            memory_mod.store.remember(session.customer_id, "campaign", result.intent)

    def _route(self, session: Session, text: str) -> TurnResult:
        if session.pending_escalation:
            reason = session.pending_escalation
            session.pending_escalation = None
            self.trace.add(
                "handoff_offer",
                "an offer was outstanding",
                f"offered because: {reason}",
            )

            if _AFFIRM_RE.match(text):
                self.trace.decide(
                    "handoff_offer", "customer accepted", "handing off to a human"
                )
                return self._escalate(
                    session, reason, intent="escalation_accepted", confidence=1.0
                )
            if _DECLINE_RE.match(text):
                self.trace.decide(
                    "handoff_offer", "customer declined", "assistant continues"
                )
                return TurnResult(
                    text=R.ESCALATION_DECLINED,
                    route="deterministic",
                    intent="escalation_declined",
                    confidence=1.0,
                    debug={"note": "customer declined the handoff"},
                )

        if session.pending_flow:
            abandon = self._should_abandon_flow(session, text)
            if abandon:
                flow_name = session.pending_flow
                session.reset_flow()
                session.flow_misses = 0
                result = self._route_fresh(session, text)
                result.debug["note"] = " · ".join(
                    filter(
                        None,
                        [
                            f"abandoned:{flow_name}({abandon})",
                            result.debug.get("note", ""),
                        ],
                    )
                )
                return result

        pending = flows.continue_pending(session, text)
        if pending is not None:
            if pending.note.endswith("_retry"):
                session.flow_misses += 1
            else:
                session.flow_misses = 0

            if pending.escalate:
                return self._escalate(
                    session, pending.escalation_reason, prefix=pending.text
                )
            return TurnResult(
                text=pending.text,
                route="deterministic",
                intent=f"flow:{session.pending_flow or 'resumed'}",
                confidence=1.0,
                debug={"note": pending.note},
            )

        return self._route_fresh(session, text)

    def _apply_llm_router(
        self, session: Session, text: str, lexical: IntentPrediction
    ) -> IntentPrediction:
        """Optionally let a model propose the intent instead.

        Returns the prediction the router acts on. In every mode the result is
        only ever an *intent name and a confidence* - the caller still applies
        HIGH_CONFIDENCE, still gates protected flows on verification, and still
        goes through the card state machine. Nothing here authorises anything.

        The agreement rate between the two classifiers is recorded on every
        turn. That is the point of shadow mode: it turns "the LLM is better"
        from a claim into a number, on this corpus, with this traffic.
        """
        mode = route.mode()
        if mode == "nlu":
            return lexical

        safe_text = guardrails.redact(text)
        history = guardrails.redact(session.transcript(limit=4))
        verdict = route.classify(safe_text, self.classifier.intents, history)

        if verdict is None:
            self.trace.add(
                "router", "lexical (không có provider cho LLM routing)", f"mode={mode}"
            )
            return lexical

        if verdict.get("error"):
            self.trace.add(
                "router",
                f"lexical (LLM routing lỗi: {verdict['error']})",
                f"mode={mode}",
            )
            return lexical

        agreed = verdict["intent"] == lexical.intent
        session.router_comparisons += 1
        if agreed:
            session.router_agreements += 1
        else:
            session.router_disagreements.append(
                {
                    "text": safe_text[:80],
                    "lexical": lexical.intent,
                    "lexical_confidence": round(lexical.confidence, 2),
                    "llm": verdict["intent"],
                    "llm_confidence": round(verdict["confidence"], 2),
                    "why": verdict.get("why", ""),
                    "used": "llm" if mode == "llm" else "lexical",
                }
            )

        self.trace.add(
            "router",
            f"LLM chọn {verdict['intent']} @ {verdict['confidence']:.2f}"
            + ("" if agreed else f" (lexical: {lexical.intent})"),
            f"mode={mode}; " + ("đang dùng" if mode == "llm" else "chỉ ghi nhận"),
        )

        if mode == "shadow":
            return lexical

        return IntentPrediction(
            intent=verdict["intent"] or "unknown",
            confidence=verdict["confidence"],
            runner_up=lexical.intent,
            runner_up_confidence=lexical.confidence,
            scores=lexical.scores,
        )

    def _should_abandon_flow(self, session: Session, text: str) -> str | None:
        """Reason to drop the in-progress flow, or None to keep it."""
        if flows.wants_out(text):
            return "explicit-cancel"

        if session.pending_flow == "verify":
            expected = 12 if session.slots.get("verify_stage") == "cccd" else 10
            if len(re.sub(r"[^0-9]", "", text)) < expected:
                return "new-question-during-verify"
            return None

        if session.pending_flow in flows.CARD_ACTIONS:
            return None

        if (
            session.pending_flow == "cross_sell_interest"
            and flows.is_cross_sell_answer(text)
        ):
            return None

        if session.flow_misses >= flows.MAX_FLOW_MISSES:
            return f"no-progress-after-{session.flow_misses}"

        prediction = self.classifier.predict(text)
        if prediction.confidence >= LOW_CONFIDENCE and prediction.intent not in (
            "unknown",
            session.pending_flow,
        ):
            return f"new-intent:{prediction.intent}@{prediction.confidence:.2f}"
        return None

    def _route_fresh(self, session: Session, text: str) -> TurnResult:

        verdict = guardrails.check_input(text)
        if not verdict.allowed:
            session.low_confidence_streak = 0
            self.trace.decide(
                "guardrail",
                f"blocked: {verdict.topic}",
                f"{verdict.reason} - no model was called",
            )
            return TurnResult(
                text=guardrails.RESTRICTED_RESPONSE,
                route="guardrail",
                intent=verdict.topic or "restricted",
                confidence=1.0,
                debug={"note": f"blocked:{verdict.topic} - {verdict.reason}"},
            )

        prediction = self.classifier.predict(text)
        prediction = self._apply_llm_router(session, text, prediction)
        self.trace.add(
            "nlu",
            f"intent {prediction.intent} @ {prediction.confidence:.2f}",
            f"threshold {HIGH_CONFIDENCE} for a scripted flow"
            + (
                f"; runner-up {prediction.runner_up} @ "
                f"{prediction.runner_up_confidence:.2f}"
                if prediction.runner_up
                else ""
            ),
        )

        if prediction.is_high_confidence and prediction.intent in agents.SKILL_CATALOG:
            agent = agents.store.agent_for_intent(
                prediction.intent, session.current_agent_id
            )
            if agent is None:
                self.trace.decide(
                    "workforce",
                    f"{prediction.intent} đang tắt",
                    "không agent nào đảm nhiệm nghiệp vụ này",
                )
                return self._escalate(
                    session,
                    f"skill '{prediction.intent}' disabled by admin",
                    intent=prediction.intent,
                    confidence=prediction.confidence,
                    prefix="Rất tiếc, nghiệp vụ này hiện chưa được hỗ trợ.",
                )
            if agent.id != session.current_agent_id:
                self.trace.add(
                    "workforce",
                    f"chuyển sang {agent.name}",
                    f"trước: {session.current_agent_id or 'chưa có'}; "
                    f"skill {prediction.intent}",
                )
                session.current_agent_id = agent.id

        if prediction.is_high_confidence and prediction.intent not in (
            "knowledge_query",
            "product_faq",
        ):
            flow = flows.handle(session, prediction.intent, text)
            if flow.handled:
                self.trace.decide(
                    "nlu",
                    "above threshold - scripted flow",
                    "answer assembled from the customer record; no model involved",
                )
                session.low_confidence_streak = 0
                if flow.escalate:
                    return self._escalate(
                        session,
                        flow.escalation_reason,
                        intent=prediction.intent,
                        confidence=prediction.confidence,
                        prefix=flow.text,
                    )
                return TurnResult(
                    text=flow.text,
                    route="deterministic",
                    intent=prediction.intent,
                    confidence=prediction.confidence,
                    debug={"note": flow.note, "scores": prediction.scores},
                )

        return self._answer_from_kb(session, text, prediction)

    def _retrieve(self, text: str) -> tuple[list, str]:
        """Retrieve passages, optionally through the LLM reranking stage."""
        if not rerank.enabled():
            return self.kb.search(text, top_k=3), ""
        candidates = self.kb.search(
            text, top_k=rerank.CANDIDATE_POOL, gate=rerank.RECALL_GATE
        )
        return rerank.rerank(text, candidates, top_k=3)

    _FOLLOWUP_RE = re.compile(
        r"^(còn\b|thế còn\b|vậy còn\b|còn về\b|còn thẻ\b|còn loại\b|còn cái\b|"
        r"thì sao\b|thế thì\b|vậy thì\b|sao ạ\b|"
        r"cái đó|thẻ đó|loại đó|nó |của nó|"
        r"phí bao nhiêu|bao nhiêu|như thế nào|what about\b|how about\b)",
        re.IGNORECASE,
    )

    _LEAD_FILLER_RE = re.compile(
        r"^\s*(?:"
        r"nghe(?:\s+(?:có vẻ|thì))?(?:\s+(?:hấp dẫn|hay|ổn|được|thú vị))?"
        r"(?:\s+(?:đấy|nhỉ|ha|ghê|quá))?"
        r"|à|ờ+|ừm?|ờm|thế|vậy|kiểu(?:\s+như)?|ý là|thế còn|vậy còn"
        r"|cho\s+(?:anh|chị|em|tôi|mình)\s+hỏi(?:\s+(?:chút|tí|xíu))?|cho\s+hỏi|xin\s+hỏi"
        r"|dạ|vâng|ok(?:ay)?|ừ|ừa"
        r")\s*[,.]?\s*",
        re.IGNORECASE,
    )

    def _expand_followup(self, text: str, session: Session) -> str:
        """Prepend the last customer question when the current one is a follow-up.

        Short pronouny follow-ups ("còn Classic?", "phí bao nhiêu?") have almost
        no retrieval signal on their own. Prepending the previous exchange gives
        the TF-IDF index the topic words it needs without altering what the model
        eventually answers.
        """
        if not self._FOLLOWUP_RE.match(text):
            return text

        prev = [
            m.text
            for m in session.messages[-8:-1]
            if m.role == "customer" and len(m.text.split()) >= 3
        ]
        if not prev:
            return text

        return f"{guardrails.redact(prev[-1])} {text}"

    def _retrieval_retries(self, text: str, session: Session) -> list[tuple[str, str]]:
        """Các truy vấn thử lại khi retrieval lần 1 rỗng. Chỉ đổi câu đi TÌM, không
        đổi câu hỏi gửi cho model.

        Chỉ LƯỢC BỎ nhiễu (cụm đệm, lấy mệnh đề sau dấu phẩy) - không bao giờ THÊM
        từ khoá chủ đề khác vào. Thêm ngữ cảnh lượt trước từng được thử nhưng nó
        "cứu" nhầm câu ngoài phạm vi (câu hỏi bảo hiểm cây trồng bị ghép với chủ
        đề 'vay mua nhà' ở lượt trước rồi trúng tài liệu vay) - phá escalation.
        Lược bỏ thì không thể làm một câu đáng-rỗng trở nên khớp."""
        cleaned = text
        for _ in range(3):
            stripped = self._LEAD_FILLER_RE.sub("", cleaned, count=1).strip()
            if stripped == cleaned:
                break
            cleaned = stripped

        retries: list[tuple[str, str]] = []
        if cleaned and cleaned != text:
            retries.append((cleaned, "bỏ cụm đệm"))
        if "," in text:
            tail = text.rsplit(",", 1)[-1].strip()
            if tail and tail not in (cleaned, text):
                retries.append((tail, "mệnh đề sau dấu phẩy"))
        return retries

    def agent_reply(self, session: Session, text: str, staff) -> None:
        """Record a human agent's reply to the customer.

        Writes an audit row naming the staff member. That row is the point: up
        to here the trail explains how the *assistant* decided, and once people
        are replying, "who said this to the customer" is the question a
        reviewer will actually be asking.
        """
        session.handled_by = staff.display_name
        session.add_message("agent", text, author=staff.display_name)
        session.record(
            utterance=text,
            route="agent",
            intent="agent_reply",
            confidence=1.0,
            actor=staff.username,
            note=f"reply sent by {staff.username} ({staff.role})",
        )

    def _answer_from_kb(self, session: Session, text: str, prediction) -> TurnResult:
        cross_sell = playbook.is_cross_sell_context(prediction.intent)

        safe_text = guardrails.redact(text)
        if safe_text != text:
            self.trace.add(
                "privacy",
                "sensitive data masked",
                "redacted before retrieval and before the model",
            )

        retrieval_text = self._expand_followup(safe_text, session)
        if retrieval_text != safe_text:
            self.trace.add(
                "query_expansion",
                f"'{safe_text}' → '{retrieval_text}'",
                "short follow-up expanded with recent context",
            )

        fixed = _fix_card_typos(retrieval_text)
        if fixed != retrieval_text:
            self.trace.add(
                "typo_fix", f"'{retrieval_text}' → '{fixed}'", "sửa tên thẻ gõ sai"
            )
            retrieval_text = fixed

        pitched_card = session.slots.pop("xsell_card", None)
        if pitched_card:
            card_name = flows.product_name(pitched_card)
            if card_name:
                retrieval_text = card_name
                self.trace.add(
                    "cross_sell_context",
                    f"bơm thẻ đã chào: {card_name}",
                    "cuộc gọi cross-sell -> truy xuất tài liệu thẻ cho LLM tư vấn",
                )

        passages, rerank_note = self._retrieve(retrieval_text)
        self.trace.add(
            "retrieval",
            (
                f"{len(passages)} passage(s) above the floor"
                if passages
                else "nothing cleared the relevance floor"
            ),
            "; ".join(f"{p.passage.citation} (cov {p.score:.2f})" for p in passages[:3])
            or f"floor {MIN_RELEVANCE}",
        )

        if not passages:
            for retry_q, why in self._retrieval_retries(safe_text, session):
                if retry_q == retrieval_text:
                    continue
                retried, retried_note = self._retrieve(retry_q)
                if retried:
                    passages, rerank_note = retried, retried_note
                    self.trace.add(
                        "query_cleanup",
                        f"'{safe_text}' → '{retry_q}'",
                        f"truy xuất lại sau khi lần 1 rỗng ({why})",
                    )
                    break

        if not passages:
            reason = "No supporting knowledge-base passage found for the question"
            if rerank_note.startswith("rerank:rejected-all"):
                reason = "Reranker judged no retrieved passage relevant"
            self.trace.decide(
                "retrieval",
                "no verified source",
                "offering a human rather than answering",
            )

            if policy.current.general_fallback:
                gen_history = [
                    {
                        "role": "user" if m.role == "customer" else "assistant",
                        "content": guardrails.redact(m.text),
                    }
                    for m in session.messages[-10:]
                    if m.role in ("customer", "assistant")
                ]
                if gen_history and gen_history[-1]["role"] == "user":
                    gen_history = gen_history[:-1]
                gen = llm.answer_general(
                    safe_text,
                    history_messages=gen_history or None,
                    cross_sell=cross_sell,
                )
                if gen.text.strip() and not gen.refused:
                    self.trace.decide(
                        "generation",
                        "general-knowledge answer (no KB source)",
                        "answered from model general knowledge; persona + safety rails kept",
                    )
                    session.low_confidence_streak = 0
                    return TurnResult(
                        text=gen.text,
                        route="llm_general",
                        intent=prediction.intent,
                        confidence=prediction.confidence,
                        generated=gen.generated,
                        debug={"note": "general_fallback (no KB match)"},
                    )

            session.low_confidence_streak += 1
            if session.low_confidence_streak >= MAX_LOW_CONFIDENCE_STREAK:
                reason = "Assistant could not resolve two consecutive " "questions"
            return self._offer_escalation(
                session,
                reason,
                confidence=prediction.confidence,
            )

        history_msgs = [
            {
                "role": "user" if m.role == "customer" else "assistant",
                "content": guardrails.redact(m.text),
            }
            for m in session.messages
            if m.role in ("customer", "assistant")
        ]
        if history_msgs and history_msgs[-1]["role"] == "user":
            history_msgs = history_msgs[:-1]
        history_msgs = history_msgs[-10:]

        result = llm.answer_from_kb(
            safe_text,
            passages,
            history_messages=history_msgs or None,
            cross_sell=cross_sell,
        )

        if not result.text.strip():
            return self._escalate(
                session,
                "Generative layer returned no usable answer",
                intent=prediction.intent,
                confidence=prediction.confidence,
            )

        if result.refused:
            return self._offer_escalation(
                session,
                f"Provider safety system declined the request ({result.provider})",
                confidence=prediction.confidence,
            )

        context = " ".join(p.passage.text for p in passages)
        grounding = round(guardrails.grounding_score(result.text, context), 3)

        market_only = bool(passages) and all(
            p.passage.doc_id.startswith(MARKET_DOC_PREFIX) for p in passages
        )
        floor = MARKET_MIN_GROUNDING if market_only else policy.current.min_grounding

        self.trace.add(
            "generation",
            (
                "answer written by the model"
                if result.generated
                else "extractive fallback (no model)"
            ),
            f"grounding {grounding:.2f} against a {floor} floor"
            + (" (tài liệu thị trường - ngưỡng nới)" if market_only else ""),
        )

        if result.generated and grounding < floor:
            self.trace.decide(
                "generation",
                "failed the grounding check",
                "answer discarded; offering a human",
            )
            return self._offer_escalation(
                session,
                f"Answer failed the grounding check ({grounding:.2f} < {floor})",
                confidence=prediction.confidence,
            )

        if market_only:
            result.text = _ensure_market_disclaimer(result.text)

        session.low_confidence_streak = 0

        self.trace.decide(
            "generation", "grounded answer served", f"cited {len(passages)} passage(s)"
        )
        return TurnResult(
            text=result.text,
            route="rag",
            intent=prediction.intent,
            confidence=prediction.confidence,
            sources=[
                {
                    "citation": p.passage.citation,
                    "title": p.passage.title,
                    "heading": p.passage.heading,
                    "source": p.passage.source,
                    "score": p.score,
                    "bm25": p.bm25,
                    "fusion": p.fusion,
                    "rerank": p.rerank,
                    "breadcrumb": p.passage.breadcrumb,
                }
                for p in passages
            ],
            generated=result.generated,
            grounding=grounding,
            debug={
                "note": " · ".join(
                    filter(
                        None,
                        [
                            rerank_note,
                            result.error
                            or ("llm" if result.generated else "extractive"),
                        ],
                    )
                ),
                "scores": prediction.scores,
                "tokens": {"in": result.input_tokens, "out": result.output_tokens},
            },
        )

    def _offer_escalation(
        self,
        session: Session,
        reason: str,
        intent: str = "escalation_offered",
        confidence: float = 0.0,
        prefix: str = "",
    ) -> TurnResult:
        """Ask before handing off, rather than handing off and announcing it.

        The customer, not the assistant, should decide whether their time is
        better spent in a queue. Plenty of people would rather rephrase the
        question than wait - and a bot that escalates unilaterally the first
        time it is stuck feels like it gave up on them.

        Handoffs the customer already asked for, and security failures, are
        exempt: there is nothing to consult them about in either case.
        """
        session.pending_escalation = reason
        body = R.ESCALATION_OFFER_BODY
        message = f"{prefix.strip()}\n\n{body}" if prefix.strip() else body
        return TurnResult(
            text=message,
            route="escalation_offered",
            intent=intent,
            confidence=confidence,
            escalation_reason=reason,
            debug={"note": f"offered handoff: {reason}"},
        )

    def _escalate(
        self,
        session: Session,
        reason: str | None,
        intent: str = "escalation",
        confidence: float = 0.0,
        prefix: str = "",
        grounding: float | None = None,
    ) -> TurnResult:
        reason = reason or "Assistant could not resolve the request"

        already_queued = session.escalated
        session.escalated = True
        session.escalation_reason = reason
        session.reset_flow()

        session.escalation_summary = self.build_summary(session).text

        announcement = REQUEUED_MESSAGE if already_queued else ESCALATION_MESSAGE
        message = (
            f"{prefix.strip()}\n\n{announcement}" if prefix.strip() else announcement
        )
        return TurnResult(
            text=message,
            route="escalation",
            intent=intent,
            confidence=confidence,
            escalated=True,
            escalation_reason=reason,
            grounding=grounding,
            debug={"note": f"escalated: {reason}"},
        )

    def build_summary(self, session: Session):
        customer = session.customer
        context_lines = [
            f"Session ID: {session.session_id}",
            f"Identity verified: {'yes' if session.verified else 'no'}",
            (
                f"Customer: {customer['name']} ({customer['customer_id']})"
                if customer
                else "Customer: not identified"
            ),
            f"Escalation trigger: {session.escalation_reason or 'n/a'}",
            f"Turns handled by the assistant: "
            f"{sum(1 for m in session.messages if m.role == 'customer')}",
        ]
        return llm.summarize_for_agent(session.transcript(), context_lines)
