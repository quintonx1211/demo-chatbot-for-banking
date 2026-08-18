"""Re-calibrate the grounding threshold for the active provider.

The threshold in guardrails.MIN_GROUNDING was measured against Claude's output
style. A model that paraphrases more loosely scores lower on the same correct
answer and will escalate turns it should have answered; one that stays closer
to the source scores higher and makes the gate too permissive. Either way the
number has to be re-measured per provider, not assumed.

    python tests/calibrate_grounding.py

With no provider configured this still runs, scoring hand-written reference
answers so you can see the metric's behaviour and the separation it needs.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import guardrails, llm
from app.retriever import KnowledgeBase

# Questions the knowledge base genuinely covers. A correct answer to these
# should score well above the threshold.
IN_SCOPE = [
    "what are your fees for international transfers",
    "how do I reset my online banking password",
    "how much is the overdraft fee",
    "what documents do I need to open an account",
    "how long does a mortgage application take",
    "what happens if I report my card stolen",
    "how long do I have to dispute a transaction",
    "what are your branch opening hours",
]

# Plausible-sounding answers that are NOT in the knowledge base. These are the
# failure mode the gate exists to catch, and should score far below in-scope.
HALLUCINATED = [
    ("what are your fees for international transfers",
     "Our premium international remittance service offers competitive exchange "
     "rates with zero markup for Platinum members, and typically arrives within "
     "30 minutes via our partner network in over 120 countries."),
    ("how much is the overdraft fee",
     "Overdraft protection is complimentary on all accounts and we never charge "
     "a fee; we simply decline the transaction and send you a text alert so you "
     "can top up at your convenience."),
    ("how long does a mortgage application take",
     "Most mortgage applications are approved instantly through our AI "
     "underwriting engine, with funds released the same afternoon once you "
     "e-sign the offer documents."),
]


def main() -> int:
    kb = KnowledgeBase()
    info = llm.describe()

    print(f"Provider : {info['provider'] or 'none (extractive)'}")
    print(f"Model    : {info['model'] or '-'}")
    if info["mode"] != "live":
        print(f"Note     : {info['detail']}")
        print("           Scores below come from the extractive fallback, which "
              "returns\n           source text verbatim and therefore scores near "
              "1.00 by construction.\n           Configure a provider to calibrate "
              "a real threshold.")
    print(f"Current threshold: {guardrails.MIN_GROUNDING}\n")

    print("IN-SCOPE (a correct answer should score high)")
    in_scores = []
    for question in IN_SCOPE:
        passages = kb.search(question)
        if not passages:
            print(f"  --    (no passages retrieved) {question}")
            continue
        result = llm.answer_from_kb(question, passages)
        context = " ".join(p.passage.text for p in passages)
        score = guardrails.grounding_score(result.text, context)
        in_scores.append(score)
        flag = " <-- would escalate" if score < guardrails.MIN_GROUNDING else ""
        print(f"  {score:.2f}  {question}{flag}")

    print("\nHALLUCINATED (fixed text - should score low)")
    out_scores = []
    for question, answer in HALLUCINATED:
        passages = kb.search(question)
        context = " ".join(p.passage.text for p in passages)
        score = guardrails.grounding_score(answer, context)
        out_scores.append(score)
        flag = " <-- would be served!" if score >= guardrails.MIN_GROUNDING else ""
        print(f"  {score:.2f}  {question}{flag}")

    if not in_scores:
        print("\nNo in-scope answers scored; check the knowledge base.")
        return 1

    lowest_good, highest_bad = min(in_scores), max(out_scores)
    print(f"\nLowest in-scope : {lowest_good:.2f}")
    print(f"Highest bad     : {highest_bad:.2f}")

    if lowest_good <= highest_bad:
        print("\nNO SEPARATION - the two populations overlap, so no single "
              "threshold\nworks for this provider. Tighten the answer prompt "
              "toward the source\nwording, or replace this metric with a "
              "model-based faithfulness check.")
        return 1

    suggested = round((lowest_good + highest_bad) / 2, 2)
    print(f"\nSuggested MIN_GROUNDING: {suggested}  "
          f"(midpoint of the gap {highest_bad:.2f}–{lowest_good:.2f})")
    if abs(suggested - guardrails.MIN_GROUNDING) > 0.05:
        print(f"Current value {guardrails.MIN_GROUNDING} is off; update "
              "app/guardrails.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
