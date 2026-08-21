"""OpenAI adapter - and, via OPENAI_BASE_URL, any OpenAI-compatible endpoint.

Uses Chat Completions, which is the most stable surface across model
generations and the one third-party providers implement. Two differences from
Claude worth knowing:

  * Prompt caching is automatic and prefix-based - there is no `cache_control`
    to declare, so the caching we do explicitly on the Claude path happens for
    free here (and cannot be steered).
  * `reasoning_effort` is only accepted by reasoning models; sending it to a
    non-reasoning model is an error. It is therefore opt-in via
    LLM_REASONING_EFFORT=1 rather than always-on.

Setting OPENAI_BASE_URL points this same adapter at a compatible gateway -
Groq, OpenRouter, a local llama.cpp or vLLM server - which is the cheapest way
to exercise the demo end to end. See README § Running the demo for free.
Compatibility is close but not total: `refusal` is an OpenAI field that most
gateways omit, so on those a safety block arrives as ordinary text and the
grounding gate becomes the only thing catching it.
"""

from __future__ import annotations

import os

from ..base import LLMRequest, LLMResult, classify_provider_error

NAME = "openai"
DEFAULT_MODEL = "gpt-4.1"
PACKAGE = "openai"
ENV_KEYS = ("OPENAI_API_KEY",)

_EFFORT = {"low": "low", "medium": "medium", "high": "high"}


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


def base_url() -> str | None:
    """Non-None when pointed at a compatible gateway rather than OpenAI itself."""
    return os.environ.get("OPENAI_BASE_URL") or None


def complete(request: LLMRequest, effort: str) -> LLMResult:
    from openai import OpenAI

    # The SDK reads OPENAI_BASE_URL itself, but passing it explicitly keeps the
    # decision visible here rather than hidden in SDK env handling.
    client = OpenAI(base_url=base_url()) if base_url() else OpenAI()
    model = model_name()

    if request.messages:
        msgs = [{"role": "system", "content": request.system}] + request.messages
    else:
        msgs = [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ]

    kwargs: dict = {
        "model": model,
        # Newer models reject `max_tokens` in favour of this; it also counts
        # reasoning tokens, which is why the budget in base.py is generous.
        "max_completion_tokens": request.max_tokens,
        "messages": msgs,
    }
    if os.environ.get("LLM_REASONING_EFFORT"):
        kwargs["reasoning_effort"] = _EFFORT.get(effort, "low")
    if request.temperature is not None:
        kwargs["temperature"] = request.temperature

    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:
        return LLMResult(text="", generated=False, provider=NAME, model=model,
                         error=classify_provider_error(exc))

    choice = response.choices[0]

    # OpenAI surfaces a safety decline as a populated `refusal` field on the
    # message, with `content` left null - a different shape from Claude's
    # stop_reason, which is exactly why refusal handling lives per-adapter.
    if getattr(choice.message, "refusal", None):
        return LLMResult(text="", generated=False, provider=NAME, model=model,
                         error="model_refusal")

    text = (choice.message.content or "").strip()
    usage = response.usage
    return LLMResult(
        text=text,
        generated=bool(text),
        provider=NAME,
        model=response.model,
        input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
        output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
        error="truncated" if choice.finish_reason == "length" else None,
    )
