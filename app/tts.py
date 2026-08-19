"""Multi-provider TTS - gTTS & Vbee AI.

Provider switch happens at runtime, no restart needed.
"""
from __future__ import annotations

import io
import json
import logging
import os
import threading
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

AUDIO_CONTENT_TYPE = "audio/mpeg"

# --- runtime state ---
_lock = threading.Lock()
_provider: str    = "vbee"
_vbee_key: str    = os.environ.get("VBEE_TTS_KEY", "")
_vbee_app_id: str = os.environ.get("VBEE_APP_ID", "")
_vbee_voice: str  = "hn_female_ngochuyen_full_48k-fhg"

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


def synthesize(text: str) -> bytes:
    with _lock:
        prov   = _provider
        vkey   = _vbee_key
        app_id = _vbee_app_id
        voice  = _vbee_voice

    cache_key = (prov, voice, text)
    with _cache_lock:
        if cache_key in _cache:
            return _cache[cache_key]

    if prov == "vbee":
        if not vkey or not app_id:
            raise RuntimeError("Vbee API key và App ID chưa được đặt")
        audio = _call_vbee(text, vkey, app_id, voice)
    else:
        audio = _call_gtts(text)

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

    # Register event BEFORE Vbee can fire the callback
    event = threading.Event()
    with _cb_lock:
        _pending[request_id] = event

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
