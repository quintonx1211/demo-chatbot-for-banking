"""Generative layer - provider-agnostic public surface.

The rest of the application calls exactly two functions from here and never
learns which vendor answered. Selection is by environment:

    LLM_PROVIDER            anthropic | openai | gemini | auto   (default: auto)
    LLM_MODEL               override the provider's default model
    LLM_EFFORT              low | medium | high                  (default: low)
    LLM_REASONING_EFFORT    OpenAI only - set to send reasoning_effort at all

`auto` picks the first provider whose SDK is importable and whose API key is
present, in PROVIDER_ORDER. If none qualifies the layer runs in extractive
mode, returning verified knowledge-base text verbatim rather than generating.
"""

from __future__ import annotations

import os

from ..retriever import RetrievedPassage
from .base import (
    LLMRequest,
    LLMResult,
    build_answer_request,
    build_raw_request,
    build_summary_request,
    extractive_answer,
    extractive_summary,
)
from .providers import (
    anthropic_provider,
    gemini_provider,
    groq_provider,
    openai_provider,
)

PROVIDERS = {
    anthropic_provider.NAME: anthropic_provider,
    openai_provider.NAME: openai_provider,
    gemini_provider.NAME: gemini_provider,
    groq_provider.NAME: groq_provider,
}

# Order used when LLM_PROVIDER is unset or "auto". Groq sits last so adding it
# cannot change which provider an existing configuration selects.
PROVIDER_ORDER = (
    anthropic_provider, openai_provider, gemini_provider, groq_provider,
)


def _effort() -> str:
    return os.environ.get("LLM_EFFORT", "low").lower()


def active_provider():
    """The selected adapter, or None when the demo runs extractive.

    Resolved per call rather than cached at import so the mode reported by
    /api/health always reflects the current environment.
    """
    requested = os.environ.get("LLM_PROVIDER", "auto").lower()

    if requested in ("", "auto"):
        return next((p for p in PROVIDER_ORDER if p.available()), None)

    provider = PROVIDERS.get(requested)
    if provider is None:
        raise ValueError(
            f"Unknown LLM_PROVIDER {requested!r}. "
            f"Expected one of: {', '.join(PROVIDERS)}, auto."
        )
    # An explicit choice that isn't usable falls back to extractive rather than
    # crashing the server - but describe() reports why, so it isn't silent.
    return provider if provider.available() else None


def is_live() -> bool:
    return active_provider() is not None


def describe() -> dict:
    """Mode summary for /api/health and the startup banner."""
    requested = os.environ.get("LLM_PROVIDER", "auto").lower()
    provider = active_provider()
    if provider is None:
        return {
            "mode": "offline",
            "provider": None,
            "model": None,
            "effort": None,
            "requested": requested,
            "endpoint": None,
            "detail": _unavailable_reason(requested),
        }
    # An adapter pointed at a compatible gateway is still that adapter, but
    # reporting it as plain "openai" would be misleading in the health check
    # and on the UI pill - the traffic is going somewhere else entirely.
    endpoint = getattr(provider, "base_url", lambda: None)()
    return {
        "mode": "live",
        "provider": provider.NAME,
        "model": provider.model_name(),
        "effort": _effort(),
        "requested": requested,
        "endpoint": endpoint,
        "detail": f"via {endpoint}" if endpoint else None,
    }


def _provider_status(provider) -> str:
    """Why this specific provider is unusable. Distinguishing 'no key' from
    'no SDK' matters - they are different fixes, and a key set against a
    missing SDK looks like a working configuration until you read this."""
    if not any(os.environ.get(k) for k in provider.ENV_KEYS):
        return f"no API key ({' or '.join(provider.ENV_KEYS)})"
    return f"SDK not installed (pip install {provider.PACKAGE})"


def _unavailable_reason(requested: str) -> str:
    if requested in ("", "auto"):
        return "; ".join(
            f"{p.NAME}: {_provider_status(p)}" for p in PROVIDER_ORDER
        )
    provider = PROVIDERS.get(requested)
    if provider is None:
        return f"Unknown provider {requested!r}"
    return f"{requested}: {_provider_status(provider)}"


def _run(request: LLMRequest, fallback: str) -> LLMResult:
    """Dispatch to the active adapter, falling back to verified text on failure.

    Any adapter failure - transport error, refusal, empty output - degrades to
    the extractive text rather than surfacing an error to the customer. The
    reason is preserved on the result so the router can escalate and the audit
    trail records what happened.
    """
    provider = active_provider()
    if provider is None:
        return LLMResult(text=fallback, generated=False)

    result = provider.complete(request, _effort())
    if not result.text.strip():
        result.text = fallback
        result.generated = False
    return result


def answer_from_kb(
    question: str,
    passages: list[RetrievedPassage],
    history: str = "",
) -> LLMResult:
    return _run(
        build_answer_request(question, passages, history=history),
        fallback=extractive_answer(passages),
    )


def raw_chat(message: str, history: str = "") -> LLMResult:
    """Answer with no routing, no knowledge base, no grounding check - just the
    model and the conversation so far. The baseline the rest of the router
    exists to improve on; see Router._raw_turn."""
    return _run(
        build_raw_request(message, history=history),
        # Reused whether there is no provider at all or a configured one just
        # failed (rate limit, timeout, ...) - the two aren't distinguished
        # here, only in `error`, which the routing inspector shows separately.
        fallback="(No answer came back - see the Note in the routing "
                 "inspector for why, or check Settings for a provider.)",
    )


def summarize_for_agent(transcript: str, context_lines: list[str]) -> LLMResult:
    return _run(
        build_summary_request(transcript, context_lines),
        fallback=extractive_summary(transcript, context_lines),
    )


__all__ = [
    "LLMResult", "answer_from_kb", "raw_chat", "summarize_for_agent",
    "is_live", "describe", "active_provider", "PROVIDERS",
]
