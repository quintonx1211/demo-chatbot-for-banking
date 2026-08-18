"""Demo HTTP server - stdlib only, so `python server.py` is the whole setup.

Endpoints
    GET  /                     customer chat, agent console, knowledge base, settings
    GET  /api/health           model mode and knowledge-base stats
    POST /api/chat             {session_id?, message} -> assistant turn
    POST /api/tts              {text} -> audio bytes (direct TTS)
    POST /api/stt              {audio_b64} -> {text} transcript (Vietnamese)
    GET  /api/tts/provider     current TTS provider status
    POST /api/tts/provider     {provider, vbee_voice?} -> configure TTS
    POST /api/session/raw-mode {session_id?, enabled} -> demo lever (staff):
                               strips routing/guardrails/retrieval/grounding
                               down to a plain LLM chat, scoped to one session
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

import base64
import json
import os
import sys
import threading

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

import base64

from app import (auth, campaigns as campaign_mod, cards, db, llm, memory, metrics,
                 policy, replay, topics)
from app import tts, stt
from app.kbstore import KBError, KnowledgeBaseStore
from app.llm import runtime
from app.router import Router

WEB_DIR = Path(__file__).resolve().parent / "web"
HOST = "127.0.0.1"
PORT = 8000

MAX_BODY_BYTES = 1024 * 1024       # 1 MB – general JSON payloads
MAX_AUDIO_BYTES = 5 * 1024 * 1024  # 5 MB – base64-encoded audio for STT
MAX_TTS_CHARS = 1500

router = Router()
kb_store = KnowledgeBaseStore(router.kb)
auth_store = auth.AuthStore()

_CONTENT_TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}


class Handler(BaseHTTPRequestHandler):
    server_version = "BankingChatbotDemo/0.1"

    # -- helpers ----------------------------------------------------------

    def _send_json(self, payload: dict, status: int = 200,
                   set_cookie: str | None = None) -> None:
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

    def _send_file(self, filename: str) -> None:
        path = WEB_DIR / filename
        if filename not in {"index.html", "app.js", "styles.css"} or not path.exists():
            self._send_json({"error": "not found"}, status=404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",
                         f"{_CONTENT_TYPES[path.suffix]}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
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
        elif path in ("/app.js", "/styles.css"):
            self._send_file(path.lstrip("/"))
        elif path == "/api/health":
            self._send_json({
                **llm.describe(),
                "knowledge_base": router.kb.stats,
                "tts_available": tts.available(),
                "stt_available": stt.available(),
            })
        elif path == "/api/tts/provider":
            self._send_json(tts.status())
        elif path == "/api/queue":
            if not self._require_staff():
                return
            self._send_json({"sessions": [{
                "session_id": s.session_id,
                "customer_name": (s.customer or {}).get("name"),
                "verified": s.verified,
                "reason": s.escalation_reason,
                "turns": len(s.audit),
                "created_at": s.created_at,
            } for s in router.sessions.escalated_sessions()]})
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
            self._send_json({
                "documents": [d.to_dict() for d in kb_store.list_documents()],
                "stats": router.kb.stats,
            })

        elif path == "/api/kb/doc":
            if not self._require_staff():
                return
            name = (parse_qs(urlparse(self.path).query).get("name") or [""])[0]
            try:
                self._send_json(kb_store.read_document(name))
            except KBError as exc:
                self._send_json({"error": str(exc)}, status=404)

        elif path == "/api/providers":
            if not self._require_staff():
                return
            self._send_json({
                "providers": runtime.describe_providers(),
                "active": llm.describe(),
            })

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
            self._send_json({
                "messages": session.messages_since(since),
                "total": len(session.messages),
                "handled_by": session.handled_by,
            })

        elif path == "/api/dashboard":
            if not self._require_staff():
                return
            sessions = list(router.sessions._sessions.values())
            self._send_json({
                "metrics": metrics.snapshot(sessions).to_dict(),
                "activity": metrics.recent_activity(sessions),
                "queue": [{
                    "session_id": s.session_id,
                    "customer_name": (s.customer or {}).get("name"),
                    "verified": s.verified,
                    "reason": s.escalation_reason,
                    "turns": len(s.audit),
                    "handled_by": s.handled_by,
                    "created_at": s.created_at,
                } for s in router.sessions.escalated_sessions()],
                "llm": llm.describe(),
                "topics": topics.cluster_unresolved(sessions),
                "policy": policy.reload().to_dict(),
                "campaigns": campaign_mod.CampaignBook().stats,
                "memory": memory.store.stats,
                "router": metrics.router_comparison(sessions),
                "database": db.stats(),
            })

        elif path == "/api/auth/me":
            staff = self._staff()
            self._send_json({"staff": {
                "username": staff.user.username,
                "display_name": staff.user.display_name,
                "role": staff.user.role,
            } if staff else None})

        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path

        # Allow larger body for audio uploads
        body_limit = MAX_AUDIO_BYTES if path == "/api/stt" else MAX_BODY_BYTES
        try:
            payload = self._read_json(max_bytes=body_limit)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return

        if path == "/api/auth/login":
            session = auth_store.login(payload.get("username", ""),
                                       payload.get("password", ""))
            if session is None:
                self._send_json({"error": "Tên đăng nhập hoặc mật khẩu không đúng"},
                                status=401)
                return
            self._send_json(
                {"staff": {"username": session.user.username,
                           "display_name": session.user.display_name,
                           "role": session.user.role}},
                set_cookie=auth.cookie_header(session.token),
            )

        elif path == "/api/auth/logout":
            auth_store.logout(auth.parse_cookie(self.headers.get("Cookie")))
            self._send_json({"staff": None},
                            set_cookie=auth.clear_cookie_header())

        elif path == "/api/tts/provider":
            provider = payload.get("provider", "").strip()
            if provider not in ("gtts", "vbee"):
                self._send_json({"error": "provider phải là 'gtts' hoặc 'vbee'"}, status=400)
                return
            tts.configure(
                provider=provider,
                vbee_voice=payload.get("vbee_voice"),
            )
            self._send_json(tts.status())

        elif path == "/api/tts":
            text = (payload.get("text") or "").strip()
            if not text:
                self._send_json({"error": "text is required"}, status=400)
                return
            if len(text) > MAX_TTS_CHARS:
                self._send_json(
                    {"error": f"text quá dài ({len(text)} ký tự, tối đa {MAX_TTS_CHARS})"},
                    status=400,
                )
                return
            if not tts.available():
                self._send_json({"error": "TTS không khả dụng"}, status=503)
                return
            try:
                audio_bytes = tts.synthesize(text)
                self._send_binary(audio_bytes, tts.AUDIO_CONTENT_TYPE)
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
                text = stt.transcribe_wav(wav_bytes)
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
            self._send_json({
                "handled_by": session.handled_by,
                "transcript": session.transcript(),
                "messages": session.messages_since(0),
                "audit": [a.__dict__ for a in session.audit],
            })

        elif path == "/api/sessions/clear":
            if not self._require_staff():
                return
            removed = router.sessions.clear()
            forgotten = memory.store.forget()
            db.reset()
            cards.reset()
            self._send_json({"removed": removed, "forgotten": forgotten,
                             "database": "reset"})

        elif path == "/api/replay":
            if not self._require_staff():
                return
            self._send_json(replay.seed(router))

        elif path == "/api/kb/upload":
            if not self._require_staff():
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
            if not self._require_staff():
                return
            try:
                kb_store.delete_document(payload.get("filename", ""))
            except KBError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json({"deleted": payload.get("filename"),
                             "stats": router.kb.stats})

        elif path == "/api/providers":
            if not self._require_staff():
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
            self._send_json({
                "providers": runtime.describe_providers(),
                "active": llm.describe(),
            })

        elif path == "/api/session/raw-mode":
            if not self._require_staff():
                return
            session = router.sessions.get_or_create(payload.get("session_id"))
            session.raw_mode = bool(payload.get("enabled"))
            self._send_json({
                "session_id": session.session_id, "raw_mode": session.raw_mode,
            })

        elif path == "/api/chat":
            message = (payload.get("message") or "").strip()
            if not message:
                self._send_json({"error": "message is required"}, status=400)
                return
            session = router.sessions.get_or_create(payload.get("session_id"))
            result = router.handle_turn(session, message)
            self._send_json({
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
                "debug": result.debug,
            })

        elif path == "/api/summary":
            if not self._require_staff():
                return
            session = router.sessions.get(payload.get("session_id", ""))
            if not session:
                self._send_json({"error": "unknown session"}, status=404)
                return
            summary = router.build_summary(session)
            session.escalation_summary = summary.text
            self._send_json({
                "summary": summary.text,
                "generated": summary.generated,
                "model": summary.model,
                "handled_by": session.handled_by,
                "transcript": session.transcript(),
                "audit": [a.__dict__ for a in session.audit],
            })

        else:
            self._send_json({"error": "not found"}, status=404)


def main() -> None:
    info = llm.describe()
    stats = router.kb.stats
    print("ABC Bank - Virtual Assistant Demo")
    if info["mode"] == "live":
        endpoint = f" → {info['endpoint']}" if info.get("endpoint") else ""
        print(f"  Generation layer  : LIVE via {info['provider']}{endpoint}")
        print(f"  Model             : {info['model']} (effort={info['effort']})")
    else:
        print("  Generation layer  : OFFLINE (extractive fallback)")
        print(f"  Reason            : {info['detail']}")
    print(f"  Knowledge base    : {stats['passages']} passages "
          f"across {stats['documents']} documents")

    # Pre-warm TTS and STT in parallel background threads
    def _warmup_tts():
        if tts.available():
            ok = tts.warmup()
            print(f"  TTS               : {'ready' if ok else 'warning - warm up failed'}")
        else:
            print("  TTS               : not available (run setup_voice.py + pip install piper-tts)")

    def _warmup_stt():
        if stt.available():
            ok = stt.warmup()
            print(f"  STT               : {'ready' if ok else 'warning - warm up failed'}")
        else:
            print("  STT               : not available (pip install transformers torch soundfile)")

    t_tts = threading.Thread(target=_warmup_tts, daemon=True)
    t_stt = threading.Thread(target=_warmup_stt, daemon=True)
    t_tts.start()
    t_stt.start()

    # Start ngrok tunnel
    try:
        import time as _time
        import pyngrok.ngrok as _ngrok
        # Disconnect stale tunnels via the API before killing the agent
        try:
            for t in _ngrok.get_tunnels():
                _ngrok.disconnect(t.public_url)
        except Exception:
            pass
        _ngrok.kill()
        _time.sleep(1)
        tunnel = _ngrok.connect(PORT, "http", pooling_enabled=True)
        public_url = tunnel.public_url
        print(f"  Public URL     : {public_url}")
    except Exception as exc:
        print(f"  ngrok             : not available ({exc})")

    print(f"  Local             : http://{HOST}:{PORT}\n")

    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStop.")
