"""Runtime policy - how strict the assistant is, from a config file.

The client asked whether strictness could be a parameter rather than a fixed
design decision, and accepted a config file over a dashboard. This is that
parameter.

It matters more than a tuning knob usually would, because the three thresholds
it controls are the ones that trade the two failure modes against each other:

  * raise them and the assistant refuses more, escalates more, and is very
    unlikely to say something wrong
  * lower them and it answers more questions, more naturally, and is more
    likely to be wrong

There is no setting that avoids both. Naming the presets after the trade-off -
strict / balanced / relaxed - keeps that visible to whoever changes the file,
rather than presenting the numbers as though one of them is simply correct.

Reloaded on demand, so a change takes effect without a restart.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

# Used when config.json is missing or unreadable. Deliberately the balanced
# preset rather than the loosest: a corrupt config should not silently make the
# assistant more willing to answer.
_FALLBACK = {"min_grounding": 0.55, "min_relevance": 0.28, "high_confidence": 0.55}


@dataclass
class Policy:
    strictness: str
    min_grounding: float      # answer must be this well supported by its sources
    min_relevance: float      # a passage must clear this to count as evidence
    high_confidence: float    # intent confidence needed to run a scripted flow
    source: str               # where each value came from, for the console
    # Which classifier decides the route. Deliberately NOT part of a strictness
    # preset: it changes which component is in charge, not how cautious that
    # component is, and bundling it into "strict"/"relaxed" would hide a
    # architectural switch behind a safety dial.
    router_mode: str = "nlu"

    def to_dict(self) -> dict:
        return {
            "strictness": self.strictness,
            "min_grounding": self.min_grounding,
            "min_relevance": self.min_relevance,
            "high_confidence": self.high_confidence,
            "source": self.source,
            "router_mode": self.router_mode,
            "config_path": str(CONFIG_PATH),
        }


def load(path: Path | None = None) -> Policy:
    path = path or CONFIG_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return Policy(strictness="balanced", source=f"defaults ({exc.__class__.__name__})",
                      **_FALLBACK)

    presets = raw.get("presets", {})
    name = str(raw.get("strictness", "balanced")).lower()
    values = dict(_FALLBACK)
    values.update(presets.get(name, {}))
    source = f"preset '{name}'"

    # An explicit override beats the preset. Anything non-numeric is ignored
    # rather than coerced - a typo in a threshold should not quietly become 0.0
    # and disable the gate it was meant to tune.
    overrides = raw.get("overrides") or {}
    applied = []
    for key in ("min_grounding", "min_relevance", "high_confidence"):
        value = overrides.get(key)
        if isinstance(value, (int, float)) and 0.0 < float(value) <= 1.0:
            values[key] = float(value)
            applied.append(key)
    if applied:
        source += f" + overrides({', '.join(applied)})"

    # Anything unrecognised falls back to the lexical router. A typo in this
    # field must not disable routing, and must not silently hand control of
    # every turn to a model.
    mode = str(raw.get("router_mode", "nlu")).strip().lower()
    if mode not in ("nlu", "shadow", "llm"):
        mode = "nlu"
        source += " + router_mode(invalid, using nlu)"

    return Policy(strictness=name, source=source, router_mode=mode, **values)


# Loaded once at import and refreshed through reload(); modules read the live
# object so a config change propagates without restarting the server.
current = load()


def reload() -> Policy:
    global current
    current = load()
    return current
