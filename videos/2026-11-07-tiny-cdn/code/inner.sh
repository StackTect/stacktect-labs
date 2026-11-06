#!/bin/sh
# Runs INSIDE the network sandbox made by run.sh (own loopback, nothing reaches the real network).
set -e
OUT=$1; FAR=$2; NEAR=$3; N=$4
ip link set lo up
ip link set lo mtu 1500                      # internet-like packet size (loopback default is 65536)
tc qdisc add dev lo root handle 1: prio bands 3 priomap 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2
tc qdisc add dev lo parent 1:1 handle 10: netem delay "${FAR}ms" limit 100000
tc qdisc add dev lo parent 1:2 handle 20: netem delay "${NEAR}ms" limit 100000
# far link = anything to or from the origin :9001; near link = laptop <-> edges :9002 (warm) and :9003 (cold)
for p in 9001; do
  tc filter add dev lo parent 1: protocol ip u32 match ip dport $p 0xffff flowid 1:1
  tc filter add dev lo parent 1: protocol ip u32 match ip sport $p 0xffff flowid 1:1
done
for p in 9002 9003; do
  tc filter add dev lo parent 1: protocol ip u32 match ip dport $p 0xffff flowid 1:2
  tc filter add dev lo parent 1: protocol ip u32 match ip sport $p 0xffff flowid 1:2
done
tc qdisc show dev lo > "$OUT/tc.txt"
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj /CN=127.0.0.1 -keyout "$OUT/key.pem" -out "$OUT/cert.pem" 2>/dev/null
python3 measure.py "$OUT" "$FAR" "$NEAR" "$N"
