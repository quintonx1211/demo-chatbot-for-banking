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
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SCRIPTS_PATH = Path(__file__).resolve().parent.parent / "data" / "demo_scripts.json"


def _load() -> dict:
    """Read data/demo_scripts.json fresh on every call.

    Re-read rather than cached: a presenter editing the file between two
    demos (or mid-rehearsal) should see the change without a server restart.
    The cost is a small file read on the hot path of every scripted turn -
    negligible against the handful of KB it holds.

    Never raises. A hand-edited file with a JSON syntax error would otherwise
    take down every armed session's chat with a 500 - the one moment a
    presenter can least afford that - so a broken file is treated exactly
    like a missing one: no scripts available, normal routing continues.
    Run `python -m app.demo_script` before a demo to catch a typo while
    there is still time to fix it instead of live on stage.
    """
    if not SCRIPTS_PATH.exists():
        return {}
    try:
        with SCRIPTS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("data/demo_scripts.json could not be read (%s) - "
                       "no scripts available until it is fixed", exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("data/demo_scripts.json must be a JSON object at the "
                       "top level - no scripts available")
        return {}
    return data


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


def validate() -> list[str]:
    """Human-readable problems in data/demo_scripts.json, empty if clean.

    Deliberately re-parses the raw file rather than calling `_load()`, so a
    JSON syntax error is reported as the specific problem it is instead of
    being silently swallowed into "no scripts available" the way every
    runtime caller treats it.
    """
    if not SCRIPTS_PATH.exists():
        return [f"{SCRIPTS_PATH} không tồn tại"]
    try:
        data = json.loads(SCRIPTS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"JSON không hợp lệ: {exc}"]
    if not isinstance(data, dict):
        return ["nội dung gốc phải là một JSON object dạng {tên_kịch_bản: {...}}"]

    problems: list[str] = []
    for name, body in data.items():
        if not isinstance(body, dict):
            problems.append(f"'{name}': phải là một object")
            continue
        steps = body.get("steps")
        if not isinstance(steps, list) or not steps:
            problems.append(f"'{name}': 'steps' phải là một danh sách khác rỗng")
            continue
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                problems.append(f"'{name}' bước {i + 1}: phải là một object")
                continue
            reply = step.get("reply")
            if not isinstance(reply, str) or not reply.strip():
                problems.append(f"'{name}' bước {i + 1}: 'reply' phải là chuỗi văn bản khác rỗng")
    return problems


if __name__ == "__main__":
    import sys
    # Windows consoles default to cp1252, which cannot print the Vietnamese
    # text this prints - same fix as tests/smoke_test.py.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    problems = validate()
    if problems:
        print(f"{len(problems)} vấn đề trong {SCRIPTS_PATH}:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    scripts = list_scripts()
    print(f"OK - {len(scripts)} kịch bản trong {SCRIPTS_PATH}:")
    for s in scripts:
        print(f"  {s['name']}: {s['label']} ({s['steps']} lượt)")
