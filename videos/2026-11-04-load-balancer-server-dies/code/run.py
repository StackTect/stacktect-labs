"""Failover test v2 for the StackTect load-balancer long (owner's script v3). Python stdlib only, all on 127.0.0.1.

Per run: backends 8001-8003 + lb.py on 8000; requests sent on a fixed schedule (RATE/s for DURATION s, never waiting
for answers; the client gives up after CLIENT_TIMEOUT s); at KILL_AT s backend 8002 is hit. Each run is repeated REPEATS
times; the summary reports min-max.
  1 plain    round robin                         kill -KILL (crash)
  2 health   --health                            kill -KILL
  3 retry    --health --retry                    kill -KILL
  4 frozen   --health --retry (no timeout)       kill -STOP (frozen: accepts connections, never answers)
  5 timeout  --health --retry --timeout 0.5      kill -STOP
  6a nodrain --health, every 10th request /slow (2 s); kill -TERM at once (control for 6b)
  6b drain   --health, same slow mix; /admin/drain 8002, wait until it has 0 in flight, then kill -TERM
Writes results-<stamp>.json (every request of every repeat + summaries) and run-<stamp>.log.

Usage: python3 run.py [REPEATS]
"""
import http.client
import json
import os
import platform
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
RATE, DURATION, KILL_AT, VICTIM, CLIENT_TIMEOUT = 50, 30.0, 10.0, 8002, 10.0
BACKENDS = [8001, 8002, 8003]
RUNS = [("1 plain", [], "KILL", False, False), ("2 health", ["--health"], "KILL", False, False),
        ("3 retry", ["--health", "--retry"], "KILL", False, False),
        ("4 frozen", ["--health", "--retry"], "STOP", False, False),
        ("5 timeout", ["--health", "--retry", "--timeout", "0.5"], "STOP", False, False),
        ("6a nodrain", ["--health"], "TERM", True, False), ("6b drain", ["--health"], "TERM", True, True)]


def get(port, path, timeout=0.5):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        c.request("GET", path)
        return c.getresponse().read().decode().strip()
    finally:
        c.close()


def wait_port(port, path="/health", timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            get(port, path)
            return
        except OSError:
            time.sleep(0.05)
    raise SystemExit(f"port {port} did not come up")


def one(t0, k, slow_mix):
    time.sleep(max(0.0, t0 + k / RATE - time.perf_counter()))
    path = "/slow" if slow_mix and k % 10 == 0 else "/"
    s = time.perf_counter()
    try:
        c = http.client.HTTPConnection("127.0.0.1", 8000, timeout=CLIENT_TIMEOUT)
        c.request("GET", path)
        r = c.getresponse()
        r.read()
        status, backend = r.status, r.getheader("X-Backend")
        c.close()
    except OSError:
        status, backend = 0, None                         # 0 = the client gave up (no answer at all)
    return {"t": round(s - t0, 3), "path": path, "status": status, "ms": round((time.perf_counter() - s) * 1000, 1),
            "backend": int(backend) if backend and backend.isdigit() else None}


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None


def summary(rows):
    ok = [r for r in rows if r["status"] == 200]
    bad = [r for r in rows if r["status"] != 200]
    fast_ok = [r["ms"] for r in ok if r["path"] == "/"]
    share = lambda rs: {str(b): sum(1 for r in rs if r["status"] == 200 and r["backend"] == b) for b in BACKENDS}
    return {"requests": len(rows), "failed": len(bad), "failed_502": sum(1 for r in bad if r["status"] == 502),
            "no_answer": sum(1 for r in bad if r["status"] == 0),
            "fail_window_s": round(max(r["t"] for r in bad) - KILL_AT, 2) if bad else 0.0,
            "slowest_ms": max(r["ms"] for r in rows),
            "over_1s": sum(1 for r in rows if r["ms"] > 1000 and r["path"] == "/"),
            "p50_ms": pct(fast_ok, 0.50), "p99_ms": pct(fast_ok, 0.99),
            "per_backend_before": share([r for r in rows if r["t"] < KILL_AT]),
            "per_backend_after": share([r for r in rows if r["t"] >= KILL_AT + 5])}


def run_once(name, flags, sig, slow_mix, drain, log):
    procs = {b: subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "backend.py"), str(b)]) for b in BACKENDS}
    for b in BACKENDS:
        wait_port(b)
    lb = subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "lb.py"), "8000", *map(str, BACKENDS), *flags])
    wait_port(8000, "/admin/inflight?port=8001")
    time.sleep(2.0)                                       # health checks see everyone up
    t0 = time.perf_counter() + 0.2
    hit = {}

    def killer():
        time.sleep(max(0.0, t0 + KILL_AT - time.perf_counter()))
        if drain:
            get(8000, f"/admin/drain?port={VICTIM}")
            while int(get(8000, f"/admin/inflight?port={VICTIM}")) > 0:
                time.sleep(0.05)
        hit["t"] = round(time.perf_counter() - t0, 2)
        procs[VICTIM].send_signal(getattr(signal, "SIG" + sig))
    threading.Thread(target=killer, daemon=True).start()
    with ThreadPoolExecutor(max_workers=600) as ex:
        rows = list(ex.map(lambda k: one(t0, k, slow_mix), range(int(RATE * DURATION))))
    lb.kill()
    for p in procs.values():
        p.kill()                                          # SIGKILL also ends a SIGSTOPped process
    for p in [lb, *procs.values()]:
        p.wait()
    s = summary(rows)
    s["signal_at_s"] = hit.get("t")
    log(f"{name:11} failed {s['failed']:4} (502: {s['failed_502']}, no answer: {s['no_answer']}), window {s['fail_window_s']} s, "
        f"slowest {s['slowest_ms']} ms, >1 s: {s['over_1s']}, p99 {s['p99_ms']} ms, signal at {s['signal_at_s']} s")
    time.sleep(1.0)
    return {"summary": s, "rows": rows}


def main():
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    lines = []

    def log(line):
        print(line, flush=True)
        lines.append(line)
    log(f"date: {datetime.now().isoformat(timespec='seconds')}  python {platform.python_version()}  cpu threads {os.cpu_count()}")
    log(f"{RATE} req/s for {DURATION:.0f} s, :{VICTIM} hit at {KILL_AT:.0f} s, client gives up after {CLIENT_TIMEOUT:.0f} s, "
        f"{repeats} repeats per run")
    out = {"settings": {"rate": RATE, "duration_s": DURATION, "kill_at_s": KILL_AT, "victim": VICTIM, "work_ms": 20, "slow_ms": 2000,
                        "client_timeout_s": CLIENT_TIMEOUT, "health_interval_s": 1.0, "health_check_timeout_s": 0.5,
                        "health_misses_to_remove": 2, "repeats": repeats}, "runs": []}
    for name, flags, sig, slow_mix, drain in RUNS:
        log(f"### {name}: lb.py 8000 {' '.join(map(str, BACKENDS))} {' '.join(flags)}  (kill -{sig}{', drain first' if drain else ''}"
            f"{', every 10th request /slow' if slow_mix else ''})")
        reps = [run_once(name, flags, sig, slow_mix, drain, log) for _ in range(repeats)]
        keys = ["failed", "no_answer", "fail_window_s", "slowest_ms", "over_1s", "p99_ms"]
        rng = {k: [min(r["summary"][k] for r in reps), max(r["summary"][k] for r in reps)] for k in keys}
        log(f"  range: " + ", ".join(f"{k} {v[0]}-{v[1]}" for k, v in rng.items()))
        log("")
        out["runs"].append({"name": name, "flags": flags, "signal": sig, "slow_mix": slow_mix, "drain": drain, "range": rng,
                            "repeats": reps})
    with open(os.path.join(HERE, f"results-{stamp}.json"), "w") as f:
        json.dump(out, f, indent=1)
    with open(os.path.join(HERE, f"run-{stamp}.log"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("wrote", f"results-{stamp}.json", f"run-{stamp}.log")


if __name__ == "__main__":
    main()
