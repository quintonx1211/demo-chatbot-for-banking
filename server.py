"""Demo HTTP server - stdlib only, so `python server.py` is the whole setup.

Endpoints
    GET  /                     customer chat, agent console, knowledge base, settings
    GET  /api/health           model mode and knowledge-base stats
    POST /api/chat             {session_id?, message} -> assistant turn
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
    POST /api/compare          {question} -> grounded vs ungrounded answers
    GET  /api/compare/suggestions  questions where invention is likely
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

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app import (auth, campaigns as campaign_mod, llm, memory, metrics,
                 policy, replay, topics)
from app.kbstore import KBError, KnowledgeBaseStore
from app.llm import compare, runtime
from app.router import Router

WEB_DIR = Path(__file__).resolve().parent / "web"
HOST = "127.0.0.1"
PORT = 8000

# Requests bigger than this are refused before being read into memory.
MAX_BODY_BYTES = 1024 * 1024

router = Router()
kb_store = KnowledgeBaseStore(router.kb)
auth_store = auth.AuthStore()

_CONTENT_TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}


class Handler(BaseHTTPRequestHandler):
    server_version = "RegionalTrustDemo/0.1"

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

    # -- authorisation ----------------------------------------------------

    def _staff(self):
        """The signed-in staff session, or None."""
        return auth_store.resolve(auth.parse_cookie(self.headers.get("Cookie")))

    def _require_staff(self) -> bool:
        """Gate a privileged endpoint. Returns False once a 401 has been sent.

        Called at the top of every staff endpoint rather than being inferred
        from the UI, because the UI is not a security boundary — these routes
        answer anyone who calls them directly.
        """
        if self._staff() is not None:
            return True
        self._send_json(
            {"error": "Staff sign-in required", "code": "unauthenticated"},
            status=401,
        )
        return False

    def _send_file(self, filename: str) -> None:
        # Serve only known assets by exact name - no path joining from user input.
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

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        if length > MAX_BODY_BYTES:
            # Refuse on the declared length rather than reading it in first.
            raise ValueError(
                f"Request body is {length // 1024} KB; the limit is "
                f"{MAX_BODY_BYTES // 1024} KB"
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
            })
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
            # Public, and scoped by the session id the customer's browser
            # already holds. The response carries messages only — never the
            # audit trail or escalation reason, which are staff-facing.
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
            })

        elif path == "/api/compare/suggestions":
            self._send_json({"questions": compare.SUGGESTED})

        elif path == "/api/auth/me":
            # Unauthenticated by design: the UI calls this on load to decide
            # what to render, so it must answer "nobody" rather than 401.
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
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return

        if path == "/api/auth/login":
            session = auth_store.login(payload.get("username", ""),
                                       payload.get("password", ""))
            if session is None:
                # One message for both failure modes — naming which was wrong
                # tells an attacker which usernames exist.
                self._send_json({"error": "Incorrect username or password"},
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

        elif path == "/api/compare":
            if not self._require_staff():
                return
            question = (payload.get("question") or "").strip()
            if not question:
                self._send_json({"error": "question is required"}, status=400)
                return
            self._send_json(compare.compare(question, router.kb))

        elif path == "/api/sessions/clear":
            if not self._require_staff():
                return
            removed = router.sessions.clear()
            # Clearing conversations clears what was learned from them too.
            # Leaving cross-session memory behind would mean a "cleared" demo
            # still greeted the next customer by recalling a conversation the
            # operator believed they had deleted.
            forgotten = memory.store.forget()
            self._send_json({"removed": removed, "forgotten": forgotten})

        elif path == "/api/replay":
            if not self._require_staff():
                return
            self._send_json(replay.seed(router))

        elif path == "/api/kb/upload":
            if not self._require_staff():
                return
            try:
                if "file_base64" in payload:
                    # Binary upload (.docx). Decoded and parsed before anything
                    # is written, so a file that cannot be read never lands in
                    # the corpus directory.
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
                )
            except runtime.ConfigError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            # Echo the catalogue back, not the submitted values - the response
            # must never contain the key that was just posted.
            self._send_json({
                "providers": runtime.describe_providers(),
                "active": llm.describe(),
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
                # Who has the conversation, so the banner can name them rather
                # than saying "a specialist" after one has already picked up.
                "handled_by": session.handled_by,
                # Session state, not turn state. `escalated` describes what this
                # turn did, so an "@bot ..." aside inside a handoff reports
                # False — and a banner driven by it would disappear while the
                # customer was still in the queue.
                "in_handoff": bool(session.escalated or session.handled_by),
                "latency_ms": result.latency_ms,
                "verified": session.verified,
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
    print("Regional Trust Bank - hybrid assistant demo")
    if info["mode"] == "live":
        endpoint = f" → {info['endpoint']}" if info.get("endpoint") else ""
        print(f"  Generative layer : LIVE via {info['provider']}{endpoint}")
        print(f"  Model            : {info['model']} (effort={info['effort']})")
    else:
        print("  Generative layer : OFFLINE (extractive fallback)")
        print(f"  Reason           : {info['detail']}")
    print(f"  Knowledge base   : {stats['passages']} passages "
          f"across {stats['documents']} documents")
    print(f"  Serving          : http://{HOST}:{PORT}\n")

    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
