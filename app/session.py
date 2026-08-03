"""Conversation state, verification state and the audit trail.

Everything a human agent needs to pick up an escalated conversation lives here:
the full transcript, which customer was verified and how, and a per-turn record
of how each answer was produced.
"""

from __future__ import annotations

import itertools
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock

from . import guardrails

ACCOUNTS_PATH = Path(__file__).resolve().parent.parent / "data" / "accounts.json"


def _load_customers() -> list[dict]:
    return json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))["customers"]


CUSTOMERS = _load_customers()


def find_customer(customer_id: str) -> dict | None:
    return next((c for c in CUSTOMERS if c["customer_id"] == customer_id), None)


@dataclass
class Message:
    role: str            # "customer" | "assistant" | "agent" | "system"
    text: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class AuditEntry:
    """One row per turn. This is what a compliance reviewer reads."""
    turn: int
    utterance: str          # redacted
    route: str              # deterministic | rag | guardrail | escalation
    intent: str
    confidence: float
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

        # Escalation state.
        self.escalated = False
        self.escalation_reason: str | None = None
        self.escalation_summary: str | None = None
        self.low_confidence_streak = 0

    # -- transcript -------------------------------------------------------

    def add_message(self, role: str, text: str) -> None:
        self.messages.append(Message(role=role, text=text))

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
            "messages": [asdict(m) for m in self.messages],
            "audit": [asdict(a) for a in self.audit],
        }


class SessionStore:
    """In-memory store. A production build would put this in Redis or Postgres."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = Lock()

    def create(self) -> Session:
        session = Session(session_id=f"CHT-{uuid.uuid4().hex[:8].upper()}")
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

    def escalated_sessions(self) -> list[Session]:
        with self._lock:
            sessions = [s for s in self._sessions.values() if s.escalated]
        return sorted(sessions, key=lambda s: s.created_at, reverse=True)
