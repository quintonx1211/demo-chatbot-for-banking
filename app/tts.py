"""Multi-provider TTS - gTTS & ElevenLabs.

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
_provider: str = os.environ.get("TTS_PROVIDER", "VieNeu")
_eleven_key: str = os.environ.get("ELEVENLABS_API_KEY", "")
# Mặc định VieNeu (Voice 1, local, tự bật cùng server). Nếu service chưa lên,
# synthesize() tự rớt về backup. Đổi qua Cấu hình → Giọng nói hoặc env TTS_PROVIDER.
_voice: str = os.environ.get("TTS_VOICE", "")
# eleven_multilingual_v2 đọc tiếng Việt tốt; đổi qua ELEVENLABS_MODEL nếu cần.
_eleven_model: str = os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")

# --- VieNeu-TTS (local, offline, tiếng Việt, chạy trên Mac M4) ---
# VieNeu chạy như một service HTTP tương thích OpenAI ở env `uv` riêng
# (`uv run python -m apps.openai_speech`). Đặt provider = "VieNeu" ở màn hình
# Cấu hình → Giọng nói để bot gọi service này sinh giọng. _voice là tên giọng
# preset (vd "Minh Quân Pro") hoặc giọng đã clone. URL trỏ tới cổng của service
# (mặc định 8080 để không đụng cổng 8000 của chatbot); đổi qua env nếu chạy cổng khác.
_vieneu_url: str = os.environ.get("VIENEU_URL", "http://127.0.0.1:8080/v1/audio/speech")
_vieneu_model: str = os.environ.get("VIENEU_MODEL", "vieneu-v3-turbo")

# cache keyed by (provider, voice, text)
_cache: dict[tuple, bytes] = {}
_cache_lock = threading.Lock()


# ---------- public API ----------


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
        elkey = _eleven_key
        vurl = _vieneu_url
    # Dropdown giọng: ElevenLabs lấy từ tài khoản, VieNeu lấy từ service local;
    # gTTS không có giọng để chọn.
    if prov == "ElevenLabs" and elkey:
        voices = _list_eleven_voices(elkey)
    elif prov == "VieNeu":
        voices = _list_vieneu_voices(vurl)
    else:
        voices = {}
    return {
        "provider": prov,
        "voice": voice,
        "gtts_available": _gtts_available(),
        "elevenlabs_available": bool(elkey),
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
        elkey = _eleven_key
        elmodel = _eleven_model
        vurl = _vieneu_url
        vmodel = _vieneu_model

    clean = strip_markdown_for_tts(text)
    if prov == "VieNeu":
        # Raise on failure (do NOT silently fall to gTTS here): synthesize()
        # catches it and uses the hand-recorded backup, then gTTS. That is the
        # "generator first, recording backup" order the deployment asked for.
        return _call_vieneu(clean, voice, vurl, vmodel)
    if prov == "ElevenLabs" and elkey:
        return _call_elevenlabs(clean, elkey, voice, elmodel)
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

    # Order of precedence differs by provider:
    #   VieNeu  -> the live generator is primary; a hand recording is only a
    #              backup used when the VieNeu service is unreachable (the
    #              "generator first, recording backup" setup requested).
    #   others  -> a hand-placed named recording wins outright, since for gTTS/
    #              ElevenLabs the curated recording is the better audio.
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
        # Live generator failed (VieNeu service down, network, etc.). Fall back
        # to a hand-recorded backup for this exact reply if present, otherwise
        # gTTS - the bot must always have a voice.
        logger.warning("TTS live thất bại (%s) - dùng backup", exc)
        named = _named_backup(text)
        if named is not None:
            return named
        audio = _call_gtts(strip_markdown_for_tts(text))

    with _cache_lock:
        _cache[cache_key] = audio
    return audio


def available() -> bool:
    # ElevenLabs rơi về gTTS khi thiếu key, nên chỉ cần một trong hai khả dụng.
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


# ---------- VieNeu-TTS (local, OpenAI-compatible) ----------


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
    struct.pack_into("<I", out, 4, len(out) - 8)  # RIFF chunk size
    i = out.find(b"data", 12)
    if i != -1 and i + 8 <= len(out):
        struct.pack_into("<I", out, i + 4, len(out) - (i + 8))  # data chunk size
    return bytes(out)


def _call_vieneu(text: str, voice: str, url: str, model: str) -> bytes:
    """Synthesize via a local VieNeu-TTS service (OpenAI-compatible endpoint).

    POSTs to `/v1/audio/speech` and returns mp3 bytes. Runs fully offline on the
    box; the only failure modes are the service not being up yet or a bad voice
    name, both of which raise so synthesize() can fall back to a recording.
    """
    # VieNeu chỉ hỗ trợ 'wav'/'pcm', KHÔNG có 'mp3'. Dùng wav: trình duyệt phát
    # được qua thẻ <audio> (sniff header RIFF), chỉ tốn dung lượng hơn mp3.
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
        "Authorization": "Bearer local",  # bỏ qua bởi service local, gửi cho đủ
    }
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        # timeout rộng: lần gọi đầu tiên còn nạp model vào bộ nhớ.
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
    # OpenAI-style {"data": [{"id"/"name": ...}]} hoặc list phẳng.
    rows = data.get("data") if isinstance(data, dict) else data
    result: dict[str, str] = {}
    for v in rows or []:
        name = v.get("name") or v.get("id") if isinstance(v, dict) else str(v)
        if name:
            result[name] = name
    return result


# ---------- ElevenLabs ----------

_ELEVEN_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
_ELEVEN_VOICES_URL = "https://api.elevenlabs.io/v1/voices"

# Danh sách giọng lấy từ tài khoản chỉ đổi khi bạn thêm/xoá giọng trên
# ElevenLabs, nên cache lại thay vì gọi API mỗi lần mở màn hình Settings.
_eleven_voices_cache: dict[str, str] | None = None


def _voice_speaks_vietnamese(v: dict) -> bool:
    """Giọng có đọc được tiếng Việt không.

    ElevenLabs khai báo ngôn ngữ đã kiểm định trong verified_languages; giọng
    multilingual (eleven_multilingual_v2) vẫn đọc được tiếng Việt kể cả khi
    không liệt kê, nên đây chỉ dùng để *ưu tiên*, không phải để loại bỏ.
    """
    for lang in v.get("verified_languages") or []:
        code = (lang.get("language") or "").lower()
        if code in ("vi", "vie", "vietnamese"):
            return True
    labels = v.get("labels") or {}
    return "viet" in " ".join(str(x).lower() for x in labels.values())


def _list_eleven_voices(api_key: str) -> dict[str, str]:
    """{voice_id: tên hiển thị} từ tài khoản ElevenLabs, giọng tiếng Việt lên đầu.

    Trả về {} nếu không gọi được API - status() để dropdown giọng trống.
    """
    global _eleven_voices_cache
    if _eleven_voices_cache is not None:
        return _eleven_voices_cache
    try:
        req = urllib.request.Request(
            _ELEVEN_VOICES_URL, headers={"xi-api-key": api_key}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, ValueError) as exc:
        logger.warning("Không lấy được danh sách giọng ElevenLabs: %s", exc)
        return {}

    voices = data.get("voices") or []
    # Giọng tiếng Việt trước, trong mỗi nhóm giữ nguyên thứ tự tài khoản trả về.
    voices.sort(key=lambda v: not _voice_speaks_vietnamese(v))
    result: dict[str, str] = {}
    for v in voices:
        vid = v.get("voice_id")
        if not vid:
            continue
        name = v.get("name") or vid
        if _voice_speaks_vietnamese(v):
            name = f"{name} (Tiếng Việt)"
        result[vid] = name
    if result:
        _eleven_voices_cache = result
    return result


def _call_elevenlabs(text: str, api_key: str, voice: str, model: str) -> bytes:
    """Synchronous ElevenLabs TTS - returns mp3 bytes directly.

    A plain request/response with no callback, so it needs no public URL. The
    endpoint streams audio/mpeg back in the response body.
    """
    body = json.dumps(
        {
            "text": text,
            "model_id": model,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": AUDIO_CONTENT_TYPE,
        "xi-api-key": api_key,
    }
    url = _ELEVEN_TTS_URL.format(voice_id=voice)
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"ElevenLabs HTTP {exc.code}: {exc.reason} - {detail}")
