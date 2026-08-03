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

import time
from dataclasses import dataclass, field

from . import flows, guardrails, llm
from .nlu import IntentClassifier
from .retriever import KnowledgeBase
from .session import Session, SessionStore

# Two consecutive turns we couldn't confidently handle means the assistant is
# not converging - hand off rather than let the customer keep rephrasing.
MAX_LOW_CONFIDENCE_STREAK = 2

ESCALATION_MESSAGE = (
    "Let me bring in one of our specialists - they'll have the full context of "
    "this conversation, so you won't need to repeat anything.\n\n"
    "**You're now in the queue for a human agent.**"
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
    debug: dict = field(default_factory=dict)


class Router:
    def __init__(self) -> None:
        self.classifier = IntentClassifier()
        self.kb = KnowledgeBase()
        self.sessions = SessionStore()

    # -- public API -------------------------------------------------------

    def handle_turn(self, session: Session, text: str) -> TurnResult:
        started = time.perf_counter()
        session.add_message("customer", text)

        result = self._route(session, text)

        result.latency_ms = int((time.perf_counter() - started) * 1000)
        session.add_message("assistant", result.text)
        session.record(
            utterance=text,
            route=result.route,
            intent=result.intent,
            confidence=result.confidence,
            sources=[s["citation"] for s in result.sources],
            grounding=result.grounding,
            generated=result.generated,
            latency_ms=result.latency_ms,
            note=result.debug.get("note", ""),
        )
        return result

    # -- routing ----------------------------------------------------------

    def _route(self, session: Session, text: str) -> TurnResult:
        # 1. A flow already in progress owns the turn. Slot answers ("4471",
        #    "yes") have no intent signal, so classifying them would misroute.
        pending = flows.continue_pending(session, text)
        if pending is not None:
            if pending.escalate:
                return self._escalate(session, pending.escalation_reason,
                                      prefix=pending.text)
            return TurnResult(
                text=pending.text, route="deterministic",
                intent=f"flow:{session.pending_flow or 'resumed'}",
                confidence=1.0, debug={"note": pending.note},
            )

        # 2. Compliance gate - restricted topics never reach a model.
        verdict = guardrails.check_input(text)
        if not verdict.allowed:
            session.low_confidence_streak = 0
            return TurnResult(
                text=guardrails.RESTRICTED_RESPONSE,
                route="guardrail", intent=verdict.topic or "restricted",
                confidence=1.0,
                debug={"note": f"blocked:{verdict.topic} - {verdict.reason}"},
            )

        # 3. NLU. High confidence + a scripted flow = deterministic answer.
        prediction = self.classifier.predict(text)

        if prediction.is_high_confidence and prediction.intent != "knowledge_query":
            flow = flows.handle(session, prediction.intent, text)
            if flow.handled:
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

        # 4. Everything else: retrieve, then generate strictly over what we found.
        return self._answer_from_kb(session, text, prediction)

    def _answer_from_kb(self, session: Session, text: str, prediction) -> TurnResult:
        passages = self.kb.search(text, top_k=3)

        if not passages:
            # No verified source covers this. Guessing here is exactly the
            # hallucination risk the architecture exists to remove.
            return self._escalate(
                session,
                "No supporting knowledge-base passage found for the question",
                intent=prediction.intent, confidence=prediction.confidence,
            )

        history = session.transcript(limit=6)
        result = llm.answer_from_kb(text, passages, history=history)

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
            return self._escalate(
                session,
                f"Provider safety system declined the request ({result.provider})",
                intent=prediction.intent, confidence=prediction.confidence,
            )

        # 5. Grounding check on the way out: if the answer drifted off the
        #    retrieved text, we escalate rather than ship it.
        context = " ".join(p.passage.text for p in passages)
        grounding = round(guardrails.grounding_score(result.text, context), 3)

        if result.generated and grounding < guardrails.MIN_GROUNDING:
            return self._escalate(
                session,
                f"Answer failed the grounding check ({grounding:.2f} < "
                f"{guardrails.MIN_GROUNDING})",
                intent=prediction.intent, confidence=prediction.confidence,
                grounding=grounding,
            )

        # Low classifier confidence twice running means we're not converging.
        if prediction.is_unknown:
            session.low_confidence_streak += 1
            if session.low_confidence_streak >= MAX_LOW_CONFIDENCE_STREAK:
                return self._escalate(
                    session,
                    "Intent confidence stayed below threshold across consecutive turns",
                    intent=prediction.intent, confidence=prediction.confidence,
                    prefix=result.text,
                )
        else:
            session.low_confidence_streak = 0

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
            } for p in passages],
            generated=result.generated,
            grounding=grounding,
            debug={
                "note": result.error or ("llm" if result.generated else "extractive"),
                "scores": prediction.scores,
                "tokens": {"in": result.input_tokens, "out": result.output_tokens},
            },
        )

    # -- escalation -------------------------------------------------------

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
        session.escalated = True
        session.escalation_reason = reason
        session.reset_flow()

        # Build the handover brief now, while the conversation is fresh, so the
        # agent picking it up has it the moment they open the queue.
        session.escalation_summary = self.build_summary(session).text

        message = (f"{prefix.strip()}\n\n{ESCALATION_MESSAGE}" if prefix.strip()
                   else ESCALATION_MESSAGE)
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
