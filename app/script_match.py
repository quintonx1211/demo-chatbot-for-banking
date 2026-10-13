"""Fuzzy-match incoming user text against scripts.md customer lines.

When a user message closely resembles a "Khách hàng:" line in scripts.md,
return the corresponding "Bot:" reply verbatim.  Falls back to None so the
caller continues with normal routing.

Matching uses difflib SequenceMatcher (no extra deps) over normalised text —
strip diacritics is not needed because both sides are Vietnamese and the
comparison is symmetric.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

SCRIPTS_PATH = Path(__file__).resolve().parent.parent / "scripts.md"
MATCH_THRESHOLD = 0.55  # tweak if too strict / too loose

# Keyword patterns → pre-recorded voice cache filename.
# Ordered from most specific to least; first match wins.
_VOICE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"Số điện thoại đã khớp|nhập CCCD|CCCD.*12 số|12 số.*CCCD", re.IGNORECASE
        ),
        "VERIFICATION_ASK_CCCD.mp3",
    ),
    (
        re.compile(
            r"nhập SĐT|nhập số điện thoại|số điện thoại đã đăng ký", re.IGNORECASE
        ),
        "VERIFICATION_PROMPT.mp3",
    ),
    (
        re.compile(
            r"Xin chào.*trợ lý ảo|trợ lý ảo.*xin chào|hân hạnh được phục vụ",
            re.IGNORECASE,
        ),
        "GREETING.mp3",
    ),
    (
        re.compile(r"đóng thẻ.*hoàn tất|hệ thống hoàn tất xử lý", re.IGNORECASE),
        "CARD_CLOSE_CONFIRM_ASK.mp3",
    ),
]


def _voice_cache_for(bot_reply: str) -> str | None:
    """Return the voice cache filename that best fits this bot reply, or None."""
    for pattern, filename in _VOICE_PATTERNS:
        if pattern.search(bot_reply):
            return filename
    return None


def _normalise(text: str) -> str:
    """Lowercase, remove punctuation, collapse whitespace."""
    text = text.lower()
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_scripts() -> list[tuple[str, str]]:
    """Return [(customer_line, bot_reply), ...] from scripts.md."""
    if not SCRIPTS_PATH.exists():
        return []

    text = SCRIPTS_PATH.read_text(encoding="utf-8")
    # Strip markdown bold markers then split into blocks
    text = re.sub(r"\*\*", "", text)

    pairs: list[tuple[str, str]] = []
    # Walk line by line, collect Bot: lines after each Khách hàng: line
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m_user = re.match(r"Khách hàng:\s*(.+)", line)
        if m_user:
            user_text = m_user.group(1).strip()
            # Look ahead for the next Bot: line
            for j in range(i + 1, min(i + 5, len(lines))):
                m_bot = re.match(r"Bot:\s*(.+)", lines[j].strip())
                if m_bot:
                    bot_text = m_bot.group(1).strip()
                    pairs.append((user_text, bot_text))
                    break
        i += 1
    return pairs


# Cache parsed pairs for the process lifetime; reload when file changes.
_cache: list[tuple[str, str]] = []
_cache_mtime: float = 0.0


def _get_pairs() -> list[tuple[str, str]]:
    global _cache, _cache_mtime
    try:
        mtime = SCRIPTS_PATH.stat().st_mtime
    except FileNotFoundError:
        return []
    if mtime != _cache_mtime:
        _cache = _parse_scripts()
        _cache_mtime = mtime
    return _cache


def match(user_text: str) -> tuple[str, str | None] | None:
    """Return (bot_reply, voice_cache_filename) if user_text matches a script line.

    voice_cache_filename is a filename in data/voice_cache/ (e.g. "VERIFICATION_PROMPT.mp3")
    when a pre-recorded file exists for that reply, otherwise None.
    Returns None when no pair scores above MATCH_THRESHOLD.
    """
    pairs = _get_pairs()
    if not pairs:
        return None

    needle = _normalise(user_text)
    best_score = 0.0
    best_reply = None

    for customer_line, bot_reply in pairs:
        score = SequenceMatcher(
            None, needle, _normalise(customer_line), autojunk=False
        ).ratio()
        if score > best_score:
            best_score = score
            best_reply = bot_reply

    if best_score >= MATCH_THRESHOLD and best_reply is not None:
        return (best_reply, _voice_cache_for(best_reply))
    return None
