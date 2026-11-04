"""A tiny round-robin load balancer, written for this test (not production). Python standard library only.

Each request goes to the next backend in turn; a backend that fails gives the client 502 Bad Gateway.
  --health         every 1 s GET /health on each backend (0.5 s limit); 2 misses in a row = out, 1 good check = back in
  --retry          if the chosen backend fails (refused, reset or timed out), try the next one once
  --timeout S      give up on a backend after S seconds (default: wait forever)
  GET /admin/drain?port=P   stop sending NEW requests to P (in-flight ones finish)
  GET /admin/inflight?port=P  how many requests P is still working on
Every response carries "X-Backend: PORT".

Usage: python3 lb.py PORT BACKEND_PORT [BACKEND_PORT ...] [--health] [--retry] [--timeout S]
"""
import argparse
import http.client
import itertools
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

p = argparse.ArgumentParser()
p.add_argument("port", type=int)
p.add_argument("backends", type=int, nargs="+")
p.add_argument("--health", action="store_true")
p.add_argument("--retry", action="store_true")
p.add_argument("--timeout", type=float, default=None)
args = p.parse_args()

up = {b: True for b in args.backends}
misses = {b: 0 for b in args.backends}
drained, inflight = set(), {b: 0 for b in args.backends}
turn = itertools.cycle(args.backends)
lock = threading.Lock()


def next_backend(skip=None):
    with lock:
        for _ in args.backends:
            b = next(turn)
            if b != skip and b not in drained and (not args.health or up[b]):
                return b
    return None


def forward(b, path):
    with lock:
        inflight[b] += 1
    conn = http.client.HTTPConnection("127.0.0.1", b, timeout=args.timeout)
    try:
        conn.request("GET", path)
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()
        with lock:
            inflight[b] -= 1


def check_forever():
    while True:
        for b in args.backends:
            try:
                c = http.client.HTTPConnection("127.0.0.1", b, timeout=0.5)
                c.request("GET", "/health")
                ok = c.getresponse().status == 200
                c.close()
            except OSError:
                ok = False
            misses[b] = 0 if ok else misses[b] + 1
            up[b] = misses[b] < 2
        time.sleep(1.0)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        if u.path.startswith("/admin/"):
            port = int(parse_qs(u.query)["port"][0])
            if u.path == "/admin/drain":
                drained.add(port)
            body = f"{inflight[port]}\n".encode()
            self.send_response(200)
        else:
            b = next_backend()
            status, body, used = 502, b"502 Bad Gateway\n", b
            for attempt in range(2 if args.retry else 1):
                if attempt:                               # only after a failure
                    b = next_backend(skip=used)
                if b is None:
                    break
                used = b
                try:
                    status, body = forward(b, u.path)
                    break
                except OSError:                           # refused, reset, or timed out
                    status, body = 502, b"502 Bad Gateway\n"
            self.send_response(status)
            self.send_header("X-Backend", str(used))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if args.health:
    threading.Thread(target=check_forever, daemon=True).start()
srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
srv.daemon_threads = True
srv.serve_forever()
