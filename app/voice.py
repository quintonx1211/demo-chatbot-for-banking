"""Vbee TTS/STT adapter - voice out on every reply, voice in on demand.

TTS uses the Batch (async) API, not Realtime. Realtime looked like the
obvious choice for a demo - it returns audio in the same HTTP response, no
webhook needed - but the account this was built against returned
`BAD_REQUEST: "This feature is not supported in user package"` for every
Realtime call: it is a plan-gated feature, not something code can work
around. Batch is available on the base package, at the cost of needing a
`webhookUrl` Vbee can reach - see VBEE_PUBLIC_BASE_URL below.

Rather than build the receiving side of that webhook into a stateful
correlation dance (store by request id, wait for the callback, match it back
to the right browser request), `synthesize()` submits the job and then polls
Vbee's own "Get request" endpoint itself, synchronously, inside the HTTP
handler. The webhook still has to be a real, reachable URL - Vbee's API
requires the field - but nothing in this file depends on it actually being
called; `/api/voice/webhook/tts` in server.py exists to give Vbee somewhere
valid to deliver it, not because this code reads what arrives there. That
trade simplicity in this file for one operational cost: something has to
make 127.0.0.1 reachable from the internet for that URL to resolve at all -
ngrok or an equivalent tunnel, pointed at this server, with its public URL
set as VBEE_PUBLIC_BASE_URL.

STT stays on Realtime: the failure was specific to the TTS feature flag on
this account, and a short spoken clip does not need async at all. If STT
ever comes back with the same "not supported" error, the fix is the same
shape as this file's TTS path, not a different design.

  * STT Realtime caps a clip at 10 seconds / 10 MB and requires WAV. A
    browser's MediaRecorder does not produce WAV, so the client encodes PCM
    to a WAV container itself (`encodeWav` in app.js) rather than pulling in
    a conversion library for one format.

Configuration: VBEE_TOKEN and VBEE_APP_ID (an app created at
studio.vbee.vn/apps), following the same pattern as the LLM providers, plus
VBEE_PUBLIC_BASE_URL (e.g. `https://xxxx.ngrok-free.app`, no trailing slash)
for the webhook TTS needs. Missing VBEE_TOKEN/VBEE_APP_ID makes
`available()` False and the client hides voice entirely (see
/api/voice/status). Missing VBEE_PUBLIC_BASE_URL leaves STT working but TTS
returns "voice_no_public_url" - a narrower, later failure, because the
tunnel is only needed at the moment something is actually spoken.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

TTS_URL = "https://api.vbee.vn/v1/tts"
TTS_GET_URL = "https://api.vbee.vn/v1/tts/requests/{}"
STT_URL = "https://api.vbee.vn/v1/stt"

# One reviewed Vietnamese voice as the fixed default. The brief calls for
# voice on every reply, not a voice picker, so there is no selection UI and
# no reason to expose more than one code.
DEFAULT_VOICE = "hn_female_ngochuyen_full_48k-fhg"

# Batch's own ceiling is 100,000 characters - nothing a chat reply approaches,
# so unlike the Realtime path this file replaced, there is no chunking here.
_TIMEOUT_S = 15
_POLL_INTERVAL_S = 1.0
_POLL_TIMEOUT_S = 30.0


def available() -> bool:
    return bool(os.environ.get("VBEE_TOKEN")) and bool(os.environ.get("VBEE_APP_ID"))


def _webhook_url() -> str | None:
    base = (os.environ.get("VBEE_PUBLIC_BASE_URL") or "").rstrip("/")
    return f"{base}/api/voice/webhook/tts" if base else None


def _headers(content_type: str = "application/json") -> dict:
    return {
        "Authorization": f"Bearer {os.environ.get('VBEE_TOKEN', '')}",
        "App-Id": os.environ.get("VBEE_APP_ID", ""),
        "Content-Type": content_type,
    }


def _classify_error(exc: Exception) -> str:
    """A short, stable code - never the raw response body.

    /api/voice/tts and /api/voice/stt are public endpoints (same trust zone
    as /api/chat), so whatever lands in their error field is customer-
    visible. Vbee's error bodies are short JSON today, but trusting that to
    stay true is exactly the mistake already made once with the LLM
    providers (see app/llm/base.py classify_provider_error) - an
    organisation id or account detail in a future error message should not
    become a customer-facing leak just because nobody re-checked this file.

    The raw body is not discarded, only kept off the customer-facing
    response: it goes to stderr, which only the operator running
    `python server.py` sees. That is what surfaced the Realtime/plan issue
    this file exists to work around - losing that detail entirely would have
    left it looking like an unexplained 400 forever.
    """
    if isinstance(exc, urllib.error.HTTPError):
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        print(f"  [voice] Vbee HTTP {exc.code}: {detail}", file=sys.stderr)
        if exc.code == 401:
            return "voice_auth_error"
        if exc.code == 429:
            return "voice_rate_limited"
        if exc.code == 400:
            return "voice_bad_request"
        return f"voice_http_{exc.code}"
    if isinstance(exc, urllib.error.URLError):
        print(f"  [voice] unreachable: {exc}", file=sys.stderr)
        return "voice_unreachable"
    print(f"  [voice] {type(exc).__name__}: {exc}", file=sys.stderr)
    return "voice_error"


def _poll_until_done(request_id: str) -> tuple[str | None, str | None]:
    """Ask Vbee's Get Request endpoint until the job is COMPLETED or FAILED,
    or _POLL_TIMEOUT_S runs out. Returns (audio_link, error)."""
    deadline = time.monotonic() + _POLL_TIMEOUT_S
    url = TTS_GET_URL.format(request_id)
    while True:
        request = urllib.request.Request(url, headers=_headers(), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            return None, _classify_error(exc)

        status = payload.get("status")
        if status == "COMPLETED":
            return payload.get("audioLink"), None
        if status == "FAILED":
            print(f"  [voice] Vbee TTS job {request_id} failed: {payload}",
                  file=sys.stderr)
            return None, "voice_synthesis_failed"

        if time.monotonic() >= deadline:
            return None, "voice_timeout"
        time.sleep(_POLL_INTERVAL_S)


def synthesize(text: str, voice_code: str = DEFAULT_VOICE) -> tuple[list[bytes], str | None]:
    """The reply, spoken - one audio file, or (empty, error).

    Returns a one-element list on success so the response shape server.py
    sends matches what it always has (`chunks: [...]`) - the client's
    sequential-playback queue still works, it just ever has one item to play.
    """
    text = text.strip()
    if not text:
        return [], None

    webhook = _webhook_url()
    if not webhook:
        return [], "voice_no_public_url"

    submit_body = json.dumps({
        "text": text,
        "mode": "async",
        "voiceCode": voice_code,
        "webhookUrl": webhook,
        "outputFormat": "mp3",
        "bitrate": 128,
        "speed": 1.0,
    }).encode("utf-8")
    request = urllib.request.Request(
        TTS_URL, data=submit_body, headers=_headers(), method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
            submitted = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return [], _classify_error(exc)

    request_id = submitted.get("requestId")
    if not request_id:
        print(f"  [voice] TTS submit had no requestId: {submitted}", file=sys.stderr)
        return [], "voice_bad_response"

    audio_link, error = _poll_until_done(request_id)
    if error:
        return [], error

    try:
        with urllib.request.urlopen(audio_link, timeout=_TIMEOUT_S) as response:
            return [response.read()], None
    except Exception as exc:
        return [], _classify_error(exc)


def transcribe(wav_bytes: bytes) -> tuple[str | None, str | None]:
    """One short WAV clip -> text, or (None, reason).

    Length/size are enforced client-side before the recording is even sent
    (see MAX_RECORD_MS in app.js) - Vbee's own 10s/10MB ceiling is the
    backstop, not the primary control, because the customer should see
    "recording stopped" locally rather than wait on a network round trip
    just to be told the clip was too long.
    """
    boundary = uuid.uuid4().hex
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="mode"\r\n\r\nsync\r\n',
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="audioContent"; '
        b'filename="clip.wav"\r\nContent-Type: audio/wav\r\n\r\n',
        wav_bytes,
        f"\r\n--{boundary}--\r\n".encode(),
    ])

    headers = _headers(content_type=f"multipart/form-data; boundary={boundary}")
    request = urllib.request.Request(STT_URL, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except json.JSONDecodeError:
        return None, "voice_error"
    except Exception as exc:
        return None, _classify_error(exc)

    if "transcript" not in payload:
        return None, f"voice_status_{payload.get('status', 'unknown')}"
    return payload.get("transcript") or "", None
