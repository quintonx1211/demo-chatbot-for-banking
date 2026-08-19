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

# Questions chosen because the answer is bank-specific policy: it cannot be
# derived, only looked up. A model with no source has nothing to do but invent
# something plausible, which is precisely the risk being demonstrated.
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
    from . import active_provider, _effort, answer_from_kb
    from .. import guardrails

    provider = active_provider()

    # --- grounded: the production path, unchanged ---
    passages = kb.search(question, top_k=3)
    if passages:
        grounded_result = answer_from_kb(question, passages)
        context = " ".join(p.passage.text for p in passages)
        grounding = round(
            guardrails.grounding_score(grounded_result.text, context), 3
        )
        grounded = {
            "text": grounded_result.text,
            "generated": grounded_result.generated,
            # Carried explicitly, because `answer_from_kb` degrades to
            # extractive text on any provider failure and returns something
            # that reads like a working answer. In the product that is the
            # right behaviour - the customer gets verified text instead of an
            # error. Here it is actively misleading: if the model was
            # unreachable, this panel would show a confident grounded column
            # beside an ungrounded column reading "provider_unreachable", and
            # look like proof that grounding won. It would be proof of nothing
            # - the model never ran on either side. The panel has to be able
            # to say so.
            "error": grounded_result.error,
            "grounding": grounding,
            "passed_gate": grounding >= guardrails.MIN_GROUNDING,
            "sources": [
                {"citation": p.passage.citation,
                 "breadcrumb": p.passage.breadcrumb,
                 "score": p.score}
                for p in passages
            ],
        }
    else:
        grounded = {
            "text": ("No verified passage covers this, so the assistant would "
                     "escalate to a human rather than answer."),
            "generated": False, "grounding": None,
            "passed_gate": False, "sources": [],
        }

    # --- ungrounded: same model, no passages, no gate ---
    if provider is None:
        ungrounded = {
            "text": "", "generated": False,
            "error": "No LLM provider configured - sign in and set one in "
                     "Settings to run this comparison.",
        }
    else:
        result = provider.complete(
            LLMRequest(system=UNGROUNDED_SYSTEM_PROMPT, user=question,
                       max_tokens=1000),
            _effort(),
        )
        ungrounded = {
            "text": result.text,
            "generated": result.generated,
            "error": result.error,
        }
        if result.text:
            # How much of the ungrounded answer is actually supported by the
            # bank's documentation. Not a verdict on truth - it cannot catch a
            # figure that happens to be right - but it does show, concretely,
            # how much of that fluent paragraph has nothing behind it.
            corpus = " ".join(p.text for p in kb.passages)
            ungrounded["overlap_with_corpus"] = round(
                guardrails.grounding_score(result.text, corpus), 3
            )

    # A comparison is only evidence when the same model answered both sides.
    # Anything else - no provider, a transport failure, a refusal - makes the
    # two columns incomparable, and the panel says so instead of letting the
    # viewer draw the flattering conclusion.
    both_ran = bool(grounded.get("generated")) and bool(ungrounded.get("generated"))
    # "No provider" is checked first and reported as a code, not as the prose
    # already sitting in `ungrounded["error"]` for that case - the UI phrases
    # it, and a sentence arriving where a reason code is expected reads as a
    # failure of the failure handling.
    blocked_by = ("no_provider" if provider is None
                  else grounded.get("error") or ungrounded.get("error"))

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
