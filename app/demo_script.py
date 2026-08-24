"""Deterministic reply scripts for live demos.

`data/demo_scripts.json` holds named, ordered scripts. Once armed on a
session (`start`), every subsequent turn returns that script's next reply
verbatim - bypassing routing, guardrails, retrieval and the model entirely -
so a live demo is never at the mercy of a flaky provider, a retrieval miss,
or the customer fixture having drifted since the script was written. This is
a presenter's safety net, not a new production mode: nothing here checks or
validates what the "customer" typed, on purpose (see `AskUserQuestion`
answer this was built against - a live demo should never break because
someone fat-fingered a digit).

When a script runs out mid-conversation, `next_reply` disarms it and returns
None so the caller falls back to normal routing for that turn, rather than
erroring out in front of an audience.
"""
from __future__ import annotations

import json
from pathlib import Path

SCRIPTS_PATH = Path(__file__).resolve().parent.parent / "data" / "demo_scripts.json"


def _load() -> dict:
    if not SCRIPTS_PATH.exists():
        return {}
    with SCRIPTS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def list_scripts() -> list[dict]:
    """Every script defined in the file, for a staff-facing picker."""
    return [
        {"name": name, "label": body.get("label", name),
         "steps": len(body.get("steps", []))}
        for name, body in sorted(_load().items())
    ]


def step_count(name: str) -> int:
    return len(_load().get(name, {}).get("steps", []))


def start(session, name: str) -> bool:
    """Arm `name` on `session`. Returns False if no such script exists."""
    if name not in _load():
        return False
    session.script_name = name
    session.script_step = 0
    return True


def stop(session) -> None:
    session.script_name = None
    session.script_step = 0


def next_reply(session) -> dict | None:
    """The next scripted step for this session's armed script.

    Returns None - and disarms the script - once every step has been used,
    so the router's fallback to normal handling is unconditional rather than
    something the caller has to remember to check for separately.
    """
    if not session.script_name:
        return None
    body = _load().get(session.script_name)
    if body is None:
        # The file changed under us (script renamed/removed mid-demo).
        session.script_name = None
        session.script_step = 0
        return None
    steps = body.get("steps", [])
    if session.script_step >= len(steps):
        session.script_name = None
        session.script_step = 0
        return None
    step = steps[session.script_step]
    session.script_step += 1
    return step
