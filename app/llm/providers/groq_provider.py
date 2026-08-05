"""Groq adapter.

Groq serves open-weight models behind an OpenAI-compatible Chat Completions
API, so this uses the `openai` SDK pointed at Groq's host - no separate
package. It is a provider in its own right rather than a base-URL override on
the OpenAI adapter, so an operator can pick "groq" in the console, paste a
GROQ_API_KEY, and get a working default model without also knowing the host.

Why it is worth having: Groq's free tier is comfortable for a demo - at the
time of writing, 30 requests/min and 1,000/day on Llama 3.3 70B - which makes
it the cheapest way to exercise the full RAG path end to end.

Two behaviours differ from OpenAI itself and are handled below:

  * No `refusal` field. OpenAI reports a safety decline in a dedicated field
    that maps onto the `model_refusal` escalation; Groq returns the decline as
    ordinary assistant text, which is indistinguishable from an answer at this
    layer. The grounding gate becomes the only thing that catches it.
  * No `reasoning_effort` on the models offered here, so LLM_EFFORT is accepted
    and ignored rather than sent and rejected.
"""

from __future__ import annotations

import os

from ..base import LLMRequest, LLMResult, classify_provider_error

NAME = "groq"
# Groq speaks the OpenAI wire format, so the OpenAI SDK is the client.
PACKAGE = "openai"
DEFAULT_MODEL = "llama-3.3-70b-versatile"
ENV_KEYS = ("GROQ_API_KEY",)

BASE_URL = "https://api.groq.com/openai/v1"


def available() -> bool:
    if not any(os.environ.get(key) for key in ENV_KEYS):
        return False
    try:
        import openai  # noqa: F401
    except ImportError:
        return False
    return True


def model_name() -> str:
    return os.environ.get("LLM_MODEL", DEFAULT_MODEL)


def base_url() -> str:
    """Fixed - reported by /api/health so the endpoint is never a surprise."""
    return BASE_URL


def complete(request: LLMRequest, effort: str) -> LLMResult:
    from openai import OpenAI

    model = model_name()
    # api_key passed explicitly: the SDK would otherwise look for
    # OPENAI_API_KEY and quietly send an OpenAI key to Groq.
    client = OpenAI(
        api_key=next((os.environ[k] for k in ENV_KEYS if os.environ.get(k)), None),
        base_url=BASE_URL,
    )

    try:
        response = client.chat.completions.create(
            model=model,
            **({"temperature": request.temperature}
               if request.temperature is not None else {}),
            # Counts reasoning tokens too on models that reason, which is why
            # the budget in base.py is generous.
            max_completion_tokens=request.max_tokens,
            messages=[
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
        )
    except Exception as exc:  # network, auth, rate limit - degrade, don't crash
        return LLMResult(text="", generated=False, provider=NAME, model=model,
                         error=classify_provider_error(exc))

    choice = response.choices[0]
    text = (choice.message.content or "").strip()
    usage = response.usage
    return LLMResult(
        text=text,
        generated=bool(text),
        provider=NAME,
        model=getattr(response, "model", model),
        input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
        output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
        error="truncated" if choice.finish_reason == "length" else None,
    )
