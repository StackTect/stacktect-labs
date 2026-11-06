"""Runs every test case with curl (a new curl process = a fresh connection every time) and writes
results.json + run.log into the run folder. Called by inner.sh inside the network sandbox.
Usage: python3 measure.py OUT_DIR FAR_MS NEAR_MS RUNS
"""
import atexit
import hashlib
import json
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

OUT, FAR, NEAR, N = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
WWW = OUT / "www"
DIRECT, EDGE, EDGE_COLD = "https://127.0.0.1:9001", "https://127.0.0.1:9002", "https://127.0.0.1:9003"
LOG = open(OUT / "run.log", "w", encoding="utf-8")
FIELDS = {"connect": "time_connect", "tls": "time_appconnect", "first_byte": "time_starttransfer", "total": "time_total"}


def log(line=""):
    print(line)
    LOG.write(line + "\n")
    LOG.flush()


def curl(url):
    """One request. Times in ms (curl's are cumulative from the start: connect <= tls <= first byte <= total)."""
    p = subprocess.run(["curl", "-sk", "--http1.1", "--noproxy", "*", "-D", "-", "-o", str(OUT / "body.tmp"),
                        "-w", "\n%{json}", url], capture_output=True, text=True, check=True)
    head, js = p.stdout.rsplit("\n", 1)
    w = json.loads(js)
    headers = {}
    for line in head.splitlines()[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    body = (OUT / "body.tmp").read_bytes()
    r = {k: round(w[v] * 1000, 1) for k, v in FIELDS.items()}
    r.update(status=w["http_code"], bytes=len(body), x_cache=headers.get("x-cache", "-"), age=headers.get("age"),
             cache_control=headers.get("cache-control"), tls_version=headers.get("x-tls"),
             version=body.split(b"\n", 1)[0].decode(errors="replace") if body.startswith(b"//") else None)
    return r


def show(label, r):
    age = f"  age {r['age']}" if r["age"] is not None else ""
    log(f"{label:<22} {r['status']}  total {r['total']:7.1f} ms  connect {r['connect']:6.1f}  tls {r['tls']:6.1f}  "
        f"first byte {r['first_byte']:7.1f}  x-cache {r['x_cache']}{age}" + (f"  [{r['version']}]" if r["version"] else ""))


def case(name, label, urls):
    log(f"\n# {name}: {label}")
    runs = []
    for url in urls:
        r = curl(url)
        show(label, r)
        runs.append(r)
    med = {k: round(statistics.median(x[k] for x in runs), 1) for k in FIELDS}
    rng = {k: [min(x[k] for x in runs), max(x[k] for x in runs)] for k in FIELDS}
    log(f"median {name}: " + "  ".join(f"{k} {v} ms" for k, v in med.items()) +
        f"  x-cache {sorted({x['x_cache'] for x in runs})}")
    RESULTS["cases"][name] = {"label": label, "median_ms": med, "range_ms": rng, "runs": runs}


def app_js(version):
    """~200 KB JavaScript file; first line says which version it is."""
    line = "function render(item){return '<li>'+item.name+' '+item.price+'</li>';}\n"
    return (f"// version {version}\n" + line * 2800).encode()


WWW.mkdir()
(WWW / "app.js").write_bytes(app_js(1))
CERT = ["--cert", str(OUT / "cert.pem"), "--key", str(OUT / "key.pem")]
SERVERS = [subprocess.Popen([sys.executable, "origin.py", "--port", "9001", "--www", str(WWW), *CERT]),
           subprocess.Popen([sys.executable, "edge.py", "--port", "9002", "--origin", "9001", *CERT]),
           subprocess.Popen([sys.executable, "edge.py", "--port", "9003", "--origin", "9001", "--cold", *CERT])]
atexit.register(lambda: [s.terminate() for s in SERVERS])
for port in (9001, 9002, 9003):                     # wait until each server listens
    for _ in range(100):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.1)
    else:
        raise SystemExit(f"server on :{port} did not start")
RESULTS = {"date": time.strftime("%Y-%m-%d %H:%M:%S"),
           "setup": {"simulated": True,
                     "far_link_one_way_ms": FAR, "near_link_one_way_ms": NEAR,
                     "far_rtt_ms": 2 * FAR, "near_rtt_ms": 2 * NEAR,
                     "runs_per_case": N, "mtu": 1500, "app_js_bytes": len(app_js(1)),
                     "how": "unshare -rn network sandbox; tc netem delay on loopback per port: origin :9001 = far link, "
                            "edges :9002 (warm) / :9003 (cold) = near link; edge -> origin also crosses the far link. "
                            "Real kernel TCP + TLS (Python ssl / curl OpenSSL); a new curl process per request."},
           "cases": {}, "stale": []}
log(f"simulated distance: far round trip {2 * FAR} ms (laptop <-> origin, edge <-> origin), near round trip {2 * NEAR} ms (laptop <-> edge)")
log(f"app.js = {len(app_js(1))} bytes, {N} runs per case, a fresh connection every run")

case("direct_app", "direct /app.js", [f"{DIRECT}/app.js"] * N)
curl(f"{EDGE}/app.js")                                                 # warm the edge once (not counted)
case("cdn_hit_app", "cdn /app.js", [f"{EDGE}/app.js"] * N)
case("cdn_miss_app", "cdn /app.js?v=new", [f"{EDGE}/app.js?v=miss{i}" for i in range(N)])
curl(f"{EDGE_COLD}/app.js?v=coldwarmup")
case("cdn_miss_cold_app", "cdn(cold) /app.js?v=new", [f"{EDGE_COLD}/app.js?v=cold{i}" for i in range(N)])
case("direct_time", "direct /api/time", [f"{DIRECT}/api/time"] * N)
case("cdn_time", "cdn /api/time", [f"{EDGE}/api/time"] * N)
case("direct_slow", "direct /api/slow", [f"{DIRECT}/api/slow"] * N)
case("cdn_slow", "cdn /api/slow", [f"{EDGE}/api/slow"] * N)

log("\n# stale: deploy a new app.js while the edge still has the old copy")
WAIT = 8
log(f"(edge copy of /app.js is from the warm-up; waiting {WAIT} s so Age is visible)")
time.sleep(WAIT)
(WWW / "app.js").write_bytes(app_js(2))
log("$ deploy app.js  (version 2 written on the origin)")
for label, url in (("direct /app.js", f"{DIRECT}/app.js"), ("cdn /app.js", f"{EDGE}/app.js")):
    r = curl(url)
    show(label, r)
    RESULTS["stale"].append({"step": label, **r})
h = hashlib.sha256(app_js(2)).hexdigest()[:8]
(WWW / f"app.{h}.js").write_bytes(app_js(2))
log(f"$ deploy app.{h}.js  (same version 2, fingerprinted name)")
for i in (1, 2):
    r = curl(f"{EDGE}/app.{h}.js")
    show(f"cdn /app.{h}.js", r)
    RESULTS["stale"].append({"step": f"cdn /app.{h}.js #{i}", **r})

(OUT / "body.tmp").unlink()
(OUT / "results.json").write_text(json.dumps(RESULTS, indent=1), encoding="utf-8")
log(f"\nwrote {OUT / 'results.json'}")
