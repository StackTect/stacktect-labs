"""Tiny CDN edge: "the shop down the road" (about 80 lines, written for this test, not production).

Keeps a copy of a response only when the origin's Cache-Control allows it (max-age, and not no-store/private).
The cache key is the full URL path including the query string. Sends X-Cache: HIT or MISS, and Age on a HIT
(seconds the copy has been stored, RFC 9111 section 5.1). Stale copies are re-fetched after max-age.
A MISS is streamed to the client as the origin's bytes arrive. By default it keeps a pool of warm keep-alive
connections to the origin (real CDNs do); --cold opens a new one per miss.
Usage: python3 edge.py --port 9002 --origin 9001 --cert c.pem --key k.pem [--cold]
"""
import argparse
import http.client
import queue
import re
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=9002)
ap.add_argument("--origin", type=int, default=9001)
ap.add_argument("--cert", required=True)
ap.add_argument("--key", required=True)
ap.add_argument("--cold", action="store_true")
a = ap.parse_args()

CACHE, LOCK, POOL = {}, threading.Lock(), queue.Queue()
UPSTREAM_TLS = ssl._create_unverified_context()      # test certificate on 127.0.0.1


def connect():
    conn = http.client.HTTPSConnection("127.0.0.1", a.origin, context=UPSTREAM_TLS, timeout=10)
    conn.connect()
    conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return conn


def fetch(path):
    """GET path from the origin. Returns (conn, response) with the body still unread, so it can be streamed.
    Reuses a pooled keep-alive connection unless --cold (a stale pooled connection is replaced once)."""
    for attempt in (1, 2):
        try:
            conn = POOL.get_nowait() if not a.cold else None
        except queue.Empty:
            conn = None
        conn = conn or connect()
        try:
            conn.request("GET", path)
            return conn, conn.getresponse()
        except (http.client.HTTPException, OSError):
            conn.close()
            if attempt == 2:
                raise


def done(conn):
    if a.cold:
        conn.close()
    else:
        POOL.put(conn)


def max_age(cache_control):
    cc = cache_control.lower()
    if "no-store" in cc or "private" in cc:
        return None
    m = re.search(r"max-age=(\d+)", cc)
    return int(m.group(1)) if m else None


class Edge(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   # no Nagle delay (nginx does the same)
        super().setup()

    def log_message(self, *args):
        pass

    def send_head(self, status, headers, length, xcache, age=None):
        self.send_response(status)
        for k in ("Content-Type", "Cache-Control"):
            if k in headers:
                self.send_header(k, headers[k])
        self.send_header("Content-Length", str(length))
        self.send_header("X-Cache", xcache)
        if age is not None:
            self.send_header("Age", str(age))
        self.send_header("Via", "tiny-cdn")
        self.send_header("X-TLS", self.connection.version())
        self.end_headers()

    def do_GET(self):
        now = time.time()
        with LOCK:
            hit = CACHE.get(self.path)
        if hit and now - hit["stored"] < hit["max_age"]:
            self.send_head(hit["status"], hit["headers"], len(hit["body"]), "HIT", int(now - hit["stored"]))
            self.wfile.write(hit["body"])
            return
        conn, r = fetch(self.path)                      # MISS: stream the origin's bytes on as they arrive
        headers = dict(r.getheaders())
        self.send_head(r.status, headers, int(headers.get("Content-Length", 0)), "MISS")
        parts = []
        while chunk := r.read(16384):
            self.wfile.write(chunk)
            parts.append(chunk)
        done(conn)
        ttl = max_age(headers.get("Cache-Control", ""))
        if r.status == 200 and ttl:
            with LOCK:
                CACHE[self.path] = {"stored": time.time(), "max_age": ttl, "status": r.status, "headers": headers,
                                    "body": b"".join(parts)}


srv = ThreadingHTTPServer(("127.0.0.1", a.port), Edge)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(a.cert, a.key)
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
srv.serve_forever()
