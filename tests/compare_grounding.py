"""Same question, same model, with and without grounding - from the terminal.

This used to be a tab in the agent console. It does not belong there: a contact
centre console is a place to work escalations, not a place to run experiments
about the architecture. The comparison is a thing you show once, deliberately,
while narrating it - which makes it a scripted demo step, not an operational
surface. See TEST-SCENARIOS.txt section B4.

    python tests/compare_grounding.py                     # run the suggested set
    python tests/compare_grounding.py "your question"     # one question

Needs a provider configured, and says so plainly when there is not - a run
where the model never answered proves nothing either way, and this script
refuses to present it as though it did.
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
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.llm import compare as compare_mod   # noqa: E402
from app.retriever import KnowledgeBase      # noqa: E402

WIDTH = 78


def _rule(char: str = "=") -> str:
    return char * WIDTH


def _wrap(text: str, indent: str = "  ") -> str:
    if not text:
        return f"{indent}(empty)"
    return "\n".join(
        textwrap.fill(line, WIDTH, initial_indent=indent,
                      subsequent_indent=indent) or indent
        for line in text.strip().splitlines()
    )


def _pct(value) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def show(result: dict) -> bool:
    """Print one comparison. Returns True if it was a valid one."""
    print("\n" + _rule())
    print(f"  {result['question']}")
    print(_rule())

    if not result["comparable"]:
        reason = result["blocked_by"]
        print("\n  !! THIS COMPARISON IS NOT VALID - DO NOT DEMO IT !!\n")
        if reason == "no_provider":
            print("  No model provider is configured.")
        else:
            print(f"  The model call failed: {reason}")
        # The reason this warning exists at all: the grounded path degrades to
        # verified knowledge-base text on any provider failure, so it prints
        # something that reads like a working answer. Beside an ungrounded
        # column showing an error, that looks like proof grounding won a
        # contest which never took place.
        print("  The grounded side below has fallen back to returning verified")
        print("  knowledge-base text. That is correct product behaviour, but it")
        print("  is not the model answering, so nothing here shows what")
        print("  grounding prevents. Fix the provider and run it again.\n")

    ungrounded = result["ungrounded"]
    print("\n  UNGROUNDED - no passages, no gate")
    print(f"  {'-' * (WIDTH - 2)}")
    if ungrounded.get("error"):
        print(f"  error: {ungrounded['error']}")
    else:
        print(_wrap(ungrounded["text"]))
        print(f"\n  sources: none")
        print(f"  supported by the corpus: "
              f"{_pct(ungrounded.get('overlap_with_corpus'))}")
        print(f"  checked before shipping: no")

    grounded = result["grounded"]
    print("\n  GROUNDED - the production path")
    print(f"  {'-' * (WIDTH - 2)}")
    print(_wrap(grounded["text"]))
    print(f"\n  answered by: "
          + ("the model" if grounded["generated"]
             else "extractive fallback - THE MODEL DID NOT RUN"))
    for source in grounded["sources"]:
        print(f"  source: {source['breadcrumb']}")
    if not grounded["sources"]:
        print("  source: none - nothing cleared the relevance floor")
    if grounded["grounding"] is not None:
        print(f"  grounding score: {grounded['grounding']:.2f}"
              f"  ({'passed' if grounded['passed_gate'] else 'FAILED - would escalate'})")

    return result["comparable"]


def main(argv: list[str]) -> int:
    kb = KnowledgeBase()
    questions = argv[1:] or compare_mod.SUGGESTED

    valid = 0
    for question in questions:
        if show(compare_mod.compare(question, kb)):
            valid += 1

    print("\n" + _rule())
    print(f"  {valid} of {len(questions)} comparisons were valid.")
    if valid < len(questions):
        print("  An invalid comparison is not a weaker result - it is no result.")
    print(_rule() + "\n")
    return 0 if valid == len(questions) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
