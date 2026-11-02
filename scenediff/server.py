"""Loopback-only UI server. Comparison endpoints never write project files."""

from __future__ import annotations

import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .gitrepo import compare_git
from .report import demo, render
from .semantic import compare_file, compare_local, report
from .matching import MatchError
from .merge import merge_selection

MAX_REQUEST = 32 * 1024 * 1024


def refresh_file(data, sources, matches, strict=False):
    """Re-compare one file after an apply or a match change, from what the page sends.

    Warnings that came from outside the comparison (for example, about Git conflict
    markers) are carried over; confirmed matches that no longer fit are dropped.
    """
    name = data.get("name")
    if not isinstance(name, str) or not isinstance(sources, list) or len(sources) != 3 or not all(
            s is None or isinstance(s, str) for s in sources):
        raise ValueError("Send the file name and its base, A and B sources")
    if not isinstance(matches, list):
        raise ValueError("Node matches must be a list")
    three = bool(data.get("three_way"))
    carried = data.get("extra_warnings")
    if not isinstance(carried, list) or not all(isinstance(w, str) for w in carried):
        previous = data.get("sources") if isinstance(data.get("sources"), list) else sources
        produced = compare_file(name, *previous, three_way=three)["warnings"]
        carried = [w for w in data.get("warnings") or [] if isinstance(w, str) and w not in produced]
    try:
        item = compare_file(name, *sources, three_way=three, node_matches=matches)
    except MatchError:
        if strict:
            raise
        item = compare_file(name, *sources, three_way=three)
        item["warnings"].append("The confirmed node matches no longer fit this version of the file and were cleared.")
    item["warnings"] = list(dict.fromkeys(item["warnings"] + carried))
    item["extra_warnings"] = carried
    return item


def create_server(port=0, initial=None):
    token = secrets.token_urlsafe(32)
    current_report = initial if initial is not None else demo()
    report_lock = threading.Lock()

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
                self.send(200, render(current_report, token=token, live=True), "text/html")
            else:
                self.send(404, '{"error":"Not found"}')

        def do_POST(self):
            nonlocal current_report
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
                if self.path == "/api/match":
                    payload = {"file": refresh_file(data, data.get("sources"), data.get("node_matches"), strict=True)}
                    self.send(200, json.dumps(payload, ensure_ascii=True))
                    return
                elif self.path == "/api/git":
                    for key in ("repo", "left", "right"):
                        if not isinstance(data.get(key), str):
                            raise ValueError(f"Missing {key}")
                    for key in ("base", "path"):
                        if data.get(key) is not None and not isinstance(data[key], str):
                            raise ValueError(f"Invalid {key}")
                    payload = compare_git(data["repo"], data["left"], data["right"], data.get("base") or None, data.get("path") or None)
                elif self.path == "/api/merge":
                    sources, source = data.get("sources"), data.get("source")
                    matches = data.get("node_matches") or []
                    text, warnings, imports = merge_selection(sources, source, data.get("entity"), data.get("property"),
                                                              data.get("name", "scene.tscn"), data.get("imports"),
                                                              matches, bool(data.get("three_way")))
                    merged = list(sources)
                    merged[3 - source] = text
                    payload = {"file": refresh_file(data, merged, matches),
                               "merge": {"target": 3 - source, "warnings": warnings, "imports": imports}}
                    self.send(200, json.dumps(payload, ensure_ascii=True))
                    return
                elif self.path == "/api/files":
                    if not isinstance(data.get("left"), str):
                        raise ValueError("Missing left file contents")
                    for key in ("base", "right"):
                        if data.get(key) is not None and not isinstance(data[key], str):
                            raise ValueError(f"Invalid {key} file contents")
                    payload = compare_local(str(data.get("name", "scene.tscn")), data.get("base"), data["left"], data.get("right"))
                else:
                    self.send(404, '{"error":"Not found"}')
                    return
                with report_lock:
                    current_report = payload
                self.send(200, json.dumps(payload, ensure_ascii=True))
            except (ValueError, OSError) as exc:
                self.send(400, json.dumps({"error": str(exc)}))

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server

