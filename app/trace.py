"""Decision trace — why a turn took the route it did.

The audit trail already records *what* happened. This records the reasoning:
every gate the turn passed through, what it saw, and which threshold it was
compared against. Two audiences need it for different reasons.

A reviewer needs to answer "why did the assistant say that" months later, and
"the classifier was confident" is not an answer — "intent `block_card` scored
0.91 against a 0.55 threshold, so the scripted flow ran and no model was
involved" is.

A prospect in a demo needs to see that the routing is a set of stated rules
rather than a black box. The single most common question about a hybrid
assistant is "how does it decide?", and pointing at a trace answers it better
than any diagram.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Step:
    stage: str          # pending_flow | guardrail | nlu | retrieval | generation | ...
    outcome: str        # what this stage concluded
    detail: str = ""    # the numbers behind it
    decisive: bool = False   # True if this stage is why the turn ended up where it did

    def to_dict(self) -> dict:
        return {"stage": self.stage, "outcome": self.outcome,
                "detail": self.detail, "decisive": self.decisive}


@dataclass
class Trace:
    steps: list[Step] = field(default_factory=list)

    def add(self, stage: str, outcome: str, detail: str = "",
            decisive: bool = False) -> None:
        self.steps.append(Step(stage, outcome, detail, decisive))

    def decide(self, stage: str, outcome: str, detail: str = "") -> None:
        """Record the step that settled the route."""
        self.add(stage, outcome, detail, decisive=True)

    def to_list(self) -> list[dict]:
        return [s.to_dict() for s in self.steps]

    @property
    def summary(self) -> str:
        """One line naming the deciding stage, for the audit note."""
        decisive = next((s for s in reversed(self.steps) if s.decisive), None)
        return f"{decisive.stage}: {decisive.outcome}" if decisive else ""


# Human-readable descriptions of each stage, shown alongside the trace so the
# rule being applied is legible without reading the source.
STAGE_RULES = {
    "pending_flow": "A scripted flow already in progress owns the turn, unless "
                    "the customer clearly changed subject.",
    "handoff_offer": "An outstanding offer of a human agent is answered before "
                     "anything else is classified.",
    "guardrail": "Regulated topics are refused here, before any model sees the "
                 "text.",
    "campaign": "A campaign the customer is eligible for, from the overnight "
                "batch file. Deterministic — no model involved.",
    "nlu": "Intent confidence at or above 0.55 runs a scripted flow that reads "
           "the record. Below that, the question goes to the knowledge base.",
    "retrieval": "Passages must clear a relevance floor. Nothing above it means "
                 "no verified source exists, and the assistant offers a human "
                 "rather than guessing.",
    "generation": "The model may only use the retrieved passages. Its answer is "
                  "then scored against them; below 0.55 it is discarded.",
    "escalation": "Handed to a human, with the full conversation attached.",
    "raw_mode": "Demo lever, on for this conversation only: routing, "
                "guardrails, retrieval and the grounding check are all "
                "bypassed - the model answers from conversation history alone.",
}
