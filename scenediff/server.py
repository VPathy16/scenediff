"""Loopback-only UI server. Comparison endpoints never write project files."""

from __future__ import annotations

import hmac
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .gitrepo import compare_git
from .report import demo, render
from .semantic import compare_file, report

MAX_REQUEST = 32 * 1024 * 1024


def create_server(port=0, initial=None):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, status, body, mime="application/json"):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(data)

        def local_request(self):
            expected = f"127.0.0.1:{self.server.server_port}"
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            return host == expected and (origin is None or origin == f"http://{expected}")

        def do_GET(self):
            if not self.local_request():
                self.send(403, '{"error":"Local requests only"}')
            elif urlsplit(self.path).path == "/":
                self.send(200, render(initial or demo(), token=token, live=True), "text/html")
            else:
                self.send(404, '{"error":"Not found"}')

        def do_POST(self):
            if not self.local_request() or not hmac.compare_digest(self.headers.get("X-SceneDiff-Token", ""), token):
                self.send(403, '{"error":"Invalid local session"}')
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_REQUEST:
                    raise ValueError("Request must be between 1 byte and 32 MiB")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Expected a JSON object")
                if self.path == "/api/git":
                    for key in ("repo", "left", "right"):
                        if not isinstance(data.get(key), str):
                            raise ValueError(f"Missing {key}")
                    for key in ("base", "path"):
                        if data.get(key) is not None and not isinstance(data[key], str):
                            raise ValueError(f"Invalid {key}")
                    payload = compare_git(data["repo"], data["left"], data["right"], data.get("base") or None, data.get("path") or None)
                elif self.path == "/api/files":
                    for key in ("left", "right"):
                        if not isinstance(data.get(key), str):
                            raise ValueError(f"Missing {key} file contents")
                    if data.get("base") is not None and not isinstance(data["base"], str):
                        raise ValueError("Invalid base file contents")
                    path = str(data.get("name", "scene.tscn"))
                    three = data.get("base") is not None
                    item = compare_file(path, data.get("base"), data["left"], data["right"], three_way=three)
                    payload = report([item], ["Base", "A", "B"], three_way=three, context={"source": "files"})
                else:
                    self.send(404, '{"error":"Not found"}')
                    return
                self.send(200, json.dumps(payload, ensure_ascii=True))
            except (ValueError, OSError) as exc:
                self.send(400, json.dumps({"error": str(exc)}))

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server
