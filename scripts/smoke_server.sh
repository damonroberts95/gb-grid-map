#!/usr/bin/env bash
# Smoke test: start the server (no scheduler, so no network), curl the contract endpoints, stop it.
# Needs data/ built (python -m gridmap.refresh). Usage: scripts/smoke_server.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PORT=8787
B=http://127.0.0.1:$PORT
GRIDMAP_NO_SCHEDULER=1 uv run uvicorn gridmap.server.app:app --host 127.0.0.1 --port $PORT --log-level warning &
PID=$!
trap 'kill $PID 2>/dev/null || true' EXIT
for _ in $(seq 60); do curl -sf $B/api/manifest >/dev/null 2>&1 && break; sleep 0.5; done

echo "== manifest";   curl -sf $B/api/manifest; echo
LV=$(curl -s $B/api/manifest | python3 -c "import sys,json;print(json.load(sys.stdin)['layers']['lv'])")
SID=$(curl -s "$B/api/search?q=beauly" | python3 -c "import sys,json;print(next(r['id'] for r in json.load(sys.stdin) if r['type']=='site' and r['op']!='NESO'))")
echo "== site $SID"; curl -sf "$B/api/site/$SID" | python3 -c "
import sys,json;d=json.load(sys.stdin);print({k:(len(v) if isinstance(v,(list,dict)) else v) for k,v in d.items()})"
echo "== search";   curl -sf "$B/api/search?q=beau&limit=3" | head -c 300; echo
echo "== outages";  curl -sf $B/api/live/outages | python3 -c "
import sys,json,collections;d=json.load(sys.stdin);print(len(d['features']),dict(collections.Counter(f['properties']['op'] for f in d['features'])))"
echo "== national"; curl -sf $B/api/live/national | head -c 300; echo
echo "== range $LV"; curl -s -D - -o /dev/null -H "Range: bytes=0-15" "$B$LV" | grep -i -E "^HTTP|content-range"
curl -s -H "Range: bytes=0-6" "$B$LV"; echo
echo "== sse first event"; curl -sN -m 2 $B/api/events | head -n 2 | cut -c1-160 || true
echo "== latency (ms, 30 calls each)"
for u in "api/site/$SID" "api/search?q=beau" "api/search?q=1171199"; do
  python3 - "$B/$u" <<'P'
import sys,time,urllib.request
u=sys.argv[1].replace(" ","%20");t=[]
for _ in range(30):
    s=time.perf_counter();urllib.request.urlopen(u).read();t.append((time.perf_counter()-s)*1000)
t.sort();print(f"{u.split('8787/')[1]:32} median {t[15]:.1f}  p95 {t[28]:.1f}  max {t[-1]:.1f}")
P
done
echo OK
