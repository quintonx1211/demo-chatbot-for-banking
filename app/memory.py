"""What the bank remembers about a customer between conversations.

The client asked for this directly: a customer who called yesterday should not
start from zero today, and the same memory should hold across channels. The
demonstrable version of that is small - it is a store keyed by customer_id, not
by session - but the shape is the part that matters, because it is where the
privacy question lives.

Three rules, and they are the reason this is a separate module rather than a
dictionary on the session store:

1. Nothing is written before the customer is verified. An unverified session has
   no customer_id, so there is nothing to key on and nothing is retained.
2. Only topics and outcomes are kept, never utterances. "Asked about travel
   insurance, unresolved" is useful to the next conversation; the customer's
   sentence, which may contain a card number they typed by mistake, is not.
   Redaction is a mitigation; not storing it is a guarantee.
3. Entries expire. A campaign the customer declined six months ago should not
   shape today's conversation.

In production this is a row in the CRM, not a process dictionary - which is also
why the interface is deliberately narrow: `remember`, `recall`, `forget`.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# How long a remembered fact stays relevant. Short enough that the demo can show
# expiry, long enough to span the "called yesterday" case the client described.
TTL = timedelta(days=30)

# Per customer. A memory that grows without bound becomes a profile, which is a
# different thing from a continuity aid and carries different obligations.
MAX_ENTRIES = 12


@dataclass
class Note:
    kind: str                 # "topic" | "outcome" | "campaign"
    label: str                # a topic or offer name, never a raw utterance
    detail: str = ""
    channel: str = "chat"
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def expired(self) -> bool:
        return datetime.now(timezone.utc) - self.at > TTL

    def to_dict(self) -> dict:
        age = datetime.now(timezone.utc) - self.at
        return {
            "kind": self.kind, "label": self.label, "detail": self.detail,
            "channel": self.channel, "at": self.at.isoformat(timespec="seconds"),
            "age_days": round(age.total_seconds() / 86400, 1),
        }


class CustomerMemory:
    """Cross-session, cross-channel recall keyed to a verified customer."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._notes: dict[str, list[Note]] = {}

    def remember(self, customer_id: str | None, kind: str, label: str,
                 detail: str = "", channel: str = "chat") -> None:
        """Record one fact. A null customer_id is a no-op, by rule 1."""
        if not customer_id or not label:
            return
        with self._lock:
            notes = [n for n in self._notes.get(customer_id, []) if not n.expired]
            # Re-asking the same thing refreshes the note rather than stacking
            # duplicates - otherwise a persistent customer looks like twelve
            # distinct concerns instead of one unresolved one.
            for note in notes:
                if note.kind == kind and note.label == label:
                    note.at = datetime.now(timezone.utc)
                    note.detail = detail or note.detail
                    break
            else:
                notes.append(Note(kind=kind, label=label, detail=detail,
                                  channel=channel))
            self._notes[customer_id] = notes[-MAX_ENTRIES:]

    def recall(self, customer_id: str | None) -> list[Note]:
        """Live notes, newest first. Expired entries are dropped on read."""
        if not customer_id:
            return []
        with self._lock:
            notes = [n for n in self._notes.get(customer_id, []) if not n.expired]
            self._notes[customer_id] = notes
            return sorted(notes, key=lambda n: n.at, reverse=True)

    def summary(self, customer_id: str | None) -> str:
        """One line for the greeting after verification, or empty.

        Kept to a single sentence on purpose. A customer who is told everything
        the bank remembers about them at the start of every conversation finds
        it unsettling, not helpful.
        """
        notes = self.recall(customer_id)
        unresolved = [n for n in notes if n.kind == "outcome" and n.label == "unresolved"]
        topics = [n for n in notes if n.kind == "topic"][:2]
        if unresolved and unresolved[0].detail:
            return (f"Last time we spoke you asked about {unresolved[0].detail} "
                    f"and I passed it to a colleague.")
        if topics:
            subjects = " and ".join(n.label for n in topics)
            return f"Welcome back - last time we covered {subjects}."
        return ""

    def forget(self, customer_id: str | None = None) -> int:
        """Erase one customer's memory, or everyone's. Backs the console button
        and, in production, a data-subject deletion request."""
        with self._lock:
            if customer_id is None:
                count = sum(len(v) for v in self._notes.values())
                self._notes.clear()
                return count
            return len(self._notes.pop(customer_id, []))

    @property
    def stats(self) -> dict:
        with self._lock:
            live = {k: [n for n in v if not n.expired] for k, v in self._notes.items()}
            return {
                "customers": sum(1 for v in live.values() if v),
                "notes": sum(len(v) for v in live.values()),
                "ttl_days": TTL.days,
                "retains": "topics and outcomes only - never utterances",
            }

    def to_dict(self) -> dict:
        with self._lock:
            return {
                cid: [n.to_dict() for n in self.recall(cid)]
                for cid in list(self._notes) if self._notes[cid]
            }


# One store for the process, mirroring the single CRM it stands in for.
store = CustomerMemory()
