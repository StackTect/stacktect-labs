# Does a CDN Make Your Site Faster? I Built One to Find Out

Video: [Does a CDN Make Your Site Faster? I Built One to Find Out](https://youtu.be/fctvxCplKnE)

In this lab you build a tiny CDN on your own laptop: an origin server ("the warehouse"), a small caching edge server
("the shop down the road") and a measuring script that fetches the same files directly and through the edge.
You'll see a cache HIT, a MISS, what a CDN does for requests it can't cache, and how a CDN can keep serving an old file after you deploy.

**The distance is simulated.** Everything runs on one laptop, and Linux adds a fixed delay to the traffic. No cloud account,
no sudo, nothing to install beyond standard Linux tools.

## How the distance is simulated
`run.sh` starts a private network sandbox (`unshare -rn`: its own loopback, nothing reaches your real network) and runs
`inner.sh` inside it. Linux `tc netem` adds a delay per port inside that sandbox, so **real kernel TCP and real TLS 1.3
handshakes** pay the delay:

| Link | Delay | Round trip | Think of it as |
|---|---|---|---|
| far: you <-> origin, and edge <-> origin | 100 ms each way | **200 ms** | India to a server on the US east coast |
| near: you <-> edge | 5 ms each way | **10 ms** | a CDN location in your own city |

The loopback MTU is set to 1500 (internet-sized packets), so TCP's slow start over the far link is real too.

## What's in `code/`

| File | What it does |
|---|---|
| `origin.py` | The origin, HTTPS + HTTP/1.1. `/app.js` (~200 KB, `Cache-Control: public, max-age=86400`), `/api/time` and `/api/slow` (`no-store`; `/api/slow` does 500 ms of pretend work). |
| `edge.py` | A small caching proxy written for this lab (**not production code**). It stores only what `Cache-Control` allows, sends `X-Cache: HIT/MISS` and `Age`, streams misses, and keeps warm connections to the origin. `--cold` opens a new origin connection for every miss instead. |
| `measure.py` | Runs every test case with `curl`, 20 times each. A new curl process every time = a fresh connection every time. Writes `run.log` and `results.json`. |
| `run.sh` | Starts the sandbox and the whole test: `sh run.sh [far_one_way_ms=100] [near_one_way_ms=5] [runs=20]` |
| `inner.sh` | Runs inside the sandbox: sets the MTU and the `tc netem` delays, makes a test certificate, starts `measure.py`. |
| `slowstart_check.sh` | Extra check: where do the ~800 ms *after* the first byte come from? (see below) |

## Setup

You need **Linux** (we used Fedora 44, kernel 7.2), Python 3.8+ (we used 3.14), `curl`, `openssl` and `iproute2` (`ip`, `tc`).
macOS and Windows don't have `unshare` or `tc netem`; use a Linux VM.

```bash
git clone https://github.com/StackTect/stacktect-labs.git
cd stacktect-labs/videos/2026-11-07-tiny-cdn/code
sh run.sh            # about 2 minutes
```

Every run makes a new folder `code/runs/<date-time>/` with `run.log`, `results.json` and `tc.txt`. Old runs are never overwritten.

If it fails:
- `unshare: ... Operation not permitted`: your system blocks unprivileged user namespaces. On Ubuntu 23.10+ this is an
  AppArmor setting (`kernel.apparmor_restrict_unprivileged_userns`). Run it in a VM or container where they are allowed.
- `Specified qdisc kind is unknown` / `netem` errors: the `sch_netem` kernel module is missing. On Fedora it is in
  `kernel-modules-extra`; on most other distros it comes with the kernel.

Try other distances: `sh run.sh 40 5` (80 ms round trip) or `sh run.sh 150 2`.

## The test cases

| Case | What happens |
|---|---|
| `/app.js` direct | Straight to the far origin. New connection: TCP handshake + TLS 1.3 handshake + the request = 3 far round trips before the first byte. |
| `/app.js` CDN HIT | The edge already has a copy. Everything happens over the near link. |
| `/app.js` CDN MISS, warm edge | A new URL each time (`?v=...`), so the edge must fetch it. It reuses an open connection to the origin. |
| `/app.js` CDN MISS, cold edge | Same, but the edge opens a new connection to the origin for every miss (`--cold`). |
| `/api/time` direct and via CDN | `no-store`: the edge may never keep it. |
| `/api/slow` direct and via CDN | `no-store` + 500 ms of work on the origin. |
| Stale file | Deploy version 2 of `app.js` while the edge still holds version 1, then try a fingerprinted name (`app.<hash>.js`). |

## Our results

Measured on 2026-11-05 on one Linux laptop (16 CPU threads, Fedora 44, Python 3.14.8, curl 8.18.0, OpenSSL 3.5.9).
20 runs per case, **median total time** in ms with the min-max range. Full numbers: [`results/results.json`](results/results.json),
raw log: [`results/run-log.txt`](results/run-log.txt), the `tc` settings: [`results/tc.txt`](results/tc.txt).

| Case | Total | Range | Where the time goes |
|---|---|---|---|
| `/app.js` direct | **1,412.6** | 1,213.7-1,612.3 | connect 200.5, TLS done 410.0, first byte 611.9 (3 round trips), then ~800 ms of download |
| `/app.js` CDN HIT | **71.7** | 67.3-83.4 | connect 10.3, TLS 28.2, first byte 39.4. About **20x faster** |
| `/app.js` CDN MISS, warm edge | **284.1** | 274.6-854.4 | first byte 242.8: one far round trip on the edge's open connection. Still faster than direct |
| `/app.js` CDN MISS, cold edge | **1,447.2** | 1,444.6-1,651.8 | the edge opens a new far connection for each miss: slightly **slower** than going direct |
| `/api/time` direct | **612.3** | 608.7-612.8 | 3 far round trips |
| `/api/time` CDN | **242.3** | 238.8-244.2 | `X-Cache: MISS` on all 20 (no-store), but the handshakes end at the edge: one far trip left |
| `/api/slow` direct | **1,112.2** | 1,110.0-1,114.2 | 612 + 500 ms of work |
| `/api/slow` CDN | **743.2** | 739.3-744.7 | 242 + 500 ms of work: the trips got shorter, the 500 ms stayed |

**Stale file:** after deploying version 2 of `app.js`, the edge still served **version 1, `X-Cache: HIT`, `Age: 103`**,
because `max-age` says it may keep the copy for a day. The fingerprinted `app.a41d2543.js` served version 2 straight away
(MISS, then HIT). That's why real sites put a hash in the file name.

What this shows:
- A **HIT** is fast because every round trip is short, not because the file is smaller.
- A **MISS** still has to go to the origin. With warm connections it costs one far round trip; with a cold edge it can be slower than going direct. Watch your hit ratio.
- Even **uncacheable** requests got faster here (612 -> 242 ms), because the TCP and TLS handshakes end at the nearby edge.
- A CDN can't fix **slow code**: the 500 ms of work is still there.
- Caching means users can get an **old file** after a deploy. Fingerprinted file names avoid that.

Your numbers will differ a little. The pattern should not.

## Extra check: the ~800 ms after the first byte
`sh slowstart_check.sh` fetches `/app.js` from the origin over the same 200 ms link with different TCP settings
(5 fetches each, inside the same kind of sandbox). Our run: [`results/slowstart-check.log`](results/slowstart-check.log).

| Case | Total | Extra round trips after the first byte |
|---|---|---|
| A Linux defaults (initial congestion window 10 packets, receive buffer 128 KB) | 1.41 s | 4 |
| B sender's initial window 300 packets (no slow start) | 1.01 s | 2 |
| D receiver's initial window 300 only | 1.41 s | 4 |
| E receive buffer 4 MB only | 1.21 s | 3 |
| F both windows open | 0.61 s | 0 (the whole file arrives with the first byte) |

So the download time is TCP's **two windows** growing one round trip at a time: the sender's congestion window (slow start)
and the receiver's window. Both behave this way on the real internet.

## Limits of this lab
- The distance is **simulated**: a fixed delay, no packet loss, no jitter, and no bandwidth limit apart from the TCP windows.
- `edge.py` is a toy: one location, no request collapsing, no TLS session resumption, the query string is part of the cache key.
- The numbers are for these delays. With other delays the ratios change. The physics (3 round trips before the first byte on a
  new HTTPS connection with TCP + TLS 1.3) is the same as on the real internet.
- Real CDNs (Cloudflare, CloudFront, Fastly, Akamai...) have many locations, tiered caches, HTTP/2 and HTTP/3, and much more.
