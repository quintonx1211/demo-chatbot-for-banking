"""TTS đa nhà cung cấp – gTTS & Vbee AI.

Chuyển đổi runtime không cần restart.
"""
from __future__ import annotations

import io
import json
import logging
import os
import threading
import time
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

# cache keyed by (provider, voice, text)
_cache: dict[tuple, bytes] = {}
_cache_lock = threading.Lock()

VBEE_VOICES = {
    "hn_female_ngochuyen_full_48k-fhg": "Ngọc Huyền – nữ HN",
    "hn_female_hermer_stor_48k-fhg":    "Ngọc Lan – nữ HN",
    "hn_female_lenka_stor_48k-phg":     "Nguyệt Dương – nữ HN",
    "hn_male_minhquan_yt-stable":       "Minh Quân – nam HN",
    "hn_male_manhdung_news_48k-fhg":    "Mạnh Dũng – nam HN",
    "sg_female_tuongvy_call_44k-fhg":   "Tường Vy – nữ SG",
    "sg_female_thaotrinh_full_44k-phg": "Thảo Trinh – nữ SG",
    "sg_male_chidat_ebook_48k-phg":     "Chí Đạt – nam SG",
}


# ---------- public API ----------

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

_VBEE_TTS_URL  = "https://vbee.vn/api/v1/tts"
_VBEE_POLL_URL = "https://vbee.vn/api/v1/tts/{request_id}"


def _call_vbee(text: str, api_key: str, app_id: str, voice: str) -> bytes:
    # 1. Gửi yêu cầu TTS
    body = json.dumps({
        "input_text": text,
        "voice_code":  voice,
        "audio_type":  "mp3",
        "appId":       app_id,
        "callbackUrl": "https://httpbin.org/post",  # bắt buộc nhưng không dùng
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

    request_id = (payload.get("result") or {}).get("request_id")
    if not request_id:
        raise RuntimeError(f"Vbee không trả về request_id: {payload}")

    # 2. Poll cho đến khi progress == 100
    poll_url = _VBEE_POLL_URL.format(request_id=request_id)
    for _ in range(30):
        time.sleep(0.6)
        req2 = urllib.request.Request(poll_url, headers={k: v for k, v in headers.items() if k != "Content-Type"})
        with urllib.request.urlopen(req2, timeout=10) as r2:
            status = json.loads(r2.read())
        result = status.get("result") or {}
        if result.get("progress", 0) >= 100:
            audio_link = result.get("audio_link")
            if not audio_link:
                raise RuntimeError("Vbee: audio_link trống sau khi hoàn thành")
            with urllib.request.urlopen(audio_link, timeout=15) as ar:
                return ar.read()

    raise RuntimeError("Vbee: timeout chờ audio")
