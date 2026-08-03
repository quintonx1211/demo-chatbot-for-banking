"""Runtime provider configuration for the operator console.

Lets an operator pick a provider and paste an API key without restarting the
server. Adapters read their configuration from the environment, so applying a
change means writing to `os.environ` - which keeps the adapters unchanged and
keeps the key in process memory only.

Security posture, and its limits:

  * A key is never written to disk and never leaves the process except as a
    mask. `describe_providers()` returns "sk-a…4f2c", never the key itself,
    and there is no endpoint that reads one back.
  * It is still plaintext in the process's environment, which means it is
    visible to anything that can read the process - other code in it, a core
    dump, a debugger, and any child process the server spawns.

That is acceptable for a localhost demo and is not a way to handle credentials
in production, where they belong in a secrets manager the app reads at start-up
with no UI path to set them. The console warns about this.
"""

from __future__ import annotations

import os

from . import PROVIDER_ORDER, PROVIDERS

# Settings the console may change. Anything not in here cannot be written
# through the API - the handler is not a general environment editor.
_WRITABLE = ("LLM_PROVIDER", "LLM_MODEL", "LLM_EFFORT", "OPENAI_BASE_URL")

VALID_EFFORTS = ("low", "medium", "high")


class ConfigError(ValueError):
    """Rejected operator input. The message is safe to show in the UI."""


def mask(secret: str | None) -> str | None:
    """Render a key for display: enough to recognise, not enough to use."""
    if not secret:
        return None
    if len(secret) <= 8:
        return "•" * len(secret)
    return f"{secret[:3]}…{secret[-4:]}"


def describe_providers() -> list[dict]:
    """Catalogue for the console - never includes a key, only its mask."""
    catalogue = []
    for provider in PROVIDER_ORDER:
        key_var = provider.ENV_KEYS[0]
        # Report whichever accepted variable actually holds a value, so a key
        # supplied as GOOGLE_API_KEY isn't shown as "not set" for Gemini.
        present = next((k for k in provider.ENV_KEYS if os.environ.get(k)), None)
        catalogue.append({
            "name": provider.NAME,
            "package": provider.PACKAGE,
            "env_key": key_var,
            "accepts": list(provider.ENV_KEYS),
            "default_model": provider.DEFAULT_MODEL,
            "sdk_installed": _sdk_installed(provider),
            "key_set": present is not None,
            "key_source": present,
            "key_masked": mask(os.environ.get(present)) if present else None,
            "available": provider.available(),
        })
    return catalogue


def _sdk_installed(provider) -> bool:
    """Whether the SDK imports, independent of whether a key is set.

    `provider.available()` conflates the two; the console needs to tell an
    operator "install the package" apart from "paste a key".
    """
    import importlib.util

    root = provider.PACKAGE.replace("-", "_").split("[")[0]
    # google-genai installs as the `google.genai` namespace package.
    module = "google.genai" if root == "google_genai" else root
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def configure(
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    base_url: str | None = None,
) -> None:
    """Apply console settings. Every field is optional; None means "leave alone".

    An empty string is meaningful and distinct from None: it clears the
    setting, which is how the console removes a stored key or reverts a model
    override back to the provider default.
    """
    if provider is not None:
        name = provider.strip().lower()
        if name not in PROVIDERS and name != "auto":
            raise ConfigError(
                f"Unknown provider {provider!r}. "
                f"Expected one of: {', '.join(PROVIDERS)}, auto."
            )
        os.environ["LLM_PROVIDER"] = name

    if effort is not None:
        level = effort.strip().lower()
        if level and level not in VALID_EFFORTS:
            raise ConfigError(
                f"Effort must be one of: {', '.join(VALID_EFFORTS)}")
        _set("LLM_EFFORT", level)

    if model is not None:
        _set("LLM_MODEL", model.strip())

    if base_url is not None:
        url = base_url.strip()
        if url and not url.startswith(("http://", "https://")):
            raise ConfigError("Base URL must start with http:// or https://")
        _set("OPENAI_BASE_URL", url)

    if api_key is not None:
        target = (os.environ.get("LLM_PROVIDER") or "auto").lower()
        if target == "auto":
            raise ConfigError(
                "Select a specific provider before setting a key - with 'auto' "
                "there is no way to tell which provider the key belongs to."
            )
        key_var = PROVIDERS[target].ENV_KEYS[0]
        stripped = api_key.strip()
        if stripped:
            os.environ[key_var] = stripped
        else:
            # Clearing must remove every accepted variable, or a key left in an
            # alternate one (GOOGLE_API_KEY) would keep the provider live and
            # the console would look like it had cleared something it hadn't.
            for var in PROVIDERS[target].ENV_KEYS:
                os.environ.pop(var, None)


def _set(name: str, value: str) -> None:
    if name not in _WRITABLE:  # pragma: no cover - guards future edits
        raise ConfigError(f"{name} is not settable at runtime")
    if value:
        os.environ[name] = value
    else:
        os.environ.pop(name, None)
