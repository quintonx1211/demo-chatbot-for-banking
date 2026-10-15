"""Multi-provider TTS - VieNeu (local) with a gTTS fallback.

Provider switch happens at runtime, no restart needed.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import struct
import threading
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIO_CONTENT_TYPE = "audio/mpeg"

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_BOLD_ITALIC_RE = re.compile(r"\*{3}([^*]+)\*{3}")
_MD_BOLD_RE = re.compile(r"\*{2}([^*]+)\*{2}")
_MD_ITALIC_RE = re.compile(r"\*([^*\n]+)\*")
_MD_UNDERLINE_BOLD_RE = re.compile(r"_{2}([^_]+)_{2}")
_MD_UNDERLINE_ITALIC_RE = re.compile(r"_([^_\n]+)_")
_MD_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_MD_HR_RE = re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE)
_MD_BLOCKQUOTE_RE = re.compile(r"^>\s*", re.MULTILINE)
_MD_BULLET_RE = re.compile(r"^[ \t]*[-*+•]\s+", re.MULTILINE)
_MD_ORDERED_RE = re.compile(r"^[ \t]*\d+[.)]\s+", re.MULTILINE)
_CMND_CCCD_RE = re.compile(r"CMND\s*/\s*CCCD", re.IGNORECASE)
_CCCD_CMND_RE = re.compile(r"CCCD\s*/\s*CMND", re.IGNORECASE)
_CMND_RE = re.compile(r"\bCMND\b")
_CCCD_RE = re.compile(r"\bCCCD\b")


def strip_markdown_for_tts(text: str) -> str:
    """Plain speech from a reply that may contain chat-display Markdown.

    Bold/italic/link/heading/list syntax read aloud as literal asterisks and
    hashes is a bug a customer hears, not a cosmetic one - this runs before
    every TTS call and every cache-key computation so it is not possible to
    bypass by calling a different entry point.
    """
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_BOLD_ITALIC_RE.sub(r"\1", text)
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_ITALIC_RE.sub(r"\1", text)
    text = _MD_UNDERLINE_BOLD_RE.sub(r"\1", text)
    text = _MD_UNDERLINE_ITALIC_RE.sub(r"\1", text)
    text = _MD_INLINE_CODE_RE.sub(r"\1", text)
    text = _MD_HEADING_RE.sub(r"\1", text)
    text = _MD_HR_RE.sub(".", text)
    text = _MD_BLOCKQUOTE_RE.sub("", text)
    text = _MD_BULLET_RE.sub("", text)
    text = _MD_ORDERED_RE.sub("", text)
    text = re.sub(r"\n{2,}", ". ", text)
    text = text.replace("\n", " ")
    text = re.sub(r"([.!?,;:])\.\s", r"\1 ", text)
    text = re.sub(r" {2,}", " ", text)
    text = _CMND_CCCD_RE.sub("chứng minh nhân dân hoặc căn cước công dân", text)
    text = _CCCD_CMND_RE.sub("căn cước công dân hoặc chứng minh nhân dân", text)
    text = _CMND_RE.sub("chứng minh nhân dân", text)
    text = _CCCD_RE.sub("căn cước công dân", text)
    return text.strip()


VOICE_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "voice_cache"

_lock = threading.Lock()
_provider: str = os.environ.get("TTS_PROVIDER", "VieNeu")
_voice: str = os.environ.get("TTS_VOICE", "")

_vieneu_url: str = os.environ.get("VIENEU_URL", "http://127.0.0.1:8080/v1/audio/speech")
_vieneu_model: str = os.environ.get("VIENEU_MODEL", "vieneu-v3-turbo")

_cache: dict[tuple, bytes] = {}
_cache_lock = threading.Lock()


def configure(provider: str | None = None, voice: str | None = None) -> None:
    global _provider, _voice
    with _lock:
        if provider:
            _provider = provider
        if voice:
            _voice = voice


def status() -> dict:
    with _lock:
        prov = _provider
        voice = _voice
        vurl = _vieneu_url
    voices = _list_vieneu_voices(vurl) if prov == "VieNeu" else {}
    return {
        "provider": prov,
        "voice": voice,
        "gtts_available": _gtts_available(),
        "vieneu_available": bool(prov == "VieNeu" and voices),
        "voices": voices,
    }


_canned_text_to_name_cache: dict[str, str] | None = None


def _canned_text_to_name() -> dict[str, str]:
    """Every canned reply's exact text -> its constant name in
    canned_responses.py, e.g. "Trợ lý ảo ABC Bank hân hạnh..." -> "GREETING".

    Imported lazily (not at module load) so a circular-import surprise in
    canned_responses.py can never take app/tts.py down with it - this module
    has to keep working even if that lookup fails. Computed once and cached:
    the constants don't change at runtime, and this is on the hot path of
    every synthesize() call.
    """
    global _canned_text_to_name_cache
    if _canned_text_to_name_cache is None:
        from . import canned_responses

        _canned_text_to_name_cache = {
            text: name for name, text in canned_responses.all_responses().items()
        }
    return _canned_text_to_name_cache


def named_cache_path(text: str) -> Path | None:
    """Where a manually-supplied recording for this exact canned reply would
    live - `data/voice_cache/<CONSTANT_NAME>.mp3` - or None if `text` is not
    one of the fixed replies in canned_responses.py.

    This is the path a human voice actor or a vendor delivery gets matched
    against: name the file after the constant, not a content hash, because a
    person naming files by hand needs a name they can read off
    canned_responses.py, not a hex digest they have to compute. Checked
    before the hash-based cache in `synthesize()`, and never regenerated by
    `pregenerate_voice.py` once present - a file placed here by hand is
    already the exact intended audio for that exact text, so re-synthesizing
    it would only replace a real recording with a lesser TTS one.
    """
    name = _canned_text_to_name().get(text)
    if name is None:
        return None
    return VOICE_CACHE_DIR / f"{name}.mp3"


def cache_path_for(
    text: str, provider: str | None = None, voice: str | None = None
) -> Path:
    """Where the pre-generated file for `text` would live, under the
    currently configured provider/voice unless overridden.

    Hashed rather than named after the reply (`GREETING.mp3`, say) because
    the text is the only thing that actually has to match at lookup time -
    keying on it directly means a reply that quietly changed wording in
    canned_responses.py can never silently serve stale, mismatched audio
    under an old filename; it just misses the cache and falls through to a
    live call until pregenerate_voice.py is run again.
    """
    with _lock:
        prov = provider or _provider
        vox = voice or _voice
    clean = strip_markdown_for_tts(text)
    digest = hashlib.sha256(f"{prov}|{vox}|{clean}".encode("utf-8")).hexdigest()[:24]
    return VOICE_CACHE_DIR / f"{digest}.mp3"


def synthesize_live(text: str) -> bytes:
    """Call the configured provider directly - no cache, disk or memory.

    What pregenerate_voice.py calls to bake the disk cache in the first
    place; `synthesize()` below is what everything else should call.
    """
    with _lock:
        prov = _provider
        voice = _voice
        vurl = _vieneu_url
        vmodel = _vieneu_model

    clean = strip_markdown_for_tts(text)
    if prov == "VieNeu":
        return _call_vieneu(clean, voice, vurl, vmodel)
    return _call_gtts(clean)


def _named_backup(text: str) -> bytes | None:
    """A hand-placed recording (`data/voice_cache/<CONSTANT>.mp3`) for this exact
    fixed reply, served as-is, or None if there is no recording for it."""
    cache_key = ("named", text)
    with _cache_lock:
        if cache_key in _cache:
            return _cache[cache_key]
    named_path = named_cache_path(text)
    if named_path is not None and named_path.exists():
        audio = named_path.read_bytes()
        with _cache_lock:
            _cache[cache_key] = audio
        return audio
    return None


def synthesize(text: str) -> bytes:
    with _lock:
        prov = _provider
        voice = _voice

    generator_first = prov == "VieNeu"
    if not generator_first:
        named = _named_backup(text)
        if named is not None:
            return named

    cache_key = (prov, voice, strip_markdown_for_tts(text))
    with _cache_lock:
        if cache_key in _cache:
            return _cache[cache_key]

    disk_path = cache_path_for(text, prov, voice)
    if disk_path.exists():
        audio = disk_path.read_bytes()
        with _cache_lock:
            _cache[cache_key] = audio
        return audio

    try:
        audio = synthesize_live(text)
    except Exception as exc:
        logger.warning("TTS live thất bại (%s) - dùng backup", exc)
        named = _named_backup(text)
        if named is not None:
            return named
        audio = _call_gtts(strip_markdown_for_tts(text))

    with _cache_lock:
        _cache[cache_key] = audio
    return audio


def available() -> bool:
    return _gtts_available()


def warmup() -> bool:
    try:
        synthesize("Xin chào.")
        return True
    except Exception as exc:
        logger.warning("TTS warmup thất bại: %s", exc)
        return False


def _gtts_available() -> bool:
    try:
        import gtts

        return True
    except ImportError:
        return False


def _call_gtts(text: str) -> bytes:
    try:
        from gtts import gTTS
    except ImportError:
        raise RuntimeError("gTTS chưa cài — chạy: pip install gtts")
    buf = io.BytesIO()
    gTTS(text=text, lang="vi", slow=False).write_to_fp(buf)
    return buf.getvalue()


def _vieneu_base(url: str) -> str:
    """The `/v1` base of the VieNeu service, derived from the speech URL, so the
    voices endpoint can be reached without a second env var."""
    return url.rsplit("/audio/speech", 1)[0]


def _fix_wav_header(b: bytes) -> bytes:
    """VieNeu trả WAV kiểu streaming: trường kích thước trong header là số
    placeholder khổng lồ (báo file dài ~44739 giây thay vì ~2 giây). Trình duyệt
    (nhất là Edge) đọc header đó -> renderer rối -> KHÔNG ra tiếng dù timeline
    chạy. Sửa lại RIFF-size và data-size cho đúng độ dài thật -> WAV hợp lệ, phát
    được ở mọi trình duyệt."""
    if len(b) < 44 or b[:4] != b"RIFF" or b[8:12] != b"WAVE":
        return b
    out = bytearray(b)
    struct.pack_into("<I", out, 4, len(out) - 8)
    i = out.find(b"data", 12)
    if i != -1 and i + 8 <= len(out):
        struct.pack_into("<I", out, i + 4, len(out) - (i + 8))
    return bytes(out)


def _call_vieneu(text: str, voice: str, url: str, model: str) -> bytes:
    """Synthesize via a local VieNeu-TTS service (OpenAI-compatible endpoint).

    POSTs to `/v1/audio/speech` and returns mp3 bytes. Runs fully offline on the
    box; the only failure modes are the service not being up yet or a bad voice
    name, both of which raise so synthesize() can fall back to a recording.
    """
    body = json.dumps(
        {
            "model": model,
            "voice": voice or "Ngọc Huyền",
            "input": text,
            "response_format": "wav",
        }
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "audio/wav",
        "Authorization": "Bearer local",
    }
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return _fix_wav_header(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"VieNeu HTTP {exc.code}: {exc.reason} - {detail}")


def _list_vieneu_voices(url: str) -> dict[str, str]:
    """{voice_name: nhãn hiển thị} lấy từ service VieNeu (GET /v1/voices).

    Trả về {} nếu service chưa chạy - màn hình Cấu hình để dropdown trống thay
    vì hỏng. KHÔNG cache: giọng clone được thêm vào lúc chạy, mà đây là gọi
    local nên rẻ - cache lại sẽ giấu mất giọng vừa clone.
    """
    try:
        req = urllib.request.Request(_vieneu_base(url) + "/voices")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, ValueError, OSError) as exc:
        logger.warning("Không lấy được danh sách giọng VieNeu: %s", exc)
        return {}
    rows = data.get("data") if isinstance(data, dict) else data
    result: dict[str, str] = {}
    for v in rows or []:
        name = v.get("name") or v.get("id") if isinstance(v, dict) else str(v)
        if name:
            result[name] = _VIENEU_VOICE_LABELS.get(name, name)
    return result


_VIENEU_VOICE_LABELS = {
    "Adam bựa": "Adam",
}
