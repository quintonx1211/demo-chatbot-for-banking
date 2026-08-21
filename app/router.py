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

import re
import time
from dataclasses import dataclass, field

from . import flows, guardrails, llm, policy
from .llm import rerank, route
from .nlu import (HIGH_CONFIDENCE, LOW_CONFIDENCE, IntentClassifier,
                  IntentPrediction)
from .retriever import MIN_RELEVANCE, KnowledgeBase
from .trace import Trace
from . import memory as memory_mod
from .textmodel import tokenize
from .session import Session, SessionStore

# Two consecutive turns we couldn't confidently handle means the assistant is
# not converging - hand off rather than let the customer keep rephrasing.
MAX_LOW_CONFIDENCE_STREAK = 2

# Addressing. "@agent hello" reaches a human; "@bot ..." pulls the assistant
# back in while a human has the conversation. Anything else is routed normally.
_MENTION_RE = re.compile(r"^\s*@(agent|human|staff|bot|assistant|ai)(?=[\s:,]|$)[:,]?\s*",
                         re.IGNORECASE)
_AGENT_MENTIONS = {"agent", "human", "staff"}

# Leaving a conversation a human has taken over.
_LEAVE_RE = re.compile(r"^\s*(/leave|/bot|/back|end chat|leave chat)\s*$",
                       re.IGNORECASE)

# Accepting or declining the offer of a human agent, in both languages.
# "không" is checked before "có" by the caller, because "không" is the more
# specific answer and a decline misread as an accept puts a customer in a
# queue they just said they did not want.
_AFFIRM_RE = re.compile(
    r"^\s*(yes|yeah|yep|yup|ok(ay)?|sure|please|go ahead|do it|connect me|"
    r"put me through|transfer me|i do|"
    r"có|được|đồng ý|kết nối|vâng|ừ|oke|ok)(?=[\s.!,]|$)", re.IGNORECASE)
_DECLINE_RE = re.compile(
    r"^\s*(no|nope|not now|no thanks|nah|don'?t|cancel|"
    r"không|thôi|không cần|không muốn|tiếp tục)(?=[\s.!,]|$)", re.IGNORECASE)


def split_mention(text: str) -> tuple[str | None, str]:
    """Split a leading @mention off a message. Returns (target, remainder)."""
    match = _MENTION_RE.match(text or "")
    if not match:
        return None, text
    target = match.group(1).lower()
    who = "agent" if target in _AGENT_MENTIONS else "bot"
    return who, text[match.end():].strip()

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


ESCALATION_MESSAGE = (
    "Để tôi kết nối bạn với một chuyên viên - họ sẽ có đầy đủ nội dung cuộc hội thoại này, "
    "bạn không cần phải giải thích lại.\n\n"
    "**Bạn đang trong hàng chờ nhân viên hỗ trợ.**"
)

REQUEUED_MESSAGE = (
    "Câu hỏi này cũng nằm ngoài phạm vi tôi có thể trả lời - tôi đã ghi vào ghi chú "
    "cho chuyên viên tiếp nhận bạn. **Bạn vẫn đang trong hàng chờ.**"
)


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
    offer_escalation: bool = False   # UI should show the yes/no handoff prompt
    with_agent: bool = False         # a human currently owns this conversation
    trace: list = field(default_factory=list)   # why this route was taken
    debug: dict = field(default_factory=dict)


class Router:
    def __init__(self) -> None:
        self.classifier = IntentClassifier()
        self.kb = KnowledgeBase()
        self.sessions = SessionStore()

    # -- public API -------------------------------------------------------

    def handle_turn(self, session: Session, text: str) -> TurnResult:
        started = time.perf_counter()
        self.trace = Trace()
        session.add_message("customer", text)

        # Demo lever, checked before anything else the router does. Everyone
        # below this line - guardrails, NLU, flows, retrieval, grounding -
        # exists to improve on what a bare LLM call over the transcript would
        # do; this path is that call, unmodified, so the difference can be
        # shown rather than described.
        if session.raw_mode:
            result = self._raw_turn(session, text)
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            result.trace = self.trace.to_list()
            session.add_message("assistant", result.text)
            session.record(
                utterance=text, route=result.route, intent=result.intent,
                confidence=result.confidence, generated=result.generated,
                latency_ms=result.latency_ms,
                note="raw mode: no routing, guardrails, retrieval or grounding check",
            )
            return result

        mention, body = split_mention(text)

        # Leaving a conversation that went to a human. Available from the
        # moment the handoff happens, not just once an agent has claimed it:
        # the customer who changes their mind while third in the queue is
        # exactly the one most in need of a way out.
        if (session.handled_by or session.escalated) and _LEAVE_RE.match(text):
            agent_name = session.handled_by
            session.handled_by = None
            session.escalated = False
            session.escalation_reason = None
            session.pending_escalation = None
            session.record(
                utterance=text, route="deterministic", intent="left_agent",
                confidence=1.0,
                note=(f"customer left the chat with {agent_name}" if agent_name
                      else "customer left the handoff queue before it was claimed"),
            )
            reply = ((f"Bạn đã quay lại với trợ lý - {agent_name} đã kết thúc cuộc hội thoại này. "
                      "Tôi có thể giúp gì cho bạn?")
                     if agent_name else
                     ("Bạn đã quay lại với trợ lý, tôi đã đưa bạn ra khỏi hàng chờ. "
                      "Tôi có thể giúp gì cho bạn?"))
            session.add_message("assistant", reply)
            return TurnResult(
                text=reply, route="deterministic", intent="left_agent",
                confidence=1.0,
                latency_ms=int((time.perf_counter() - started) * 1000),
                debug={"note": "customer ended the agent conversation"},
            )

        # "/leave" with no agent attached is a no-op, not a question. Routing
        # it as one offered the customer a handoff, which is the opposite of
        # what they asked for.
        if _LEAVE_RE.match(text):
            reply = "Bạn đang trò chuyện với trợ lý ảo. Tôi có thể giúp gì cho bạn?"
            session.add_message("assistant", reply)
            session.record(utterance=text, route="deterministic",
                           intent="left_agent", confidence=1.0,
                           note="/leave with no agent attached")
            return TurnResult(
                text=reply, route="deterministic", intent="left_agent",
                confidence=1.0,
                latency_ms=int((time.perf_counter() - started) * 1000),
                debug={"note": "no agent was attached"},
            )

        # Once the conversation has gone to a human the assistant stands down.
        #
        # The trigger is the handoff, not the agent picking it up. Gating this
        # on `handled_by` alone left a window - often minutes - where the
        # customer had been told a person was coming and the bot kept answering
        # anyway. That window is the worst possible moment to keep talking: the
        # customer is there *because* the assistant already failed, and every
        # further attempt undermines the handoff it just promised. Worse, the
        # agent arrives to a transcript where the bot has been arguing with
        # their customer on their behalf.
        #
        # "@bot ..." is the one exemption: a deliberate request for the
        # assistant is not talking over the agent.
        if (session.handled_by or session.escalated) and mention != "bot":
            waiting_for = session.handled_by or "the next available agent"
            session.record(
                utterance=text, route="agent", intent="awaiting_agent",
                confidence=1.0, note=f"queued for {waiting_for}",
            )
            return TurnResult(
                text="", route="agent", intent="awaiting_agent", confidence=1.0,
                with_agent=True, escalated=session.escalated,
                latency_ms=int((time.perf_counter() - started) * 1000),
                debug={"note": f"assistant stood down; queued for {waiting_for}"},
            )

        # "@agent ..." asks for a person explicitly - no need to consult the
        # customer about whether they want one.
        if mention == "agent" and not session.handled_by:
            result = self._escalate(
                session, "Customer addressed a human agent directly",
                intent="human_agent", confidence=1.0,
            )
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            session.add_message("assistant", result.text)
            session.record(
                utterance=text, route=result.route, intent=result.intent,
                confidence=1.0, latency_ms=result.latency_ms,
                note=result.debug.get("note", ""),
            )
            return result

        result = self._route(session, body if mention else text)

        result.latency_ms = int((time.perf_counter() - started) * 1000)
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
            note=" · ".join(filter(None, [self.trace.summary,
                                          result.debug.get("note", "")])),
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
            "raw_mode", "architecture disabled for this conversation",
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
            memory_mod.store.remember(session.customer_id, "outcome", "unresolved",
                                      detail=topic or "chủ đề chưa xác định")
        elif result.route == "rag" and result.sources:
            memory_mod.store.remember(session.customer_id, "topic",
                                      result.sources[0]["heading"].lower(),
                                      detail=result.sources[0]["citation"])
        elif result.route == "deterministic" and result.intent in (
                "card_offers", "activate_card", "cross_sell_interest", "reward_inquiry"):
            memory_mod.store.remember(session.customer_id, "campaign", result.intent)

    # -- routing ----------------------------------------------------------

    def _route(self, session: Session, text: str) -> TurnResult:
        # 0. An outstanding offer of a handoff. Answered first, because "yes"
        #    means nothing to the classifier and would otherwise be routed as
        #    an unknown utterance - the same trap the flow escape hatch fixes.
        if session.pending_escalation:
            reason = session.pending_escalation
            session.pending_escalation = None
            self.trace.add("handoff_offer", "an offer was outstanding",
                           f"offered because: {reason}")

            if _AFFIRM_RE.match(text):
                self.trace.decide("handoff_offer", "customer accepted",
                                  "handing off to a human")
                return self._escalate(session, reason, intent="escalation_accepted",
                                      confidence=1.0)
            if _DECLINE_RE.match(text):
                self.trace.decide("handoff_offer", "customer declined",
                                  "assistant continues")
                return TurnResult(
                    text="Được rồi, tôi sẽ tiếp tục hỗ trợ bạn. Bạn cần hỏi thêm điều gì không?",
                    route="deterministic", intent="escalation_declined",
                    confidence=1.0, debug={"note": "customer declined the handoff"},
                )
            # Anything else is a new question. Dropping the offer and answering
            # it is the right reading: they moved on.

        # 1. A flow already in progress usually owns the turn, because slot
        #    answers ("9411 3147", "yes") carry no intent signal and classifying
        #    them would misroute. But "usually" needs an escape hatch: without
        #    one the customer is trapped repeating themselves at a prompt they
        #    are not trying to answer, which is precisely the rule-based
        #    failure this architecture is supposed to remove.
        if session.pending_flow:
            abandon = self._should_abandon_flow(session, text)
            if abandon:
                flow_name = session.pending_flow
                session.reset_flow()
                session.flow_misses = 0
                # Fall through to normal routing, and prefix the answer so the
                # customer knows the earlier request was dropped rather than
                # silently forgotten.
                result = self._route_fresh(session, text)
                result.debug["note"] = " · ".join(
                    filter(None, [f"abandoned:{flow_name}({abandon})",
                                  result.debug.get("note", "")])
                )
                return result

        pending = flows.continue_pending(session, text)
        if pending is not None:
            # Track re-prompts: a flow that keeps asking the same question is
            # not making progress, and two of those is enough to conclude the
            # customer has moved on.
            if pending.note.endswith("_retry"):
                session.flow_misses += 1
            else:
                session.flow_misses = 0

            if pending.escalate:
                return self._escalate(session, pending.escalation_reason,
                                      prefix=pending.text)
            return TurnResult(
                text=pending.text, route="deterministic",
                intent=f"flow:{session.pending_flow or 'resumed'}",
                confidence=1.0, debug={"note": pending.note},
            )

        return self._route_fresh(session, text)

    def _apply_llm_router(self, session: Session, text: str,
                          lexical: IntentPrediction) -> IntentPrediction:
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

        # Redacted before the message reaches any provider, exactly as on the
        # retrieval path. A routing call is still a call.
        safe_text = guardrails.redact(text)
        history = guardrails.redact(session.transcript(limit=4))
        verdict = route.classify(safe_text, self.classifier.intents, history)

        if verdict is None:
            # No provider configured. Falling back silently is right - the
            # customer should not learn our model configuration from a
            # degraded reply - but the trace records it for the operator.
            self.trace.add("router", "lexical (không có provider cho LLM routing)",
                           f"mode={mode}")
            return lexical

        if verdict.get("error"):
            self.trace.add("router",
                           f"lexical (LLM routing lỗi: {verdict['error']})",
                           f"mode={mode}")
            return lexical

        agreed = verdict["intent"] == lexical.intent
        session.router_comparisons += 1
        if agreed:
            session.router_agreements += 1
        else:
            session.router_disagreements.append({
                "text": safe_text[:80],
                "lexical": lexical.intent,
                "lexical_confidence": round(lexical.confidence, 2),
                "llm": verdict["intent"],
                "llm_confidence": round(verdict["confidence"], 2),
                "why": verdict.get("why", ""),
                "used": "llm" if mode == "llm" else "lexical",
            })

        self.trace.add(
            "router",
            f"LLM chọn {verdict['intent']} @ {verdict['confidence']:.2f}"
            + ("" if agreed else f" (lexical: {lexical.intent})"),
            f"mode={mode}; " + ("đang dùng" if mode == "llm" else "chỉ ghi nhận"))

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

        # Verification flow: only keep it if the input looks like a
        # verification code (a 4-digit group). No digits means this is
        # clearly a new question, so drop verify and re-route immediately.
        if session.pending_flow == "verify":
            if not re.search(r"\b\d{4}\b", text):
                return "new-question-during-verify"
            return None

        # Card action flows ("freeze", "unfreeze", "report_lost"): "có"/"không"
        # và tên thẻ đều là slot answers ngắn, NLU không phân biệt được với
        # intent thật. Để state machine của card tự xử lý retry qua flow_misses.
        if session.pending_flow in flows.CARD_ACTIONS:
            return None

        if session.flow_misses >= flows.MAX_FLOW_MISSES:
            return f"no-progress-after-{session.flow_misses}"

        # LOW_CONFIDENCE, not HIGH: the bar for *leaving* a flow should be far
        # lower than the bar for entering one. Slot answers ("2205", "yes",
        # "debit") classify as unknown or as the current flow's own intent, so
        # they are safe from this; anything that looks like a different request
        # at all is better served by dropping the flow than by re-prompting.
        prediction = self.classifier.predict(text)
        if (prediction.confidence >= LOW_CONFIDENCE
                and prediction.intent not in ("unknown", session.pending_flow)):
            return f"new-intent:{prediction.intent}@{prediction.confidence:.2f}"
        return None

    def _route_fresh(self, session: Session, text: str) -> TurnResult:

        # Compliance gate - restricted topics never reach a model, including
        # the routing model added below.
        verdict = guardrails.check_input(text)
        if not verdict.allowed:
            session.low_confidence_streak = 0
            self.trace.decide("guardrail", f"blocked: {verdict.topic}",
                              f"{verdict.reason} - no model was called")
            return TurnResult(
                text=guardrails.RESTRICTED_RESPONSE,
                route="guardrail", intent=verdict.topic or "restricted",
                confidence=1.0,
                debug={"note": f"blocked:{verdict.topic} - {verdict.reason}"},
            )

        # NLU. High confidence + a scripted flow = deterministic answer.
        #
        # This runs strictly after the guardrail above, in every router mode.
        # Putting a model in front of the compliance gate would demote the gate
        # from a structural block to a prompt instruction, and the structural
        # block is what the product is sold on.
        prediction = self.classifier.predict(text)
        prediction = self._apply_llm_router(session, text, prediction)
        self.trace.add(
            "nlu", f"intent {prediction.intent} @ {prediction.confidence:.2f}",
            f"threshold {HIGH_CONFIDENCE} for a scripted flow"
            + (f"; runner-up {prediction.runner_up} @ "
               f"{prediction.runner_up_confidence:.2f}" if prediction.runner_up else ""))

        if prediction.is_high_confidence and prediction.intent not in ("knowledge_query", "product_faq"):
            flow = flows.handle(session, prediction.intent, text)
            if flow.handled:
                self.trace.decide(
                    "nlu", "above threshold - scripted flow",
                    "answer assembled from the customer record; no model involved")
                session.low_confidence_streak = 0
                if flow.escalate:
                    return self._escalate(session, flow.escalation_reason,
                                          intent=prediction.intent,
                                          confidence=prediction.confidence,
                                          prefix=flow.text)
                return TurnResult(
                    text=flow.text, route="deterministic",
                    intent=prediction.intent, confidence=prediction.confidence,
                    debug={"note": flow.note, "scores": prediction.scores},
                )

        # Everything else: retrieve, then generate strictly over what we found.
        return self._answer_from_kb(session, text, prediction)

    def _retrieve(self, text: str) -> tuple[list, str]:
        """Retrieve passages, optionally through the LLM reranking stage."""
        if not rerank.enabled():
            return self.kb.search(text, top_k=3), ""

    # Vietnamese patterns that signal a follow-up question with no standalone
    # retrieval signal: "còn", "thế còn", "cái đó", etc.
    _FOLLOWUP_RE = re.compile(
        r"^(còn\b|thế còn\b|vậy còn\b|còn về\b|còn thẻ\b|còn loại\b|còn cái\b|"
        r"thì sao\b|thế thì\b|vậy thì\b|sao ạ\b|"
        r"cái đó|thẻ đó|loại đó|nó |của nó|"
        r"phí bao nhiêu|bao nhiêu|như thế nào|what about\b|how about\b)",
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

        # Find the most recent substantive customer message (not the current one)
        prev = [
            m.text for m in session.messages[-8:-1]
            if m.role == "customer" and len(m.text.split()) >= 3
        ]
        if not prev:
            return text

        return f"{guardrails.redact(prev[-1])} {text}"

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
            utterance=text, route="agent", intent="agent_reply",
            confidence=1.0, actor=staff.username,
            note=f"reply sent by {staff.username} ({staff.role})",
        )

        # With a semantic judge downstream, stage one runs for recall: a looser
        # lexical gate and a wider pool, because the judge - not the threshold -
        # is what decides relevance on this path.
        candidates = self.kb.search(
            text, top_k=rerank.CANDIDATE_POOL, gate=rerank.RECALL_GATE
        )
        return rerank.rerank(text, candidates, top_k=3)

    def _answer_from_kb(self, session: Session, text: str, prediction) -> TurnResult:
        # Redact before retrieval and before the model sees anything. Two
        # separate reasons, both load-bearing:
        #
        #   Privacy - the raw text is sent to a third-party LLM provider. A
        #   customer who pastes a card number was having it forwarded verbatim
        #   to Groq. Masking it in the audit log afterwards does not unsend it.
        #
        #   Retrieval - "my card 4111 1111 1111 1111 was charged twice" tokenises
        #   the digits, and those tokens appear in no passage, so the coverage
        #   score collapses and a legitimate dispute question retrieves nothing.
        safe_text = guardrails.redact(text)
        if safe_text != text:
            self.trace.add("privacy", "sensitive data masked",
                           "redacted before retrieval and before the model")

        # Expand short follow-up questions before retrieval so the TF-IDF index
        # gets enough signal. "còn Classic?" retrieves nothing without context;
        # "phí thường niên thẻ Classic" retrieves correctly.
        retrieval_text = self._expand_followup(safe_text, session)
        if retrieval_text != safe_text:
            self.trace.add("query_expansion", f"'{safe_text}' → '{retrieval_text}'",
                           "short follow-up expanded with recent context")

        passages, rerank_note = self._retrieve(retrieval_text)
        self.trace.add(
            "retrieval",
            f"{len(passages)} passage(s) above the floor" if passages
            else "nothing cleared the relevance floor",
            "; ".join(f"{p.passage.citation} (cov {p.score:.2f})" for p in passages[:3])
            or f"floor {MIN_RELEVANCE}")

        if not passages:
            # No verified source covers this. Guessing here is exactly the
            # hallucination risk the architecture exists to remove.
            reason = "No supporting knowledge-base passage found for the question"
            if rerank_note.startswith("rerank:rejected-all"):
                # A different, stronger statement than "nothing matched the
                # words": a model read the candidates and judged none of them
                # to answer the question.
                reason = "Reranker judged no retrieved passage relevant"
            self.trace.decide("retrieval", "no verified source",
                              "offering a human rather than answering")
            # Counted here, where the assistant genuinely failed to resolve the
            # turn, rather than on low classifier confidence. Two unanswerable
            # questions running is the signal the rule was always meant to
            # catch; an unsure classifier on a question that then got answered
            # is not.
            session.low_confidence_streak += 1
            if session.low_confidence_streak >= MAX_LOW_CONFIDENCE_STREAK:
                reason = ("Assistant could not resolve two consecutive "
                          "questions")
            return self._offer_escalation(
                session, reason, confidence=prediction.confidence,
            )

        # Build structured conversation history (10 messages = 5 exchanges).
        # Exclude the current customer question — it becomes the final user turn
        # inside build_answer_request, paired with the retrieved KB passages.
        history_msgs = [
            {"role": "user" if m.role == "customer" else "assistant",
             "content": guardrails.redact(m.text)}
            for m in session.messages
            if m.role in ("customer", "assistant")
        ]
        # Drop the last entry: that is the current question already in safe_text.
        if history_msgs and history_msgs[-1]["role"] == "user":
            history_msgs = history_msgs[:-1]
        history_msgs = history_msgs[-10:]     # keep last 5 exchanges

        result = llm.answer_from_kb(
            safe_text, passages,
            history_messages=history_msgs or None,
        )

        if not result.text.strip():
            return self._escalate(
                session, "Generative layer returned no usable answer",
                intent=prediction.intent, confidence=prediction.confidence,
            )

        # A provider safety system declining is a signal in its own right, not
        # something to paper over with the extractive fallback: the customer
        # asked something the vendor's classifiers object to, and a human
        # should see it.
        if result.refused:
            return self._offer_escalation(
                session,
                f"Provider safety system declined the request ({result.provider})",
                confidence=prediction.confidence,
            )

        # 5. Grounding check on the way out: if the answer drifted off the
        #    retrieved text, we escalate rather than ship it.
        context = " ".join(p.passage.text for p in passages)
        grounding = round(guardrails.grounding_score(result.text, context), 3)

        self.trace.add(
            "generation",
            "answer written by the model" if result.generated
            else "extractive fallback (no model)",
            f"grounding {grounding:.2f} against a {guardrails.MIN_GROUNDING} floor")

        if result.generated and grounding < policy.current.min_grounding:
            self.trace.decide("generation", "failed the grounding check",
                              "answer discarded; offering a human")
            return self._offer_escalation(
                session,
                f"Answer failed the grounding check ({grounding:.2f} < "
                f"{policy.current.min_grounding})",
                confidence=prediction.confidence,
            )

        # The low-confidence streak counts turns the assistant could not
        # RESOLVE, not turns the classifier was unsure about.
        #
        # It used to count `prediction.is_unknown`, which fires on almost every
        # knowledge question - a customer asking about fees has no scripted
        # intent, and that is the RAG layer working as designed. Two such
        # questions in a row therefore produced this, verbatim, in one bubble:
        #
        #     [the correct, cited answer about annual fees]
        #     "I don't have anything verified on that..."
        #
        # An answer immediately contradicted by a claim of ignorance is worse
        # than either message alone: the customer cannot tell which half to
        # believe, and the one thing this architecture sells is that its
        # answers are trustworthy. A turn that produced a grounded answer is
        # convergence, whatever the intent classifier thought.
        session.low_confidence_streak = 0

        self.trace.decide("generation", "grounded answer served",
                          f"cited {len(passages)} passage(s)")
        return TurnResult(
            text=result.text,
            route="rag",
            intent=prediction.intent,
            confidence=prediction.confidence,
            sources=[{
                "citation": p.passage.citation,
                "title": p.passage.title,
                "heading": p.passage.heading,
                "source": p.passage.source,
                "score": p.score,
                "bm25": p.bm25,
                "fusion": p.fusion,
                "rerank": p.rerank,
                "breadcrumb": p.passage.breadcrumb,
            } for p in passages],
            generated=result.generated,
            grounding=grounding,
            debug={
                "note": " · ".join(filter(None, [
                    rerank_note,
                    result.error or ("llm" if result.generated else "extractive"),
                ])),
                "scores": prediction.scores,
                "tokens": {"in": result.input_tokens, "out": result.output_tokens},
            },
        )

    # -- escalation -------------------------------------------------------

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
        body = (
            "Thành thật mà nói, tôi không có thông tin chính xác về vấn đề này.\n\n"
            "**Bạn có muốn tôi kết nối với một chuyên viên để được tư vấn thêm không?** "
            "Họ sẽ có đầy đủ nội dung cuộc hội thoại này, bạn không cần giải thích lại. "
            "Hoặc bạn có thể hỏi tôi câu khác."
        )
        message = f"{prefix.strip()}\n\n{body}" if prefix.strip() else body
        return TurnResult(
            text=message, route="escalation_offered", intent=intent,
            confidence=confidence, escalation_reason=reason,
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

        # Announcing the handoff once is reassuring; announcing it on every
        # subsequent turn reads as broken, because nothing visibly changed the
        # first three times it was said.
        already_queued = session.escalated
        session.escalated = True
        session.escalation_reason = reason
        session.reset_flow()

        # Build the handover brief now, while the conversation is fresh, so the
        # agent picking it up has it the moment they open the queue.
        session.escalation_summary = self.build_summary(session).text

        announcement = REQUEUED_MESSAGE if already_queued else ESCALATION_MESSAGE
        message = (f"{prefix.strip()}\n\n{announcement}" if prefix.strip()
                   else announcement)
        return TurnResult(
            text=message, route="escalation", intent=intent, confidence=confidence,
            escalated=True, escalation_reason=reason, grounding=grounding,
            debug={"note": f"escalated: {reason}"},
        )

    def build_summary(self, session: Session):
        customer = session.customer
        context_lines = [
            f"Session ID: {session.session_id}",
            f"Identity verified: {'yes' if session.verified else 'no'}",
            f"Customer: {customer['name']} ({customer['customer_id']})"
            if customer else "Customer: not identified",
            f"Escalation trigger: {session.escalation_reason or 'n/a'}",
            # Counted from the transcript, not the audit log: the brief is built
            # mid-turn, before the current turn's audit row has been written.
            f"Turns handled by the assistant: "
            f"{sum(1 for m in session.messages if m.role == 'customer')}",
        ]
        return llm.summarize_for_agent(session.transcript(), context_lines)
