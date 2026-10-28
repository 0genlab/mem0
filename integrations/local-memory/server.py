"""Authenticated loopback HTTP entry point for the local developer memory."""

import argparse
import hmac
import ipaddress
import json
import os
import secrets
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from store import InvalidRequest, LocalStore

MAX_BODY = 2 * 1024 * 1024


def load_token(path):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError("token file must be private and not a symlink")
    else:
        with os.fdopen(fd, "w") as output:
            output.write(secrets.token_urlsafe(48) + "\n")
    token = path.read_text(encoding="ascii").strip()
    if len(token) < 32:
        raise ValueError("invalid token file")
    return token


def handler_for(store, token):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):
            pass

        def respond(self, status, payload):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            given = self.headers.get("Authorization", "")
            if not hmac.compare_digest(given, "Bearer " + token):
                self.respond(401, {"error": "unauthorized"})
                return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            if self.path == "/health":
                self.respond(200, {"ok": True, "backend": "local"})
            else:
                self.respond(404, {"error": "not found"})

        def do_POST(self):
            if not self.authorized():
                return
            routes = {
                "/v1/capture": store.capture,
                "/v1/remember": store.remember,
                "/v1/search": store.search,
                "/v1/supersede": store.supersede,
                "/v1/delete-one": store.delete_one,
            }
            action = routes.get(self.path)
            if action is None:
                self.respond(404, {"error": "not found"})
                return
            try:
                length = self.headers.get("Content-Length")
                if self.headers.get("Transfer-Encoding") or length is None or not length.isdecimal():
                    raise InvalidRequest("Content-Length required")
                if len(length) > 7:
                    self.respond(413, {"error": "request too large"})
                    self.close_connection = True
                    return
                size = int(length)
                if size > MAX_BODY:
                    self.respond(413, {"error": "request too large"})
                    self.close_connection = True
                    return
                if size == 0:
                    raise InvalidRequest("empty request")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise InvalidRequest("expected JSON object")
                self.respond(200, action(body))
            except (InvalidRequest, UnicodeDecodeError, json.JSONDecodeError) as exc:
                self.respond(400, {"error": str(exc) if isinstance(exc, InvalidRequest) else "invalid JSON"})
            except Exception:
                self.respond(500, {"error": "internal error"})

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Local developer memory service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--token-file", required=True)
    args = parser.parse_args()
    try:
        address = ipaddress.ip_address(args.host)
        if not address.is_loopback:
            parser.error("--host must be a loopback IP address")
    except ValueError:
        parser.error("--host must be a loopback IP address")
    token = load_token(args.token_file)
    store = LocalStore(args.data_dir)
    try:

        class LocalHTTPServer(ThreadingHTTPServer):
            address_family = socket.AF_INET6 if address.version == 6 else socket.AF_INET

        server = LocalHTTPServer((args.host, args.port), handler_for(store, token))
        try:
            server.serve_forever()
        finally:
            server.server_close()
    finally:
        store.close()


if __name__ == "__main__":
    main()
