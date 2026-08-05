"""Conversation state, verification state and the audit trail.

Everything a human agent needs to pick up an escalated conversation lives here:
the full transcript, which customer was verified and how, and a per-turn record
of how each answer was produced.
"""

from __future__ import annotations

import itertools
import secrets
import time
from dataclasses import asdict, dataclass, field
from threading import Lock

from . import db, guardrails

def find_customer(customer_id: str) -> dict | None:
    """Read a customer from the database, fresh, every time.

    Deliberately not cached. The previous version held one shared list in
    module state, so a card blocked in one conversation was blocked in every
    other one, and stayed blocked after "Clear sessions". Re-reading costs a
    few microseconds against a handful of local CSV rows and removes a class of
    bug where two sessions disagree about what a card's status is.
    """
    return db.get_customer(customer_id)


@dataclass
class Message:
    role: str            # "customer" | "assistant" | "agent" | "system"
    text: str
    timestamp: float = field(default_factory=time.time)
    author: str | None = None   # staff display name, on agent messages


@dataclass
class AuditEntry:
    """One row per turn. This is what a compliance reviewer reads."""
    turn: int
    utterance: str          # redacted
    route: str              # deterministic | rag | guardrail | escalation | agent
    intent: str
    confidence: float
    # Set when a human, not the assistant, produced this turn. Without it the
    # audit trail only explains what the bot decided, which stops being the
    # whole story the moment agents start replying.
    actor: str | None = None
    sources: list[str] = field(default_factory=list)
    grounding: float | None = None
    generated: bool = False
    latency_ms: int = 0
    note: str = ""
    timestamp: float = field(default_factory=time.time)


class Session:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.created_at = time.time()
        self.messages: list[Message] = []
        self.audit: list[AuditEntry] = []
        self.turn_counter = itertools.count(1)

        # Verification state - no account action runs without this.
        self.customer_id: str | None = None
        self.verified = False

        # Slot-filling state for whichever deterministic flow is mid-conversation.
        self.pending_flow: str | None = None
        self.slots: dict[str, str] = {}
        # Consecutive turns where the flow re-prompted instead of advancing.
        self.flow_misses = 0

        # Escalation state.
        self.escalated = False
        self.escalation_reason: str | None = None
        self.escalation_summary: str | None = None
        self.low_confidence_streak = 0
        # A handoff the assistant has offered but the customer has not accepted.
        # Escalating without asking takes the decision away from the person who
        # should be making it - plenty of customers would rather rephrase than
        # wait in a queue.
        self.pending_escalation: str | None = None

        # Set once a human replies. From then on the assistant stops answering
        # this conversation: two voices replying to the same customer is worse
        # than a slower single one, and an agent mid-sentence being talked over
        # by a bot is the failure people actually remember.
        self.handled_by: str | None = None

        # Demo lever: strips away routing, guardrails, retrieval and grounding
        # and leaves a plain LLM call over the conversation history - the
        # baseline the rest of the architecture is arguing against. Off by
        # default and scoped to this session only, so flipping it for a
        # demonstration never touches anyone else's conversation.
        self.raw_mode = False

    # -- transcript -------------------------------------------------------

    def add_message(self, role: str, text: str, author: str | None = None) -> None:
        """Append a turn to the transcript, masking anything sensitive first.

        Masking happens here, at the single point every message enters the
        record, rather than at each place one leaves it. The retrieval and
        prompt paths were patched individually and were clean; the transcript
        was not, so a card number typed by a customer still reached the agent
        console and the polling endpoint verbatim. One choke point is the only
        version of this that stays true as new readers are added - and the
        transcript outlives the turn, which is what makes it the copy that
        matters.

        The router keeps working from the raw text it was handed, so masking
        here changes what is *stored and shown*, never what is understood.
        """
        if role == "customer":
            text = guardrails.redact(text)
        self.messages.append(Message(role=role, text=text, author=author))

    def messages_since(self, index: int) -> list[dict]:
        """Messages after `index`, for the customer's page to poll.

        Deliberately narrow: role, text, author and timestamp only. The customer
        must never receive the audit trail, the escalation reason, or anything
        else `to_dict()` exposes to staff.
        """
        return [
            {"role": m.role, "text": m.text,
             "author": m.author, "timestamp": m.timestamp}
            for m in self.messages[max(index, 0):]
        ]

    def transcript(self, limit: int | None = None) -> str:
        messages = self.messages[-limit:] if limit else self.messages
        labels = {"customer": "Customer", "assistant": "Assistant",
                  "agent": "Human agent", "system": "System"}
        return "\n".join(f"{labels.get(m.role, m.role)}: {m.text}" for m in messages)

    # -- audit ------------------------------------------------------------

    def record(self, **kwargs) -> AuditEntry:
        entry = AuditEntry(turn=next(self.turn_counter), **kwargs)
        entry.utterance = guardrails.redact(entry.utterance)
        self.audit.append(entry)
        return entry

    # -- customer ---------------------------------------------------------

    @property
    def customer(self) -> dict | None:
        return find_customer(self.customer_id) if self.customer_id else None

    def reset_flow(self) -> None:
        self.pending_flow = None
        self.slots = {}
        self.flow_misses = 0

    def to_dict(self) -> dict:
        customer = self.customer
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "verified": self.verified,
            "customer_name": customer["name"] if customer else None,
            "customer_id": self.customer_id,
            "escalated": self.escalated,
            "escalation_reason": self.escalation_reason,
            "escalation_summary": self.escalation_summary,
            "handled_by": self.handled_by,
            "messages": [asdict(m) for m in self.messages],
            "audit": [asdict(a) for a in self.audit],
        }


class SessionStore:
    """In-memory store. A production build would put this in Redis or Postgres."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = Lock()

    def create(self) -> Session:
        # The customer's browser holds this id and polls with it, so it is a
        # bearer credential in practice - anyone holding it reads that
        # conversation. Sized accordingly: `uuid4().hex[:8]` was 32 bits, which
        # is guessable, and shortening a uuid for looks is a bad trade when the
        # value is the only thing protecting a transcript.
        session = Session(session_id=f"CHT-{secrets.token_urlsafe(24)}")
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def get_or_create(self, session_id: str | None) -> Session:
        if session_id:
            existing = self.get(session_id)
            if existing:
                return existing
        return self.create()

    def clear(self) -> int:
        """Drop every conversation. Returns how many were removed.

        The dashboard aggregates whatever is in this store, so a demo run leaves
        numbers behind that the next run would silently inherit. Clearing is the
        honest reset - and it is destructive, which is why it is staff-gated and
        confirmed in the UI rather than being a stray button.
        """
        with self._lock:
            count = len(self._sessions)
            self._sessions.clear()
        return count

    def escalated_sessions(self) -> list[Session]:
        with self._lock:
            sessions = [s for s in self._sessions.values() if s.escalated]
        return sorted(sessions, key=lambda s: s.created_at, reverse=True)
