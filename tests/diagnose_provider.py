"""Find out why a provider call is failing, without printing the key.

The grounding-comparison panel makes one provider call per column. When only
the ungrounded column shows an error, the natural reading is that the grounded
column worked - and that reading is wrong. `answer_from_kb` degrades to
extractive knowledge-base text on *any* provider failure, so a dead provider
produces a grounded column that reads like a working answer. This script makes
both calls directly, with no fallback in the way, so the two are comparable.

    python diagnose_provider.py

Reads the key from the environment only. Nothing here prints a credential; the
key is reported as "set"/"not set" and by length.
"""

from __future__ import annotations

import sys

# The Windows console defaults to cp1252, which cannot encode Vietnamese. Now
# that the assistant answers in Vietnamese, printing a reply raised
# UnicodeEncodeError and took the whole test run down - a test suite that dies
# on its own output reports nothing about the code it was meant to check.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import llm                                    # noqa: E402
from app.llm import compare as compare_mod             # noqa: E402
from app.llm.base import LLMRequest, build_answer_request  # noqa: E402
from app.retriever import KnowledgeBase                # noqa: E402

QUESTION = "How much is the overdraft fee, and is there a daily cap?"


def _line(label: str, value: object) -> None:
    print(f"  {label:<26} {value}")


def main() -> int:
    print("\n=== Environment ===")
    provider = llm.active_provider()
    info = llm.describe()
    _line("requested provider", info["requested"])
    _line("active provider", info["provider"] or "NONE - running offline")
    _line("model", info["model"])
    _line("effort", info["effort"])
    _line("tone", info.get("tone"))
    _line("temperature", info.get("temperature")
          if info.get("temperature") is not None else "provider default")

    for name, adapter in llm.PROVIDERS.items():
        present = next((k for k in adapter.ENV_KEYS if os.environ.get(k)), None)
        _line(f"key: {name}",
              f"set via {present} ({len(os.environ[present])} chars)"
              if present else "not set")

    if provider is None:
        print("\nNo provider is active, so both comparison columns fall back to")
        print("extractive text. Set a key in Settings, or export one, and rerun.")
        return 1

    kb = KnowledgeBase()
    passages = kb.search(QUESTION, top_k=3)
    _line("passages retrieved", len(passages))

    # Exactly the two calls the comparison panel makes, in the same order,
    # bypassing _run so nothing is masked by the extractive fallback.
    calls = [
        ("GROUNDED  (as answer_from_kb builds it)",
         build_answer_request(QUESTION, passages, tone=llm.current_tone(),
                              temperature=llm.current_temperature())),
        ("UNGROUNDED (as compare.py builds it)",
         LLMRequest(system=compare_mod.UNGROUNDED_SYSTEM_PROMPT,
                    user=QUESTION, max_tokens=1000)),
    ]

    failures = 0
    for label, request in calls:
        print(f"\n=== {label} ===")
        _line("max_tokens", request.max_tokens)
        _line("temperature", request.temperature
              if request.temperature is not None else "omitted")
        _line("system prompt", f"{len(request.system)} chars")
        started = time.perf_counter()
        try:
            result = provider.complete(request, llm._effort())
        except Exception as exc:                    # the adapter should catch
            print(f"  RAISED PAST THE ADAPTER: {type(exc).__name__}: {exc}")
            failures += 1
            continue
        elapsed = int((time.perf_counter() - started) * 1000)
        _line("elapsed", f"{elapsed} ms")
        _line("generated", result.generated)
        _line("error", result.error or "none")
        _line("tokens in/out", f"{result.input_tokens}/{result.output_tokens}")
        _line("text", (result.text[:110].replace("\n", " ") + "...")
              if result.text else "(empty)")
        if not result.generated:
            failures += 1

    print("\n=== Verdict ===")
    if failures == 0:
        print("  Both calls succeeded. The comparison panel is valid.")
        return 0
    if failures == 2:
        print("  BOTH calls failed. The comparison panel is showing you an")
        print("  extractive grounded column beside an ungrounded error - it is")
        print("  not evidence about grounding. Fix the provider first.")
    else:
        print("  One call failed and one succeeded, so the difference is in the")
        print("  request, not the connection. Compare max_tokens, temperature")
        print("  and system prompt length above - a reasoning model can spend a")
        print("  1000-token budget on thinking and return empty with")
        print("  finish_reason=length, which surfaces as 'truncated'.")
    print("\n  Error meanings:")
    print("    provider_auth_error  - key rejected (401/403). Check it is a key")
    print("                           for THIS provider, not another one.")
    print("    provider_rate_limited- free tier exhausted; wait or switch model.")
    print("    provider_unreachable - transport failed before any HTTP response.")
    print("    provider_timeout     - connected, no response in time.")
    print("    truncated            - budget spent before any answer text.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
