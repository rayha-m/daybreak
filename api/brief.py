"""Vercel serverless function: POST /api/brief  ->  {"html": "...", "notes": [...]}

Vercel runs this file whenever the website asks for a brief. Nothing is stored:
calendar links and Gemini keys are used for this one request and then forgotten
(they are never logged or written anywhere).
"""

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daybreak.web import BadInput, build  # noqa: E402

MAX_BODY = 20_000


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(413, {"error": "Request too large."})
            form = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(form, dict):
                raise BadInput("Bad request.")
            return self._send(200, build(form))
        except BadInput as exc:
            return self._send(400, {"error": str(exc)})
        except Exception as exc:  # keep details out of the page, but leave a hint in Vercel's logs
            print("brief failed:", type(exc).__name__)
            return self._send(500, {"error": "Something went wrong building the brief. Try again."})

    def do_GET(self):
        self._send(405, {"error": "Use the form on the homepage."})

    def log_message(self, *args):   # don't print request lines (could contain personal info)
        pass
