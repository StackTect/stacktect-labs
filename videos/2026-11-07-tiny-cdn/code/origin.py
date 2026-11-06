"""Origin server for the CDN test: "the warehouse" (written for this test, not production).

HTTPS + HTTP/1.1, plain Python standard library. Files come from --www (a fresh folder per run).
  /app.js, /app.<hash>.js   the file in --www       Cache-Control: public, max-age=86400  (keep for a day)
  /api/time                 {"now": ...}            Cache-Control: no-store               (never keep)
  /api/slow                 same, after 500 ms work Cache-Control: no-store
Usage: python3 origin.py --port 9001 --www runs/<ts>/www --cert c.pem --key k.pem
"""
import argparse
import json
import re
import socket
import ssl
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=9001)
ap.add_argument("--www", type=Path, required=True)
ap.add_argument("--cert", required=True)
ap.add_argument("--key", required=True)
a = ap.parse_args()
WWW = a.www.resolve()


class Origin(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   # no Nagle delay (nginx does the same)
        super().setup()          # keep-alive, so an edge can reuse its connection

    def log_message(self, *args):
        pass

    def reply(self, code, body, ctype, cache_control):
        self.send_response(code)
        for k, v in (("Content-Type", ctype), ("Content-Length", str(len(body))), ("Cache-Control", cache_control),
                     ("Server", "origin"), ("X-TLS", self.connection.version())):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if re.fullmatch(r"/app(\.[0-9a-f]{8})?\.js", path) and (WWW / path[1:]).is_file():
            return self.reply(200, (WWW / path[1:]).read_bytes(), "application/javascript", "public, max-age=86400")
        if path in ("/api/time", "/api/slow"):
            if path == "/api/slow":
                time.sleep(0.5)                # pretend a slow database query
            return self.reply(200, json.dumps({"now": round(time.time(), 3)}).encode(), "application/json", "no-store")
        self.reply(404, b"not found\n", "text/plain", "no-store")


srv = ThreadingHTTPServer(("127.0.0.1", a.port), Origin)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(a.cert, a.key)
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
srv.serve_forever()
