"""Outbound voice-call layer: tools for a voice agent + a call log.

The bot's dialog brain (router.py / flows.py) is turn-based text. A real-time
voice agent - ElevenLabs Conversational AI, FPT.AI, etc. - runs the *conversation*
itself and reaches back into the bank only for facts and actions. This module is
that reach-back surface:

  * `tool_*` functions wrap the same db / campaigns the chat flows use, but as
    small stateless calls a voice agent can invoke mid-call (verify the caller,
    read a balance, list eligible offers). They take plain identifiers and
    return plain dicts - no Session, no slot-filling.
  * `CallStore` records every outbound call an admin places, so the console has
    a call log independent of whichever telephony provider actually dials.

Nothing here dials a phone. Placing the call is the provider's job (see
docs/ELEVENLABS-CALLBOT.md); server.py::/api/voice/call creates the record and
is where the provider's outbound API gets invoked once one is wired in.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path

from . import agents, db
from .campaigns import CampaignBook

CALL_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "call_log.json"

_campaigns = CampaignBook()


# ---------- tools the voice agent calls mid-conversation ----------


def tool_verify_customer(phone: str, national_id: str) -> dict:
    """Two-factor identity check, same rule as flows._verify_*: full phone then
    national id. Returns the customer_id the other tools need, only on success."""
    phone = (phone or "").strip()
    national_id = (national_id or "").strip()
    match = db.find_by_phone(phone)
    if not match or not db.check_national_id(match["customer_id"], national_id):
        return {"verified": False}
    return {
        "verified": True,
        "customer_id": match["customer_id"],
        "name": match["name"],
    }


def tool_account_snapshot(customer_id: str) -> dict:
    """Balances + card states for an already-verified customer. The voice agent
    must call verify_customer first and pass the customer_id it returned; this
    does not itself authenticate."""
    customer = db.get_customer(customer_id)
    if not customer:
        return {"found": False}
    return {
        "found": True,
        "name": customer["name"],
        "accounts": [
            {
                "type": a["type"],
                "mask": a["mask"],
                "balance": a["balance"],
                "available": a["available"],
                "currency": a["currency"],
            }
            for a in customer["accounts"]
        ],
        "cards": [
            {"type": c["type"], "mask": c["mask"], "status": c["status"]}
            for c in customer["cards"]
        ],
    }


def tool_offers(customer_id: str) -> dict:
    """Cross-sell offers the CRM already flagged for this customer - never
    invented. Mirrors flows._card_offers: read, don't infer."""
    offers = _campaigns.offers_for(customer_id) or []
    return {
        "offers": [
            {"name": o.name, "type": o.type, "hook": o.hook, "cta": o.cta}
            for o in offers
        ]
    }


# Registry so server.py can dispatch by name and the docs/tool schema stay in
# one place. Each entry: (function, required arg names).
TOOLS = {
    "verify_customer": (tool_verify_customer, ("phone", "national_id")),
    "account_snapshot": (tool_account_snapshot, ("customer_id",)),
    "offers": (tool_offers, ("customer_id",)),
}


def call_tool(name: str, payload: dict) -> tuple[dict | None, str | None]:
    """Dispatch a tool by name. Returns (result, error). Missing args -> error,
    so a misconfigured agent gets a clear 400 instead of a stack trace."""
    entry = TOOLS.get(name)
    if entry is None:
        return None, f"unknown tool: {name}"
    fn, required = entry
    missing = [a for a in required if not (payload or {}).get(a)]
    if missing:
        return None, f"missing args: {', '.join(missing)}"
    return fn(**{a: payload[a] for a in required}), None


# ---------- outbound call log ----------


class CallStore:
    """Every outbound call an admin places, persisted to data/call_log.json.

    Deliberately provider-agnostic: a record is created the moment the admin
    hits "Gọi", carries a status the provider webhook can advance later
    (queued -> ringing -> in_progress -> completed/failed), and holds an
    optional transcript/summary the provider can post back.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or CALL_LOG_PATH
        self._lock = threading.Lock()
        self._calls: list[dict] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._calls = json.loads(self.path.read_text(encoding="utf-8")).get(
                    "calls", []
                )
            except (ValueError, KeyError):
                self._calls = []

    def _flush_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"calls": self._calls}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def create(self, phone: str, agent_id: str) -> dict:
        agent = agents.store.get(agent_id)
        record = {
            "id": "CALL-" + secrets.token_hex(5),
            "phone": (phone or "").strip(),
            "agent_id": agent_id,
            "agent_name": agent.name if agent else agent_id,
            "status": "queued",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "transcript": None,
            "summary": None,
        }
        with self._lock:
            self._calls.insert(0, record)  # newest first
            self._flush_locked()
        return record

    def update(self, call_id: str, **fields) -> dict | None:
        allowed = {"status", "transcript", "summary"}
        with self._lock:
            rec = next((c for c in self._calls if c["id"] == call_id), None)
            if rec is None:
                return None
            for k, v in fields.items():
                if k in allowed and v is not None:
                    rec[k] = v
            self._flush_locked()
            return rec

    def list(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return self._calls[:limit]


store = CallStore()
