"""A tiny backend for the StackTect load-balancer demo v2 (Python standard library only).

GET /        -> 200 "OK from :PORT" after ~20 ms of pretend work
GET /slow    -> 200 after ~2 s (the "slow requests" for the drain run)
GET /health  -> 200 "ok"
SIGTERM      -> stops at once (in-flight requests are cut off); that is why a deploy drains first.
SIGKILL      -> crash.  SIGSTOP -> frozen: the kernel still accepts connections, nothing ever answers.

Usage: python3 backend.py PORT
"""
import os
import signal
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1])


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = b"ok\n"
        else:
            time.sleep(2.0 if self.path == "/slow" else 0.020)
            body = f"OK from :{PORT}\n".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


signal.signal(signal.SIGTERM, lambda s, f: os._exit(0))
srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
srv.daemon_threads = True
srv.serve_forever()
