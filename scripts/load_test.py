"""Load test for the public read endpoints (stdlib only).
Start the API first (python main.py), then:
  python scripts/load_test.py [base_url] [concurrency] [seconds]
Writes results/load_test.md. Exit code 1 if error rate > 1% or p95 > 500 ms."""
import os
import statistics
import sys
import threading
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
USERS = int(sys.argv[2]) if len(sys.argv) > 2 else 20
SECONDS = float(sys.argv[3]) if len(sys.argv) > 3 else 15
PATHS = ["/health/live", "/api/guest/stations", "/api/guest/plans"]  # shortcut: public GETs only, add auth flows when needed

lat: dict[str, list[float]] = {p: [] for p in PATHS}
errors: dict[str, int] = {p: 0 for p in PATHS}
lock = threading.Lock()


def worker(stop: float, k: int) -> None:
    i = k
    while time.time() < stop:
        p = PATHS[i % len(PATHS)]; i += 1
        s = time.perf_counter()
        try:
            with urllib.request.urlopen(BASE + p, timeout=10) as r:
                r.read(); ok = r.status == 200
        except Exception:
            ok = False
        d = (time.perf_counter() - s) * 1000
        with lock:
            lat[p].append(d)
            if not ok:
                errors[p] += 1


def pct(v: list[float], q: float) -> float:
    return sorted(v)[min(len(v) - 1, int(len(v) * q))]


t0 = time.time()
ts = [threading.Thread(target=worker, args=(t0 + SECONDS, k)) for k in range(USERS)]
[t.start() for t in ts]; [t.join() for t in ts]
wall = time.time() - t0

rows = ["| endpoint | requests | errors | p50 ms | p95 ms | p99 ms |", "|---|---|---|---|---|---|"]
total = err = 0
worst_p95 = 0.0
for p in PATHS:
    v = lat[p]
    if not v:
        continue
    total += len(v); err += errors[p]; worst_p95 = max(worst_p95, pct(v, .95))
    rows.append(f"| {p} | {len(v)} | {errors[p]} | {statistics.median(v):.1f} | {pct(v, .95):.1f} | {pct(v, .99):.1f} |")
rows.append(f"\n{USERS} concurrent users, {wall:.0f} s, {total / wall:.1f} requests/s, error rate {err / max(total, 1):.2%}.")
out = "\n".join(rows)
print(out)
d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")
os.makedirs(d, exist_ok=True)
open(os.path.join(d, "load_test.md"), "w").write(out + "\n")
sys.exit(1 if total == 0 or err / total > 0.01 or worst_p95 > 500 else 0)
