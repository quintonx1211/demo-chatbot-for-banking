"""Multi-provider TTS - gTTS & Vbee AI.

Provider switch happens at runtime, no restart needed.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIO_CONTENT_TYPE = "audio/mpeg"

# Kept in sync with web/app.js::stripMarkdownForTts() by hand - same
# transformation, same order, because the two have to agree on what a reply
# sounds like. This copy is the one that actually matters: it runs inside
# cache_path_for()/synthesize_live() below, so it is the version that decides
# the cache key and what the provider is asked to say, regardless of whether
# the caller is a browser (which also strips client-side, redundantly but
# harmlessly), pregenerate_voice.py, or a future caller that sends raw
# canned_responses.py text straight through.
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
    # A sentence that already ended in punctuation before a paragraph break
    # (the common case) would otherwise pick up a doubled ".." or ".!" that
    # most TTS engines pause oddly on.
    text = re.sub(r"([.!?,;:])\.\s", r"\1 ", text)
    text = re.sub(r" {2,}", " ", text)
    text = _CMND_CCCD_RE.sub("chứng minh nhân dân hoặc căn cước công dân", text)
    text = _CCCD_CMND_RE.sub("căn cước công dân hoặc chứng minh nhân dân", text)
    text = _CMND_RE.sub("chứng minh nhân dân", text)
    text = _CCCD_RE.sub("căn cước công dân", text)
    return text.strip()

# Pre-generated audio for the fixed replies in app/canned_responses.py - see
# pregenerate_voice.py. Checked before any live provider call: the goal is
# that the *first* time a canned reply is spoken in a freshly started
# process is exactly as fast as the hundredth, not just every call after the
# first, which is all the in-memory cache below could ever give you.
VOICE_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "voice_cache"

# --- runtime state ---
_lock = threading.Lock()
_provider: str    = "vbee"
_vbee_key: str    = os.environ.get("VBEE_TTS_KEY", "")
_vbee_app_id: str = os.environ.get("VBEE_APP_ID", "")
# Giọng nữ Sài Gòn, "Mềm mại" - giọng miền Nam nhẹ nhàng theo yêu cầu.
_vbee_voice: str  = "sg_female_thaotrinh_full_44k-phg"

# --- Vbee callback state ---
_callback_url: str = ""          # set by server after ngrok is established
_cb_lock = threading.Lock()
_pending: dict[str, threading.Event] = {}   # request_id → event
_results: dict[str, str] = {}              # request_id → audio_link

# cache keyed by (provider, voice, text)
_cache: dict[tuple, bytes] = {}
_cache_lock = threading.Lock()

VBEE_VOICES = {
    "hn_female_ngochuyen_full_48k-fhg": "Nữ Hà Nội – Tự nhiên",
    "hn_female_hermer_stor_48k-fhg":    "Nữ Hà Nội – Chuyên nghiệp",
    "hn_female_lenka_stor_48k-phg":     "Nữ Hà Nội – Trẻ trung",
    "hn_male_minhquan_yt-stable":       "Nam Hà Nội – Ấm",
    "hn_male_manhdung_news_48k-fhg":    "Nam Hà Nội – Rõ ràng",
    "sg_female_tuongvy_call_44k-fhg":   "Nữ Sài Gòn – Năng động",
    "sg_female_thaotrinh_full_44k-phg": "Nữ Sài Gòn – Mềm mại",
    "sg_male_chidat_ebook_48k-phg":     "Nam Sài Gòn – Trầm ấm",
}


# ---------- public API ----------

def set_callback_url(url: str) -> None:
    """Called once by server after ngrok public URL is known."""
    global _callback_url
    _callback_url = url
    logger.info("Vbee callback URL: %s", url)


def receive_callback(raw: dict) -> None:
    """Called by server when Vbee POSTs to /api/vbee-callback."""
    result = raw.get("result") or raw
    request_id = result.get("request_id") or raw.get("request_id")
    audio_link = result.get("audio_link") or raw.get("audio_link")
    logger.info("Vbee callback: request_id=%s  audio_link=%s  keys=%s",
                request_id, bool(audio_link), list(raw.keys()))
    if not request_id:
        return
    with _cb_lock:
        if audio_link:
            _results[request_id] = audio_link
        ev = _pending.get(request_id)
    if ev:
        ev.set()


def configure(provider: str | None = None,
              vbee_voice: str | None = None) -> None:
    global _provider, _vbee_voice
    with _lock:
        if provider:
            _provider = provider
        if vbee_voice:
            _vbee_voice = vbee_voice


def status() -> dict:
    with _lock:
        return {
            "provider": _provider,
            "vbee_voice": _vbee_voice,
            "gtts_available": _gtts_available(),
            "vbee_available": bool(_vbee_key and _vbee_app_id),
            "voices": VBEE_VOICES,
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


def cache_path_for(text: str, provider: str | None = None, voice: str | None = None) -> Path:
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
        vox = voice or _vbee_voice
    clean = strip_markdown_for_tts(text)
    digest = hashlib.sha256(f"{prov}|{vox}|{clean}".encode("utf-8")).hexdigest()[:24]
    return VOICE_CACHE_DIR / f"{digest}.mp3"


def synthesize_live(text: str) -> bytes:
    """Call the configured provider directly - no cache, disk or memory.

    What pregenerate_voice.py calls to bake the disk cache in the first
    place; `synthesize()` below is what everything else should call.
    """
    with _lock:
        prov   = _provider
        vkey   = _vbee_key
        app_id = _vbee_app_id
        voice  = _vbee_voice

    clean = strip_markdown_for_tts(text)
    if prov == "vbee":
        if not vkey or not app_id:
            raise RuntimeError("Vbee API key và App ID chưa được đặt")
        return _call_vbee(clean, vkey, app_id, voice)
    return _call_gtts(clean)


def synthesize(text: str) -> bytes:
    with _lock:
        prov   = _provider
        voice  = _vbee_voice

    # A named, hand-placed recording for one of the fixed replies always wins
    # and is served as-is - no Markdown stripping, no hashing, no live call.
    # It is that exact text's audio by construction (that is what "named"
    # means here), so there is nothing left to process.
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

    audio = synthesize_live(text)

    with _cache_lock:
        _cache[cache_key] = audio
    return audio


def available() -> bool:
    with _lock:
        prov = _provider
        vkey = _vbee_key
    if prov == "vbee":
        return bool(vkey)
    return _gtts_available()


def warmup() -> bool:
    try:
        synthesize("Xin chào.")
        return True
    except Exception as exc:
        logger.warning("TTS warmup thất bại: %s", exc)
        return False


# ---------- gTTS ----------

def _gtts_available() -> bool:
    try:
        import gtts  # noqa: F401
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


# ---------- Vbee ----------

_VBEE_TTS_URL = "https://vbee.vn/api/v1/tts"


def _call_vbee(text: str, api_key: str, app_id: str, voice: str) -> bytes:
    cb_url = _callback_url or "https://httpbin.org/post"

    body = json.dumps({
        "input_text":  text,
        "voice_code":  voice,
        "audio_type":  "mp3",
        "speed_rate":  1,
        "appId":       app_id,
        "callbackUrl": cb_url,
    }).encode("utf-8")

    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {api_key}",
        "app-id":        app_id,
    }
    req = urllib.request.Request(_VBEE_TTS_URL, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Vbee HTTP {exc.code}: {exc.reason}")

    logger.info("Vbee submit response: %s", payload)
    result = payload.get("result") or {}

    # Some plans return audio immediately
    audio_link = result.get("audio_link")
    if audio_link:
        logger.info("Vbee: audio ready in submit response")
        with urllib.request.urlopen(audio_link, timeout=15) as ar:
            return ar.read()

    request_id = result.get("request_id")
    if not request_id:
        raise RuntimeError(f"Vbee không trả về request_id: {payload}")

    if not _callback_url:
        raise RuntimeError(
            "Vbee cần callback URL — ngrok chưa sẵn sàng hoặc không kết nối được"
        )

    # Atomically register the event AND check if the callback already arrived.
    # Vbee can fire the callback in the tiny window between when the HTTP submit
    # returns and here. If it does, receive_callback() stores audio_link in
    # _results but finds no entry in _pending, so ev.set() is never called.
    # By holding _cb_lock across both operations we close that race window.
    event = threading.Event()
    early_link = None
    with _cb_lock:
        if request_id in _results:
            early_link = _results.pop(request_id)
        else:
            _pending[request_id] = event

    if early_link:
        logger.info("Vbee: callback nhận trước khi chờ, request_id=%s", request_id)
        with urllib.request.urlopen(early_link, timeout=15) as ar:
            return ar.read()

    logger.info("Vbee request_id=%s  chờ callback tại %s", request_id, cb_url)
    try:
        if event.wait(timeout=30):
            with _cb_lock:
                link = _results.pop(request_id, None)
            if link:
                with urllib.request.urlopen(link, timeout=15) as ar:
                    return ar.read()
            raise RuntimeError("Vbee: callback nhận được nhưng không có audio_link")
    finally:
        with _cb_lock:
            _pending.pop(request_id, None)
            _results.pop(request_id, None)

    raise RuntimeError("Vbee: timeout chờ callback (30s)")
