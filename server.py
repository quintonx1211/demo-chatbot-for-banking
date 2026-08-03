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

Binds to 127.0.0.1 deliberately: the settings endpoint accepts an API key and
the knowledge-base endpoints write files, neither of which should be reachable
from off-box. There is no authentication - this is a demo, not a deployment.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app import llm
from app.kbstore import KBError, KnowledgeBaseStore
from app.llm import runtime
from app.router import Router

WEB_DIR = Path(__file__).resolve().parent / "web"
HOST = "127.0.0.1"
PORT = 8000

# Requests bigger than this are refused before being read into memory.
MAX_BODY_BYTES = 1024 * 1024

router = Router()
kb_store = KnowledgeBaseStore(router.kb)

_CONTENT_TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}


class Handler(BaseHTTPRequestHandler):
    server_version = "RegionalTrustDemo/0.1"

    # -- helpers ----------------------------------------------------------

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
            self._send_json({"sessions": [{
                "session_id": s.session_id,
                "customer_name": (s.customer or {}).get("name"),
                "verified": s.verified,
                "reason": s.escalation_reason,
                "turns": len(s.audit),
                "created_at": s.created_at,
            } for s in router.sessions.escalated_sessions()]})
        elif path.startswith("/api/session/"):
            session = router.sessions.get(path.rsplit("/", 1)[-1])
            if not session:
                self._send_json({"error": "unknown session"}, status=404)
            else:
                self._send_json(session.to_dict())

        elif path == "/api/kb":
            self._send_json({
                "documents": [d.to_dict() for d in kb_store.list_documents()],
                "stats": router.kb.stats,
            })

        elif path == "/api/kb/doc":
            name = (parse_qs(urlparse(self.path).query).get("name") or [""])[0]
            try:
                self._send_json(kb_store.read_document(name))
            except KBError as exc:
                self._send_json({"error": str(exc)}, status=404)

        elif path == "/api/providers":
            self._send_json({
                "providers": runtime.describe_providers(),
                "active": llm.describe(),
            })

        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return

        if path == "/api/kb/upload":
            try:
                info = kb_store.save_document(
                    payload.get("filename", ""), payload.get("content", "")
                )
            except KBError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json({"document": info.to_dict(), "stats": router.kb.stats})

        elif path == "/api/kb/delete":
            try:
                kb_store.delete_document(payload.get("filename", ""))
            except KBError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json({"deleted": payload.get("filename"),
                             "stats": router.kb.stats})

        elif path == "/api/providers":
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
                "latency_ms": result.latency_ms,
                "verified": session.verified,
                "debug": result.debug,
            })

        elif path == "/api/summary":
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
