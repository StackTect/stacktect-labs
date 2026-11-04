# Load Balancer: What Happens When a Server Dies?

Video: https://youtu.be/knawyvH6kOQ

In this lab you run three tiny servers and a small load balancer on your own laptop. Then you send them 50 requests a second
and kill one server while the requests are flowing. You'll see how many users get an error, and how health checks, retries,
timeouts and draining get that number down to zero.

Everything runs on `127.0.0.1` and uses only the Python standard library. Nothing to install.

## What's in `code/`

| File | What it does |
|---|---|
| `backend.py PORT` | A tiny server. `GET /` answers `OK from :PORT` after ~20 ms of pretend work. `GET /slow` takes ~2 s. `GET /health` answers `ok`. |
| `lb.py PORT BACKEND...` | A round-robin load balancer written for this lab (**not production code**). Each response has an `X-Backend` header that tells you which server answered. |
| `run.py [REPEATS]` | Runs the whole experiment automatically (all 7 runs below) and writes `results-<time>.json` and `run-<time>.log`. |

`lb.py` options:
- `--health`: checks `/health` on every server once a second (0.5 s limit). After 2 misses in a row the server is taken out of the rotation, and 1 good check puts it back.
- `--retry`: if the chosen server fails (connection refused, reset or timed out), it tries the next server **once**.
- `--timeout S`: gives up on a server after `S` seconds. Without it, the load balancer waits forever.
- `GET /admin/drain?port=P`: stops sending *new* requests to `P`. Requests already in progress still finish.
- `GET /admin/inflight?port=P`: shows how many requests `P` is still working on.

## Setup

You need Python 3.8 or newer (we used 3.14) on Linux or macOS. On Windows, use WSL, because the lab uses Unix signals (`kill -STOP`).

```bash
git clone https://github.com/StackTect/stacktect-labs.git
cd stacktect-labs/videos/2026-11-04-load-balancer-server-dies/code
python3 --version
```

## Option A: run the full experiment (about 4 minutes)

```bash
python3 run.py 1      # 1 repeat of each run, ~4 min
python3 run.py        # 5 repeats of each run (what we used for the video), ~20 min
```

Ports 8000 to 8003 must be free. The script starts and stops all the servers itself.

## Option B: try it by hand (4 terminals)

```bash
# terminals 1-3: the servers
python3 backend.py 8001
python3 backend.py 8002
python3 backend.py 8003

# terminal 4: the load balancer (plain round robin first)
python3 lb.py 8000 8001 8002 8003
```

Then, in another terminal:

```bash
for i in 1 2 3 4 5 6; do curl -s 127.0.0.1:8000; done   # OK from :8001, :8002, :8003, :8001 ...
```

Now stop the server on 8002 (press Ctrl+C in its terminal, or run `pkill -9 -f "backend.py 8002"`) and run the loop again.
Every third request now gets `502 Bad Gateway`.

Restart the load balancer with `--health --retry` and try again. The errors stop.

To see a **frozen** server, which accepts connections but never answers:

```bash
pkill -STOP -f "backend.py 8002"    # freeze it
curl -s 127.0.0.1:8000              # without --timeout, some requests hang
pkill -CONT -f "backend.py 8002"    # unfreeze it
```

## The 7 runs

Every run uses 3 servers (8001-8003) and the load balancer on 8000. The client sends 50 requests a second for 30 s (1,500 requests),
and server **8002 is hit at 10 s**. The client gives up after 10 s.

| # | Load balancer flags | What happens to 8002 |
|---|---|---|
| 1 plain | *(none)* | `kill -KILL`: crash |
| 2 health | `--health` | `kill -KILL` |
| 3 retry | `--health --retry` | `kill -KILL` |
| 4 frozen | `--health --retry` | `kill -STOP`: frozen, accepts connections but never answers |
| 5 timeout | `--health --retry --timeout 0.5` | `kill -STOP` |
| 6a no drain | `--health`, every 10th request is a 2 s `/slow` one | `kill -TERM` straight away |
| 6b drain | same as 6a | drain 8002, wait until 0 requests are in progress, then `kill -TERM` |

## Our results

Measured on 2026-11-03 on one Linux laptop (16 CPU threads, Python 3.14.8). Each run was repeated 5 times.
The table shows the **median**, with the min-max range in brackets. Full numbers: [`results/summary.json`](results/summary.json),
raw log: [`results/run-log.txt`](results/run-log.txt).

| # | Failed requests (of 1,500) | Failures went on for | Slowest request | p99 latency* |
|---|---|---|---|---|
| 1 plain | **334** (334-334), all 502 | until the end of the test (19.96 s) | 24.7 ms | 24.4 ms |
| 2 health | **31** (31-32), all 502 | 1.78 s after the kill | 24.6 ms | 24.2 ms |
| 3 retry | **0** (0-0) | none | 44.4 ms | 24.9 ms |
| 4 frozen | **48** (48-49), no answer at all, client gave up | 2.8 s after the freeze | 10,011 ms | 24.5 ms |
| 5 timeout | **0** (0-0) | none | 525.6 ms | 524.8 ms |
| 6a no drain | **36** (35-36), all 502 | 1.84 s after the kill | 2,006 ms | 24.7 ms |
| 6b drain | **0** (0-0) | none (TERM sent at ~11.8 s, after the drain) | 2,007 ms | 25.1 ms |

\* p99 of the normal (`/`) requests that succeeded.

What this shows:
- **Plain round robin never notices** that a server died. It keeps sending every third request to it until the test ends.
- **Health checks** cut the damage to about 1.8 s, because it takes 2 missed checks, 1 s apart, to take the server out.
- **Retry once** hides even that window: 0 failures.
- A **frozen** server is worse than a dead one. Retry doesn't help, because the request never fails, it just never ends. Users waited until their own 10 s limit.
- A **0.5 s timeout** fixes it (0 failures), but some users now wait ~0.5 s, and the p99 goes from ~25 ms to ~525 ms.
- **Draining before a deploy** gets you 0 failures, while stopping the server straight away gives 36.
- Load share: about 1/3 per server before the kill, and 1/2 per survivor after (run 3: 375 + 375 requests in the last 15 s).

Your numbers will differ a little. The pattern should not.

## Careful: retries aren't always safe
After a timeout, the first server might have **done the work and just been slow**. Retrying a `POST /pay` can charge someone twice.
Only retry requests that are safe to repeat (idempotent), like `GET`, or use an idempotency key.

## Limits of this lab
- One laptop, localhost only. There's no real network, so no packet loss and no latency between machines.
- `lb.py` is a short teaching load balancer. Real ones (nginx, HAProxy, Envoy, cloud LBs) have more health-check settings, connection pooling, and so on.
- `kill -STOP` simulates a frozen process. Real freezes (GC pauses, a deadlock, a full disk) look similar from the outside.
