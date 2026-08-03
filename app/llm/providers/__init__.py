"""Provider adapters. Each exposes the same four names:

    NAME         short identifier used by LLM_PROVIDER
    ENV_KEYS     API-key environment variables, most preferred first
    available()  key present and SDK importable
    model_name() the model this adapter will call
    complete(request, effort) -> LLMResult
"""

from . import anthropic_provider, gemini_provider, groq_provider, openai_provider

__all__ = [
    "anthropic_provider", "openai_provider", "gemini_provider", "groq_provider",
]
