"""Demo HTTP server - stdlib only, so `python server.py` is the whole setup.

Endpoints
    GET  /                     customer chat, agent console, knowledge base, settings
    GET  /api/health           model mode and knowledge-base stats
    POST /api/chat             {session_id?, message} -> assistant turn
    POST /api/tts              {text} -> audio bytes (direct TTS)
    POST /api/stt              {audio_b64} -> {text} transcript (Vietnamese)
    GET  /api/tts/provider     current TTS provider status
    POST /api/tts/provider     {provider, voice?} -> configure TTS
    POST /api/session/raw-mode {session_id?, enabled} -> demo lever (staff):
                               strips routing/guardrails/retrieval/grounding
                               down to a plain LLM chat, scoped to one session
    GET  /api/demo-script      list scripts in data/demo_scripts.json (staff)
    POST /api/demo-script/start {session_id?, name} -> arm a script (staff):
                               forces the next replies verbatim, scoped to
                               one session, bypassing routing entirely
    POST /api/demo-script/stop {session_id} -> disarm it (staff)
    GET  /api/session/<id>     transcript + audit trail
    GET  /api/queue            escalated sessions waiting for a human
    POST /api/summary          {session_id} -> regenerate the handover brief
    GET  /api/kb               knowledge-base documents
    GET  /api/kb/doc?name=…    one document: raw text + parsed passages
    POST /api/kb/upload        {filename, content} -> add or replace, reindex
    POST /api/kb/delete        {filename} -> remove, reindex
    GET  /api/providers        provider catalogue (keys masked) + active config
    POST /api/providers        {provider?, api_key?, model?, effort?, base_url?}
    GET  /api/chat/poll        {session_id, since} -> new messages (public)
    POST /api/session/reply    {session_id, message} -> agent replies (staff)
    GET  /api/dashboard        operational metrics + queue + activity (staff)
    POST /api/replay           seed realistic traffic through the real router
    POST /api/sessions/clear   drop every conversation (staff)
    GET  /api/auth/me          current staff session, or null
    POST /api/auth/login       {username, password} -> sets the staff cookie
    POST /api/auth/logout      clears it
Two trust zones. `/`, `/api/health` and `/api/chat` are public - the customer
chat is a widget on a public page, and identity is established inside the
conversation only when an action needs it. Everything else is staff-only and
gated in the handler by `_require_staff()`, not in the UI: hiding a tab in
JavaScript is not a control, because these routes answer anyone who calls them.

Still binds to 127.0.0.1: the settings endpoint accepts an API key and the
knowledge-base endpoints decide what the assistant may claim, so neither should
be reachable from off-box even with the sign-in in place.
"""

from __future__ import annotations

import atexit
import base64
import hmac
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time

# Windows consoles default to cp1252, which cannot print the Vietnamese text
# this server logs at startup and the banner below - reconfigure before any
# such output happens.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Load .env BEFORE any app imports so env vars are set when modules initialize.
from dotenv import load_dotenv

load_dotenv()
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app import (
    agents,
    auth,
    db,
    demo_script,
    flows,
    llm,
    memory,
    metrics,
    policy,
    replay,
    stt,
    topics,
    tts,
    voice,
)
from app import campaigns as campaign_mod
from app.kbstore import KBError, KnowledgeBaseStore
from app.llm import runtime
from app.router import Router

WEB_DIR = Path(__file__).resolve().parent / "web"
HOST = "127.0.0.1"
PORT = 8000

MAX_BODY_BYTES = 1024 * 1024  # 1 MB – general JSON payloads
MAX_AUDIO_BYTES = 5 * 1024 * 1024  # 5 MB – base64-encoded audio for STT
MAX_TTS_CHARS = 1500

router = Router()
kb_store = KnowledgeBaseStore(router.kb)
auth_store = auth.AuthStore()

_CONTENT_TYPES = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "BankingChatbotDemo/0.1"

    # -- helpers ----------------------------------------------------------

    def _send_json(
        self, payload: dict, status: int = 200, set_cookie: str | None = None
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def _send_binary(self, data: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # -- authorisation ----------------------------------------------------

    def _staff(self):
        """The signed-in staff session, or None."""
        return auth_store.resolve(auth.parse_cookie(self.headers.get("Cookie")))

    def _require_staff(self) -> bool:
        if self._staff() is not None:
            return True
        self._send_json(
            {"error": "Staff sign-in required", "code": "unauthenticated"},
            status=401,
        )
        return False

    def _require_admin(self) -> bool:
        """Gate the configuration surfaces: only the workforce manager passes.

        A signed-in agent gets 403, not 401 - they are authenticated, just not
        authorised - so the client can tell "log in" apart from "you may not do
        this". Enforced here at the API, never only in the UI (auth.py:10)."""
        session = self._staff()
        if session is None:
            self._send_json(
                {"error": "Staff sign-in required", "code": "unauthenticated"},
                status=401,
            )
            return False
        if not auth.is_admin(session):
            self._send_json(
                {"error": "Chỉ admin mới được cấu hình", "code": "forbidden"},
                status=403,
            )
            return False
        return True

    def _require_voice_secret(self) -> bool:
        """Gate the voice-agent tool webhooks with a shared bearer secret.

        These are called by an *external* voice provider (ElevenLabs, etc.), not
        a signed-in browser, so cookie auth does not apply - the provider sends
        `Authorization: Bearer <VOICE_TOOLS_SECRET>`. If the env var is unset we
        allow the call (local PoC) but that MUST be set in production, or the
        bank's customer data is one guessed URL away."""
        secret = os.environ.get("VOICE_TOOLS_SECRET", "")
        if not secret:
            return True  # PoC/dev: no secret configured
        header = self.headers.get("Authorization", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        if token and hmac.compare_digest(token, secret):
            return True
        self._send_json({"error": "invalid voice tool secret"}, status=401)
        return False

    def _send_file(self, filename: str) -> None:
        path = WEB_DIR / filename
        if (
            filename not in {"index.html", "app.js", "styles.css", "favicon.svg"}
            or not path.exists()
        ):
            self._send_json({"error": "not found"}, status=404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type", f"{_CONTENT_TYPES[path.suffix]}; charset=utf-8"
        )
        self.send_header("Content-Length", str(len(body)))
        # Không cache file tĩnh: đây là demo đang sửa liên tục, cache khiến trình
        # duyệt phục vụ bản cũ sau khi đã đổi CSS/JS. no-store buộc tải lại mỗi lần.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self, max_bytes: int = MAX_BODY_BYTES) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        if length > max_bytes:
            raise ValueError(
                f"Request body is {length // 1024} KB; the limit is "
                f"{max_bytes // 1024} KB"
            )
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            raise ValueError("Request body is not valid JSON")
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    def log_message(self, fmt, *args):  # quieter console
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write(f"  {args[0]}\n")

    # -- routes -----------------------------------------------------------

    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path == "/":
            self._send_file("index.html")
        elif path in ("/app.js", "/styles.css", "/favicon.svg"):
            self._send_file(path.lstrip("/"))
        elif path == "/favicon.ico":
            # Trình duyệt tự đòi /favicon.ico; trỏ về favicon.svg để hết 404.
            self._send_file("favicon.svg")
        elif path == "/api/health":
            self._send_json(
                {
                    **llm.describe(),
                    "knowledge_base": router.kb.stats,
                    "tts_available": tts.available(),
                    "stt_available": stt.available(),
                }
            )
        elif path == "/api/tts/provider":
            self._send_json(tts.status())
        elif path == "/api/queue":
            if not self._require_staff():
                return
            self._send_json(
                {
                    "sessions": [
                        {
                            "session_id": s.session_id,
                            "customer_name": (s.customer or {}).get("name"),
                            "verified": s.verified,
                            "reason": s.escalation_reason,
                            "turns": len(s.audit),
                            "created_at": s.created_at,
                        }
                        for s in router.sessions.escalated_sessions()
                    ]
                }
            )
        elif path.startswith("/api/session/"):
            if not self._require_staff():
                return
            session = router.sessions.get(path.rsplit("/", 1)[-1])
            if not session:
                self._send_json({"error": "unknown session"}, status=404)
            else:
                self._send_json(session.to_dict())

        elif path == "/api/kb":
            if not self._require_staff():
                return
            self._send_json(
                {
                    "documents": [d.to_dict() for d in kb_store.list_documents()],
                    "stats": router.kb.stats,
                }
            )

        elif path == "/api/kb/doc":
            if not self._require_staff():
                return
            name = (parse_qs(urlparse(self.path).query).get("name") or [""])[0]
            try:
                self._send_json(kb_store.read_document(name))
            except KBError as exc:
                self._send_json({"error": str(exc)}, status=404)

        elif path == "/api/providers":
            if not self._require_admin():
                return
            self._send_json(
                {
                    "providers": runtime.describe_providers(),
                    "active": llm.describe(),
                }
            )

        elif path == "/api/skills":
            if not self._require_admin():
                return
            self._send_json({"skills": agents.SKILL_CATALOG})

        elif path == "/api/agents":
            if not self._require_admin():
                return
            self._send_json({"agents": [a.to_dict() for a in agents.store.list()]})

        elif path == "/api/voice/calls":
            if not self._require_admin():
                return
            self._send_json({"calls": voice.store.list()})

        elif path == "/api/chat/poll":
            query = parse_qs(urlparse(self.path).query)
            session = router.sessions.get((query.get("session_id") or [""])[0])
            if not session:
                self._send_json({"error": "unknown session"}, status=404)
                return
            try:
                since = int((query.get("since") or ["0"])[0])
            except ValueError:
                since = 0
            self._send_json(
                {
                    "messages": session.messages_since(since),
                    "total": len(session.messages),
                    "handled_by": session.handled_by,
                }
            )

        elif path == "/api/dashboard":
            if not self._require_staff():
                return
            sessions = list(router.sessions._sessions.values())
            self._send_json(
                {
                    "metrics": metrics.snapshot(sessions).to_dict(),
                    "activity": metrics.recent_activity(sessions),
                    "queue": [
                        {
                            "session_id": s.session_id,
                            "customer_name": (s.customer or {}).get("name"),
                            "verified": s.verified,
                            "reason": s.escalation_reason,
                            "turns": len(s.audit),
                            "handled_by": s.handled_by,
                            "created_at": s.created_at,
                        }
                        for s in router.sessions.escalated_sessions()
                    ],
                    "llm": llm.describe(),
                    "topics": topics.cluster_unresolved(sessions),
                    "policy": policy.reload().to_dict(),
                    "campaigns": campaign_mod.CampaignBook().stats,
                    "memory": memory.store.stats,
                    "router": metrics.router_comparison(sessions),
                    "database": db.stats(),
                    "card_events": db.card_events(limit=12),
                }
            )

        elif path == "/api/demo-script":
            if not self._require_staff():
                return
            self._send_json({"scripts": demo_script.list_scripts()})

        elif path == "/api/auth/me":
            staff = self._staff()
            self._send_json(
                {
                    "staff": (
                        {
                            "username": staff.user.username,
                            "display_name": staff.user.display_name,
                            "role": staff.user.role,
                        }
                        if staff
                        else None
                    )
                }
            )

        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path

        # Allow larger body for audio uploads
        body_limit = (
            MAX_AUDIO_BYTES
            if path in ("/api/stt", "/api/voice/stt")
            else MAX_BODY_BYTES
        )
        try:
            payload = self._read_json(max_bytes=body_limit)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return

        if path == "/api/auth/login":
            session = auth_store.login(
                payload.get("username", ""), payload.get("password", "")
            )
            if session is None:
                self._send_json(
                    {"error": "Tên đăng nhập hoặc mật khẩu không đúng"}, status=401
                )
                return
            self._send_json(
                {
                    "staff": {
                        "username": session.user.username,
                        "display_name": session.user.display_name,
                        "role": session.user.role,
                    }
                },
                set_cookie=auth.cookie_header(session.token),
            )

        elif path == "/api/auth/logout":
            auth_store.logout(auth.parse_cookie(self.headers.get("Cookie")))
            self._send_json({"staff": None}, set_cookie=auth.clear_cookie_header())

        elif path == "/api/tts/provider":
            provider = payload.get("provider", "").strip()
            if provider not in ("gtts", "ElevenLabs", "VieNeu"):
                self._send_json(
                    {"error": "provider phải là 'gtts', 'ElevenLabs' hoặc 'VieNeu'"},
                    status=400,
                )
                return
            tts.configure(
                provider=provider,
                voice=payload.get("voice"),
            )
            self._send_json(tts.status())

        elif path == "/api/tts":
            text = (payload.get("text") or "").strip()
            if not text:
                self._send_json({"error": "text is required"}, status=400)
                return
            if len(text) > MAX_TTS_CHARS:
                self._send_json(
                    {
                        "error": f"text quá dài ({len(text)} ký tự, tối đa {MAX_TTS_CHARS})"
                    },
                    status=400,
                )
                return
            if not tts.available():
                self._send_json({"error": "TTS không khả dụng"}, status=503)
                return
            try:
                audio_bytes = tts.synthesize(text)
                # Gắn ĐÚNG content-type theo byte thật: VieNeu trả WAV (RIFF),
                # gTTS/ElevenLabs trả mp3. Dán nhãn sai -> trình duyệt không phát.
                ctype = (
                    "audio/wav"
                    if audio_bytes[:4] == b"RIFF"
                    else tts.AUDIO_CONTENT_TYPE
                )
                self._send_binary(audio_bytes, ctype)
            except Exception as exc:
                self._send_json({"error": f"TTS thất bại: {exc}"}, status=500)

        elif path == "/api/stt":
            audio_b64 = (payload.get("audio_b64") or "").strip()
            if not audio_b64:
                self._send_json({"error": "audio_b64 is required"}, status=400)
                return
            if not stt.available():
                self._send_json({"error": "STT không khả dụng"}, status=503)
                return
            try:
                wav_bytes = base64.b64decode(audio_b64)
                if len(wav_bytes) > MAX_AUDIO_BYTES:
                    self._send_json({"error": "audio quá lớn"}, status=400)
                    return
                mime_type = (payload.get("mime_type") or "audio/wav").strip()
                text = stt.transcribe(wav_bytes, mime_type)
                self._send_json({"text": text})
            except Exception as exc:
                self._send_json({"error": f"STT thất bại: {exc}"}, status=500)

        elif path == "/api/session/reply":
            if not self._require_staff():
                return
            session = router.sessions.get(payload.get("session_id", ""))
            if not session:
                self._send_json({"error": "unknown session"}, status=404)
                return
            message = (payload.get("message") or "").strip()
            if not message:
                self._send_json({"error": "message is required"}, status=400)
                return
            router.agent_reply(session, message, self._staff().user)
            self._send_json(
                {
                    "handled_by": session.handled_by,
                    "transcript": session.transcript(),
                    "messages": session.messages_since(0),
                    "audit": [a.__dict__ for a in session.audit],
                }
            )

        elif path == "/api/sessions/clear":
            if not self._require_admin():
                return
            removed = router.sessions.clear()
            forgotten = memory.store.forget()
            db.reset()
            self._send_json(
                {"removed": removed, "forgotten": forgotten, "database": "reset"}
            )

        elif path == "/api/replay":
            if not self._require_staff():
                return
            self._send_json(replay.seed(router))

        elif path == "/api/kb/upload":
            if not self._require_admin():
                return
            try:
                if "file_base64" in payload:
                    data = kb_store.decode_base64(payload["file_base64"])
                    info = kb_store.save_upload(payload.get("filename", ""), data)
                else:
                    info = kb_store.save_document(
                        payload.get("filename", ""), payload.get("content", "")
                    )
            except KBError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json({"document": info.to_dict(), "stats": router.kb.stats})

        elif path == "/api/kb/delete":
            if not self._require_admin():
                return
            try:
                kb_store.delete_document(payload.get("filename", ""))
            except KBError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(
                {"deleted": payload.get("filename"), "stats": router.kb.stats}
            )

        elif path == "/api/providers":
            if not self._require_admin():
                return
            try:
                runtime.configure(
                    provider=payload.get("provider"),
                    api_key=payload.get("api_key"),
                    model=payload.get("model"),
                    effort=payload.get("effort"),
                    base_url=payload.get("base_url"),
                    tone=payload.get("tone"),
                    temperature=payload.get("temperature"),
                )
            except runtime.ConfigError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(
                {
                    "providers": runtime.describe_providers(),
                    "active": llm.describe(),
                }
            )

        elif path.startswith("/api/agents/"):
            if not self._require_admin():
                return
            agent_id = path.rsplit("/", 1)[-1]
            agent = agents.store.update(
                agent_id,
                enabled=payload.get("enabled"),
                enabled_skills=payload.get("enabled_skills"),
            )
            if agent is None:
                self._send_json({"error": "unknown agent"}, status=404)
                return
            self._send_json({"agent": agent.to_dict()})

        elif path == "/api/voice/call":
            # Admin đặt một cuộc gọi ra: nhập SĐT + chọn agent. PoC tạo bản ghi
            # nhật ký; nơi này là chỗ gọi Outbound API của nhà cung cấp thoại
            # (xem docs/ELEVENLABS-CALLBOT.md) khi đã tích hợp.
            if not self._require_admin():
                return
            phone = (payload.get("phone") or "").strip()
            agent_id = payload.get("agent_id") or ""
            if not phone or not agent_id:
                self._send_json({"error": "phone và agent_id là bắt buộc"}, status=400)
                return
            if agents.store.get(agent_id) is None:
                self._send_json({"error": "unknown agent"}, status=404)
                return
            record = voice.store.create(phone, agent_id)
            # TODO(production): trigger nhà cung cấp thoại ở đây, ví dụ ElevenLabs
            # Outbound Call API, rồi cập nhật record["status"] qua webhook.
            self._send_json({"call": record})

        elif path == "/api/voice/call/sim":
            # Cuộc gọi cross-sell MÔ PHỎNG cho demo: không quay số thật, mà mở màn
            # hình gọi ngay trên máy admin. Backend tạo một phiên chat mới, gieo
            # lời chào mở đầu (theo danh xưng + tên + dòng thẻ admin chọn) rồi trả
            # session_id để trình duyệt tiếp tục hội thoại qua /api/chat/stream.
            if not self._require_admin():
                return
            title = (payload.get("title") or "").strip()
            name = (payload.get("name") or "").strip()
            card_id = (payload.get("card_id") or "").strip()
            phone = (payload.get("phone") or "").strip()
            session = router.sessions.create()
            greeting = flows.outbound_greeting(title, name, card_id)
            session.add_message("assistant", greeting)
            # Luôn ghi nhật ký cuộc gọi (kể cả khi admin không nhập SĐT).
            voice.store.create(phone or f"(demo) {name}".strip(), card_id or "sim")
            self._send_json({"session_id": session.session_id, "greeting": greeting})

        elif path.startswith("/api/voice/tools/"):
            # Webhook cho voice agent gọi giữa cuộc trò chuyện. Xác thực bằng
            # bearer secret (VOICE_TOOLS_SECRET), KHÔNG dùng cookie đăng nhập.
            if not self._require_voice_secret():
                return
            tool_name = path.rsplit("/", 1)[-1]
            result, error = voice.call_tool(tool_name, payload)
            if error:
                self._send_json({"error": error}, status=400)
                return
            self._send_json(result)

        elif path.startswith("/api/voice/calls/"):
            # Nhà cung cấp thoại cập nhật trạng thái/transcript cuộc gọi về đây.
            if not self._require_voice_secret():
                return
            call_id = path.rsplit("/", 1)[-1]
            rec = voice.store.update(
                call_id,
                status=payload.get("status"),
                transcript=payload.get("transcript"),
                summary=payload.get("summary"),
            )
            if rec is None:
                self._send_json({"error": "unknown call"}, status=404)
                return
            self._send_json({"call": rec})

        elif path == "/api/session/raw-mode":
            if not self._require_staff():
                return
            session = router.sessions.get_or_create(payload.get("session_id"))
            session.raw_mode = bool(payload.get("enabled"))
            self._send_json(
                {
                    "session_id": session.session_id,
                    "raw_mode": session.raw_mode,
                }
            )

        elif path == "/api/demo-script/start":
            if not self._require_staff():
                return
            session = router.sessions.get_or_create(payload.get("session_id"))
            name = (payload.get("name") or "").strip()
            if not demo_script.start(session, name):
                self._send_json({"error": f"unknown script '{name}'"}, status=400)
                return
            self._send_json(
                {
                    "session_id": session.session_id,
                    "script_name": session.script_name,
                    "script_step": session.script_step,
                    "script_total": demo_script.step_count(name),
                }
            )

        elif path == "/api/demo-script/stop":
            if not self._require_staff():
                return
            session = router.sessions.get_or_create(payload.get("session_id"))
            demo_script.stop(session)
            self._send_json({"session_id": session.session_id, "script_name": None})

        elif path == "/api/chat/stream":
            message = (payload.get("message") or "").strip()
            if not message:
                self._send_json({"error": "message is required"}, status=400)
                return
            session = router.sessions.get_or_create(payload.get("session_id"))
            result = router.handle_turn(session, message)
            meta = {
                "session_id": session.session_id,
                "reply": result.text,
                "route": result.route,
                "intent": result.intent,
                "confidence": result.confidence,
                "sources": result.sources,
                "generated": result.generated,
                "grounding": result.grounding,
                "escalated": result.escalated,
                "escalation_reason": result.escalation_reason,
                "offer_escalation": result.offer_escalation,
                "trace": result.trace,
                "with_agent": result.with_agent,
                "handled_by": session.handled_by,
                "in_handoff": bool(session.escalated or session.handled_by),
                "latency_ms": result.latency_ms,
                "verified": session.verified,
                "raw_mode": session.raw_mode,
                "script_name": session.script_name,
                "script_step": session.script_step,
                "script_total": (
                    demo_script.step_count(session.script_name)
                    if session.script_name
                    else 0
                ),
                "debug": result.debug,
            }
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            def _sse(data: dict) -> bytes:
                return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode(
                    "utf-8"
                )

            try:
                if result.route != "agent" and result.text:
                    words = result.text.split(" ")
                    for i, word in enumerate(words):
                        token = word if i == 0 else " " + word
                        self.wfile.write(_sse({"token": token}))
                        self.wfile.flush()
                        time.sleep(0.06)
                self.wfile.write(_sse({"meta": meta}))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        elif path == "/api/chat":
            message = (payload.get("message") or "").strip()
            if not message:
                self._send_json({"error": "message is required"}, status=400)
                return
            session = router.sessions.get_or_create(payload.get("session_id"))
            result = router.handle_turn(session, message)
            self._send_json(
                {
                    "session_id": session.session_id,
                    "reply": result.text,
                    "route": result.route,
                    "intent": result.intent,
                    "confidence": result.confidence,
                    "sources": result.sources,
                    "generated": result.generated,
                    "grounding": result.grounding,
                    "escalated": result.escalated,
                    "escalation_reason": result.escalation_reason,
                    "offer_escalation": result.offer_escalation,
                    "trace": result.trace,
                    "with_agent": result.with_agent,
                    "handled_by": session.handled_by,
                    "in_handoff": bool(session.escalated or session.handled_by),
                    "latency_ms": result.latency_ms,
                    "verified": session.verified,
                    "raw_mode": session.raw_mode,
                    "script_name": session.script_name,
                    "script_step": session.script_step,
                    "script_total": (
                        demo_script.step_count(session.script_name)
                        if session.script_name
                        else 0
                    ),
                    "debug": result.debug,
                }
            )

        elif path == "/api/summary":
            if not self._require_staff():
                return
            session = router.sessions.get(payload.get("session_id", ""))
            if not session:
                self._send_json({"error": "unknown session"}, status=404)
                return
            summary = router.build_summary(session)
            session.escalation_summary = summary.text
            self._send_json(
                {
                    "summary": summary.text,
                    "generated": summary.generated,
                    "model": summary.model,
                    "handled_by": session.handled_by,
                    "transcript": session.transcript(),
                    "audit": [a.__dict__ for a in session.audit],
                }
            )

        else:
            self._send_json({"error": "not found"}, status=404)


import logging as _logging

_logging.basicConfig(level=_logging.INFO, format="%(name)s %(levelname)s %(message)s")


# --- VieNeu-TTS: tự spawn service giọng nói local (cổng 8080) cùng chatbot ---
# Fail-safe: thiếu thư mục/'uv'/cổng bận đều chỉ log rồi bỏ qua (chatbot vẫn chạy).
_vieneu_proc: subprocess.Popen | None = None


def _vieneu_url_base() -> str:
    """Gốc `http://host:port` của VieNeu, suy từ VIENEU_URL."""
    url = os.environ.get("VIENEU_URL", "http://127.0.0.1:8080/v1/audio/speech")
    u = urlparse(url)
    return f"{u.scheme}://{u.hostname}:{u.port or 8080}"


def _vieneu_port() -> int:
    """Cổng VieNeu, suy từ VIENEU_URL để luôn khớp với chỗ tts.py gọi tới."""
    try:
        return (
            urlparse(
                os.environ.get("VIENEU_URL", "http://127.0.0.1:8080/v1/audio/speech")
            ).port
            or 8080
        )
    except Exception:
        return 8080


# Giọng clone "Giang" chỉ sống trong RAM VieNeu (mất khi restart) -> mỗi lần VieNeu
# lên phải enroll lại từ clip tham chiếu. Đổi tên/clip qua env.
_VIENEU_CLONE_NAME = os.environ.get("VIENEU_CLONE_NAME", "Giang")
_VIENEU_CLONE_CLIP = os.environ.get(
    "VIENEU_CLONE_CLIP",
    str(Path(__file__).resolve().parent / "data" / "giang-voice-ref.mp3"),
)


def _enroll_vieneu_voice() -> None:
    """Chờ VieNeu sẵn sàng rồi clone giọng tham chiếu (thread nền, fail-safe)."""
    clip = Path(_VIENEU_CLONE_CLIP)
    if not clip.exists():
        print(
            f"  VieNeu clone      : không thấy clip {clip.name} - bỏ qua "
            f"(chọn giọng preset trong Cấu hình → Giọng nói)"
        )
        return
    try:
        import requests
    except ImportError:
        print("  VieNeu clone      : thiếu 'requests' - bỏ qua clone giọng")
        return

    base = _vieneu_url_base()
    # Đợi service sẵn sàng (tải model lần đầu ~6-9s); tối đa ~90s rồi bỏ cuộc.
    for _ in range(90):
        try:
            if requests.get(f"{base}/health", timeout=1).status_code == 200:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        print("  VieNeu clone      : VieNeu chưa sẵn sàng kịp - bỏ qua clone")
        return

    try:
        with open(clip, "rb") as f:
            resp = requests.post(
                f"{base}/v1/voices",
                data={"name": _VIENEU_CLONE_NAME, "denoise": "true"},
                files={"file": (clip.name, f, "audio/mpeg")},
                timeout=60,
            )
        if resp.status_code == 200:
            print(
                f"  VieNeu clone      : đã nạp giọng '{_VIENEU_CLONE_NAME}' từ {clip.name}"
            )
        else:
            print(f"  VieNeu clone      : nạp giọng thất bại (HTTP {resp.status_code})")
    except Exception as exc:
        print(f"  VieNeu clone      : nạp giọng lỗi ({exc})")


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _stop_vieneu() -> None:
    """Tắt tiến trình con VieNeu khi chatbot thoát (đăng ký qua atexit)."""
    global _vieneu_proc
    if _vieneu_proc and _vieneu_proc.poll() is None:
        _vieneu_proc.terminate()
        try:
            _vieneu_proc.wait(timeout=5)
        except Exception:
            _vieneu_proc.kill()
    _vieneu_proc = None


def _maybe_start_vieneu() -> None:
    """Best-effort spawn VieNeu-TTS. Tắt bằng VIENEU_AUTOSTART=0."""
    global _vieneu_proc
    if os.environ.get("VIENEU_AUTOSTART", "1").lower() not in ("1", "true", "yes"):
        print("  VieNeu            : tự khởi động đã tắt (VIENEU_AUTOSTART=0)")
        return

    port = _vieneu_port()
    if _port_in_use(port):
        print(f"  VieNeu            : đã chạy sẵn ở cổng {port}")
        # Có thể là instance mới chưa có giọng clone -> vẫn enroll cho chắc.
        _enroll_vieneu_async()
        return

    vieneu_dir = Path(__file__).resolve().parent.parent / "VieNeu-TTS"
    if not (vieneu_dir / "apps" / "openai_speech.py").exists():
        print(f"  VieNeu            : không tìm thấy tại {vieneu_dir} - bỏ qua")
        return

    uv = shutil.which("uv")
    if not uv:
        print("  VieNeu            : chưa cài 'uv' - bỏ qua (cài uv để tự chạy VieNeu)")
        return

    # VieNeu đọc env PORT (mặc định 8000 -> đụng chatbot) nên ép PORT cho tiến trình con.
    child_env = dict(os.environ, PORT=str(port))
    log_path = vieneu_dir / "vieneu-service.log"
    try:
        logfile = open(log_path, "ab")
        _vieneu_proc = subprocess.Popen(
            [uv, "run", "python", "-m", "apps.openai_speech"],
            cwd=str(vieneu_dir),
            env=child_env,
            stdout=logfile,
            stderr=subprocess.STDOUT,
        )
    except Exception as exc:
        print(f"  VieNeu            : không khởi động được ({exc})")
        return

    atexit.register(_stop_vieneu)
    print(
        f"  VieNeu            : đang khởi động ở cổng {port} "
        f"(pid {_vieneu_proc.pid}; log: {log_path.name}; tải model lần đầu hơi lâu)"
    )
    _enroll_vieneu_async()  # nạp giọng clone khi service sẵn sàng (thread nền)


def _enroll_vieneu_async() -> None:
    """Chạy _enroll_vieneu_voice trong thread nền để không chặn khởi động."""
    threading.Thread(target=_enroll_vieneu_voice, daemon=True).start()


def main() -> None:
    info = llm.describe()
    stats = router.kb.stats
    print("Ngân hàng ABC – Demo trợ lý lai")
    if info["mode"] == "live":
        endpoint = f" → {info['endpoint']}" if info.get("endpoint") else ""
        print(f"  Tầng sinh thành   : LIVE qua {info['provider']}{endpoint}")
        print(f"  Model             : {info['model']} (effort={info['effort']})")
    else:
        print("  Tầng sinh thành   : OFFLINE (fallback trích xuất)")
        print(f"  Lý do             : {info['detail']}")
    print(
        f"  Cơ sở tri thức    : {stats['passages']} đoạn văn "
        f"trong {stats['documents']} tài liệu"
    )

    # Tự khởi động service VieNeu (nếu có) - chạy nền, warm-up song song.
    _maybe_start_vieneu()

    # Mở ngrok để có URL công khai (dùng để chia sẻ demo hoặc cho webhook của
    # callbot voice - xem docs/ELEVENLABS-CALLBOT.md). Không bắt buộc: nếu ngrok
    # không khả dụng thì server vẫn chạy bình thường ở localhost.
    try:
        import time as _time

        import pyngrok.ngrok as _ngrok

        # Dọn tunnel cũ qua API trước khi kill agent, tránh trùng.
        try:
            for t in _ngrok.get_tunnels():
                _ngrok.disconnect(t.public_url)
        except Exception:
            pass
        _ngrok.kill()
        _time.sleep(1)
        tunnel = _ngrok.connect(PORT, "http", pooling_enabled=True)
        public_url = tunnel.public_url
        print(f"  URL công khai     : {public_url}")
    except Exception as exc:
        print(f"  ngrok             : không khả dụng ({exc})")

    # Pre-warm TTS and STT so the first customer reply isn't the one that pays
    # the provider's cold-start latency.
    def _warmup_tts():
        if tts.available():
            ok = tts.warmup()
            print(
                f"  TTS               : {'sẵn sàng' if ok else 'cảnh báo - warmup thất bại'}"
            )
        else:
            print(
                "  TTS               : không khả dụng (chạy setup_voice.py + pip install piper-tts)"
            )

    def _warmup_stt():
        if stt.available():
            ok = stt.warmup()
            print(
                f"  STT               : {'sẵn sàng' if ok else 'cảnh báo - warmup thất bại'}"
            )
        else:
            print(
                "  STT               : không khả dụng (pip install transformers torch soundfile)"
            )

    httpd = ThreadingHTTPServer((HOST, PORT), Handler)

    print(f"  Local             : http://{HOST}:{PORT}\n")

    t_tts = threading.Thread(target=_warmup_tts, daemon=True)
    t_stt = threading.Thread(target=_warmup_stt, daemon=True)
    t_tts.start()
    t_stt.start()

    httpd.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nĐã dừng.")
    finally:
        _stop_vieneu()  # tắt tiến trình con VieNeu nếu ta đã spawn
