"""Same question, same model, with and without grounding.

The architecture's whole justification is that an ungrounded model will answer
a bank-policy question fluently and wrongly. That is easy to assert and hard to
feel. This runs both paths side by side so it can be seen instead:

  * **Ungrounded** - the model answers from whatever it absorbed in training,
    with a plain "you are a bank assistant" prompt and no passages.
  * **Grounded** - the production path: retrieve, answer only from what was
    retrieved, then check the answer stayed inside it.

One honest caveat, stated in the payload rather than hidden: the ungrounded
model sometimes declines to guess instead of inventing. That is a weaker demo
moment and it is not suppressed - a comparison rigged to always produce an
invention would prove nothing.
"""

from __future__ import annotations

from ..retriever import KnowledgeBase
from .base import LLMRequest

UNGROUNDED_SYSTEM_PROMPT = """Bạn là trợ lý chăm sóc khách hàng của một ngân hàng bán lẻ. \
Trả lời câu hỏi của khách hàng trực tiếp và hữu ích trong 2-4 câu. \
Cụ thể và rõ ràng - đưa ra con số, thời hạn và điều kiện khi liên quan, \
như một nhân viên am hiểu sẽ làm. Luôn trả lời bằng tiếng Việt."""

SUGGESTED = [
    "How much is the overdraft fee, and is there a daily cap?",
    "What does it cost to send an international wire, and how long does it take?",
    "How long do I have to dispute a transaction, and do I get provisional credit?",
    "What is the daily ATM withdrawal limit on a standard debit card?",
    "How long does a mortgage application take to decide?",
    "What fee do you charge for a replacement card?",
]


def compare(question: str, kb: KnowledgeBase) -> dict:
    """Run both paths and return them for side-by-side rendering."""
    from .. import guardrails
    from . import _effort, active_provider, answer_from_kb

    provider = active_provider()

    passages = kb.search(question, top_k=3)
    if passages:
        grounded_result = answer_from_kb(question, passages)
        context = " ".join(p.passage.text for p in passages)
        grounding = round(guardrails.grounding_score(grounded_result.text, context), 3)
        grounded = {
            "text": grounded_result.text,
            "generated": grounded_result.generated,
            "error": grounded_result.error,
            "grounding": grounding,
            "passed_gate": grounding >= guardrails.MIN_GROUNDING,
            "sources": [
                {
                    "citation": p.passage.citation,
                    "breadcrumb": p.passage.breadcrumb,
                    "score": p.score,
                }
                for p in passages
            ],
        }
    else:
        grounded = {
            "text": (
                "No verified passage covers this, so the assistant would "
                "escalate to a human rather than answer."
            ),
            "generated": False,
            "grounding": None,
            "passed_gate": False,
            "sources": [],
        }

    if provider is None:
        ungrounded = {
            "text": "",
            "generated": False,
            "error": "No LLM provider configured - sign in and set one in "
            "Settings to run this comparison.",
        }
    else:
        result = provider.complete(
            LLMRequest(system=UNGROUNDED_SYSTEM_PROMPT, user=question, max_tokens=1000),
            _effort(),
        )
        ungrounded = {
            "text": result.text,
            "generated": result.generated,
            "error": result.error,
        }
        if result.text:
            corpus = " ".join(p.text for p in kb.passages)
            ungrounded["overlap_with_corpus"] = round(
                guardrails.grounding_score(result.text, corpus), 3
            )

    both_ran = bool(grounded.get("generated")) and bool(ungrounded.get("generated"))
    blocked_by = (
        "no_provider"
        if provider is None
        else grounded.get("error") or ungrounded.get("error")
    )

    return {
        "question": question,
        "grounded": grounded,
        "ungrounded": ungrounded,
        "comparable": both_ran,
        "blocked_by": None if both_ran else blocked_by,
        "provider": provider.NAME if provider else None,
        "model": provider.model_name() if provider else None,
        "caveat": (
            "Both answers come from the same model, at the same settings. The "
            "only difference is that one was given the bank's verified passages "
            "and told to use nothing else. The ungrounded model sometimes "
            "declines to guess rather than inventing - that outcome is shown "
            "as-is rather than retried."
        ),
    }
