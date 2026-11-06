#!/bin/sh
# Where do the ~800 ms after the first byte come from? Is it TCP slow start?
# Same sandbox + same 200 ms far link as run.sh, origin only. Fetches /app.js 5 times with Linux defaults, then
# with a big initial congestion window (B, sender side = slow start), both windows (C), only the receive window (D).
# E = a big receive buffer (4 MB default instead of 128 KB), F = E + both big windows.
# Whichever change makes the extra round trips disappear is the cause.
#   sh slowstart_check.sh      -> runs/slowstart-<date-time>/check.log
set -e
cd "$(dirname "$0")"
mkdir -p runs
OUT=runs/slowstart-$(date +%Y%m%d-%H%M%S)
mkdir "$OUT"
exec unshare -rn sh -c '
set -e
OUT=$1
ip link set lo up
ip link set lo mtu 1500
tc qdisc add dev lo root netem delay 100ms limit 100000
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj /CN=127.0.0.1 -keyout "$OUT/key.pem" -out "$OUT/cert.pem" 2>/dev/null
mkdir "$OUT/www"
python3 -c "
line = \"function render(item){return \x27<li>\x27+item.name+\x27 \x27+item.price+\x27</li>\x27;}\n\"
open(\"$OUT/www/app.js\", \"w\").write(\"// version 1\n\" + line * 2800)"
python3 origin.py --port 9001 --www "$OUT/www" --cert "$OUT/cert.pem" --key "$OUT/key.pem" & PID=$!
sleep 1
fetch() { for i in 1 2 3 4 5; do curl -sk --http1.1 --noproxy "*" -o /dev/null -w "$1  first byte %{time_starttransfer}s  total %{time_total}s  bytes %{size_download}\n" https://127.0.0.1:9001/app.js; done; }
{
echo "lo: $(ip -o link show lo | grep -o "mtu [0-9]*")   delay: 100 ms each way (200 ms round trip)"
echo "route: $(ip route show table local | grep "local 127.0.0.1 ")"
echo "tcp_rmem: $(cat /proc/sys/net/ipv4/tcp_rmem)  initial window (tcp_init_cwnd default 10)"
fetch "A defaults            "
ip route replace local 127.0.0.1 dev lo table local proto kernel scope host src 127.0.0.1 initcwnd 300
echo "route: $(ip route show table local | grep "local 127.0.0.1 ")"
fetch "B initcwnd 300        "
ip route replace local 127.0.0.1 dev lo table local proto kernel scope host src 127.0.0.1 initcwnd 300 initrwnd 300
echo "route: $(ip route show table local | grep "local 127.0.0.1 ")"
fetch "C + initrwnd 300      "
ip route replace local 127.0.0.1 dev lo table local proto kernel scope host src 127.0.0.1 initrwnd 300
echo "route: $(ip route show table local | grep "local 127.0.0.1 ")"
fetch "D initrwnd 300 only   "
ip route replace local 127.0.0.1 dev lo table local proto kernel scope host src 127.0.0.1
echo "4096 4194304 33554432" > /proc/sys/net/ipv4/tcp_rmem
echo "route: $(ip route show table local | grep "local 127.0.0.1 ")  tcp_rmem: $(cat /proc/sys/net/ipv4/tcp_rmem)"
fetch "E big rcv buffer only "
ip route replace local 127.0.0.1 dev lo table local proto kernel scope host src 127.0.0.1 initcwnd 300 initrwnd 300
echo "route: $(ip route show table local | grep "local 127.0.0.1 ")  tcp_rmem: $(cat /proc/sys/net/ipv4/tcp_rmem)"
fetch "F E + initcwnd/rwnd   "
} | tee "$OUT/check.log"
kill $PID
' sh "$OUT"
