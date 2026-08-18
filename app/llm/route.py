"""LLM intent classification - a second opinion, and optionally the first.

Measured on the 70 labelled questions before this was written: 6% of failures
were routing errors and 31% were retrieval errors. That ratio is the whole
argument for how this module is scoped. An LLM reading the customer's sentence
can tell "thẻ của tôi bị mất tối qua giờ phải làm sao" (a policy question) from
"khoá thẻ giúp tôi" (an instruction), which the lexical classifier cannot. It
cannot do anything about a question whose words do not match the document that
answers it, because it never sees the documents - that is `rerank.py`'s job.

So this replaces one component, not the architecture:

  * It proposes an intent. It does not decide whether the intent is allowed,
    whether the customer is verified, or what the flow then does.
  * It runs AFTER the compliance guardrail, never before. Moving the guardrail
    behind a model call would turn a structural block into a prompt
    instruction, which is a different and much weaker guarantee - and it is
    the guarantee this whole product is sold on.
  * It never touches `rules_engine.py`. Whether a customer is eligible for a
    product stays deterministic code, because a model that can be talked into
    saying "yes, you qualify" is a vulnerability, and models can be talked
    into things.

Three modes, set by `router_mode` in config.json:

    nlu     - lexical only. The default, and the fallback whenever a provider
              is unavailable or the call fails.
    shadow  - both run, the LEXICAL result is used, both are written to the
              audit trail. This is how you get evidence before switching.
    llm     - the model decides, with the lexical classifier as fallback.

Shadow exists because "the LLM is obviously better" is a claim, and this
project has already been wrong about one of those (rank fusion, which measured
worse than the baseline it was meant to beat).
"""

from __future__ import annotations

import json
import os
import re

from .base import LLMRequest

# Kept in sync with app/nlu.INTENTS by `build_request`, so a new intent does
# not need to be added in two places.
SYSTEM_PROMPT = """You classify a Vietnamese bank customer's message into one \
intent. Reply with JSON only, no prose:

{"intent": "<name>", "confidence": <0.0-1.0>, "why": "<max 12 words>"}

Rules:
- Pick exactly one intent from the list given in the user message.
- Use "product_faq" for any question about products, fees, policy, \
timeframes or procedure - including questions that name a specific product. \
A question about HOW something works is product_faq, not a personal request.
- Use a personal intent (cross_sell_interest, card_close, card_limit_adjust, \
reward_inquiry) only when the customer is asking about THEIR OWN card or \
situation, not the product in general.
- Use "unknown" when nothing fits. Do not guess to avoid saying unknown.
- confidence is your own certainty. Below 0.55 the system will treat the turn \
as a knowledge question regardless of the intent you picked, so use a low \
number when you are unsure rather than picking a safer intent."""

# Small budget: the reply is one short JSON object. A large ceiling here would
# let a reasoning model spend the whole budget thinking and return nothing.
MAX_TOKENS = 300

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def mode() -> str:
    """Active router mode, from config with an env override for one-off runs."""
    from .. import policy
    override = (os.environ.get("LLM_ROUTER_MODE") or "").strip().lower()
    if override in ("nlu", "shadow", "llm"):
        return override
    return policy.current.router_mode


def build_request(message: str, intents: dict, history: str = "") -> LLMRequest:
    """One request describing the available intents and the customer's turn."""
    catalogue = "\n".join(
        f"- {name}: {spec['utterances'][0]}"
        for name, spec in intents.items()
    )
    history_block = f"Hội thoại trước đó:\n{history}\n\n" if history.strip() else ""
    return LLMRequest(
        system=SYSTEM_PROMPT,
        user=(f"{history_block}Intents:\n{catalogue}\n- unknown: none of these\n\n"
              f"Tin nhắn của khách: {message}"),
        max_tokens=MAX_TOKENS,
        # Classification is not a creative task, and a router that changes its
        # mind between identical turns cannot be audited.
        temperature=0.0,
    )


def classify(message: str, intents: dict, history: str = "") -> dict | None:
    """Ask the model for an intent. Returns None on any failure.

    None means "no opinion", and every caller treats that as a reason to use
    the lexical classifier. A router that raises, or that invents an intent
    when the provider is down, would take the assistant offline for a reason
    the customer cannot see.
    """
    from . import active_provider, _effort

    provider = active_provider()
    if provider is None:
        return None

    result = provider.complete(build_request(message, intents, history), _effort())
    if not result.text.strip():
        return {"intent": None, "error": result.error or "empty"}

    match = _JSON_RE.search(result.text)
    if not match:
        return {"intent": None, "error": "unparseable"}
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"intent": None, "error": "unparseable"}

    name = str(payload.get("intent") or "").strip()
    # An intent the model invented is not an intent. Silently accepting one
    # would route the turn to a flow that does not exist, or worse, to one
    # whose name happens to collide.
    if name not in intents and name != "unknown":
        return {"intent": None, "error": f"unknown_intent:{name[:24]}"}

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "intent": name,
        "confidence": max(0.0, min(1.0, confidence)),
        "why": str(payload.get("why", ""))[:80],
        "error": None,
        "tokens": result.input_tokens + result.output_tokens,
    }
