"""Operational metrics, computed from live session data.

The case study is sold on business outcomes — deflection, response time, agent
hours — while the demo until now spoke only in coverage scores and intent
confidence. This module translates: it reads the audit trail the router already
writes and reports the same run in the language the buyer uses.

Two rules it follows, because a dashboard that breaks either is worse than no
dashboard:

  * **Nothing is invented.** Every figure is derived from real turns in this
    process. There are no seeded constants and no illustrative numbers.
  * **Estimates are labelled as estimates.** Agent-hours saved is a *model*,
    not a measurement — it multiplies deflected conversations by an assumed
    handling time. The assumption is returned alongside the number so the
    screen can show it, because presenting a modelled figure as a measured one
    is exactly where a demo loses the room under questioning.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass

from .session import Session

# Assumption behind the hours-saved estimate. The case study's own framing —
# routine calls displaced from an overstretched call centre — puts a handled
# call in the minutes, not seconds. Stated here so it can be shown and argued
# with rather than buried.
ASSUMED_HANDLE_MINUTES = 4.0

# The call-centre baseline the case study quotes, for the response-time
# comparison. Not measured here — it is the client's stated starting point.
BASELINE_RESPONSE_SECONDS = 180


@dataclass
class Snapshot:
    conversations: int
    turns: int
    deflected: int
    escalated: int
    deflection_rate: float
    route_mix: dict[str, int]
    median_latency_ms: float
    p90_latency_ms: float
    grounded_answers: int
    knowledge_answers: int
    grounding_rate: float
    guardrail_blocks: int
    llm_answers: int
    estimated_minutes_saved: float
    assumptions: dict

    def to_dict(self) -> dict:
        return {
            "conversations": self.conversations,
            "turns": self.turns,
            "deflected": self.deflected,
            "escalated": self.escalated,
            "deflection_rate": round(self.deflection_rate, 3),
            "route_mix": self.route_mix,
            "median_latency_ms": round(self.median_latency_ms),
            "p90_latency_ms": round(self.p90_latency_ms),
            "baseline_response_seconds": BASELINE_RESPONSE_SECONDS,
            "grounded_answers": self.grounded_answers,
            "knowledge_answers": self.knowledge_answers,
            "grounding_rate": round(self.grounding_rate, 3),
            "guardrail_blocks": self.guardrail_blocks,
            "llm_answers": self.llm_answers,
            "estimated_minutes_saved": round(self.estimated_minutes_saved, 1),
            "assumptions": self.assumptions,
        }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(fraction * len(ordered)), len(ordered) - 1)
    return ordered[index]


def snapshot(sessions: list[Session]) -> Snapshot:
    """Aggregate the audit trails of every conversation in this process."""
    turns = [entry for session in sessions for entry in session.audit]

    route_mix: dict[str, int] = {}
    for entry in turns:
        route_mix[entry.route] = route_mix.get(entry.route, 0) + 1

    # A conversation counts as deflected when the assistant carried it to the
    # end without a handoff. Sessions with no turns yet are excluded rather
    # than counted as successes — an empty chat window is not a deflection.
    engaged = [s for s in sessions if s.audit]
    escalated = [s for s in engaged if s.escalated]
    deflected = [s for s in engaged if not s.escalated]

    # Latency excludes agent turns: a human's typing speed is not the
    # assistant's response time, and mixing them makes the number meaningless.
    latencies = [float(e.latency_ms) for e in turns if e.route != "agent"]

    # "Knowledge answers" are the turns where grounding is even applicable —
    # deterministic flows read the record and cite nothing by design, so
    # including them would inflate the rate with turns that were never at risk.
    knowledge = [e for e in turns if e.route == "rag"]
    grounded = [e for e in knowledge if e.sources]

    return Snapshot(
        conversations=len(engaged),
        turns=len(turns),
        deflected=len(deflected),
        escalated=len(escalated),
        deflection_rate=len(deflected) / len(engaged) if engaged else 0.0,
        route_mix=dict(sorted(route_mix.items(), key=lambda kv: -kv[1])),
        median_latency_ms=statistics.median(latencies) if latencies else 0.0,
        p90_latency_ms=_percentile(latencies, 0.9),
        grounded_answers=len(grounded),
        knowledge_answers=len(knowledge),
        grounding_rate=len(grounded) / len(knowledge) if knowledge else 0.0,
        guardrail_blocks=route_mix.get("guardrail", 0),
        llm_answers=sum(1 for e in turns if e.generated),
        estimated_minutes_saved=len(deflected) * ASSUMED_HANDLE_MINUTES,
        assumptions={
            "handle_minutes_per_deflected_conversation": ASSUMED_HANDLE_MINUTES,
            "note": (
                "Hours saved is an estimate, not a measurement: deflected "
                "conversations multiplied by an assumed handling time. Every "
                "other figure on this page is counted from real turns in this "
                "process."
            ),
        },
    )


def recent_activity(sessions: list[Session], limit: int = 12) -> list[dict]:
    """Newest conversations, for the dashboard's activity list."""
    engaged = [s for s in sessions if s.audit]
    engaged.sort(key=lambda s: s.audit[-1].timestamp, reverse=True)

    rows = []
    for session in engaged[:limit]:
        last = session.audit[-1]
        customer = session.customer
        rows.append({
            "session_id": session.session_id,
            "customer_name": customer["name"] if customer else None,
            "turns": len(session.audit),
            "escalated": session.escalated,
            "handled_by": session.handled_by,
            "reason": session.escalation_reason,
            "last_route": last.route,
            "last_utterance": last.utterance,
            "age_seconds": int(time.time() - last.timestamp),
        })
    return rows
