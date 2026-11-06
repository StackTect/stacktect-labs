#!/bin/sh
# Simulated CDN test, all on this laptop, no sudo, no installs:
#   sh run.sh [far_one_way_ms=100] [near_one_way_ms=5] [runs=20]
# Makes a NEW folder runs/<date-time>/ (results.json, run.log, tc.txt); never overwrites an old run.
set -e
cd "$(dirname "$0")"
FAR=${1:-100}; NEAR=${2:-5}; N=${3:-20}
mkdir -p runs
OUT=runs/$(date +%Y%m%d-%H%M%S)
mkdir "$OUT"
exec unshare -rn sh inner.sh "$OUT" "$FAR" "$NEAR" "$N"
