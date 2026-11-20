"""Writes the CEP result files into results/: runtime vs size plot + CSV, and the pilot baseline vs rebalanced table.
Run from the backend folder:  python scripts/benchmarks.py   (needs matplotlib)"""
import csv
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite://")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from modules.pilot import run_pilot
from modules.routing import StationState, plan_route
from utils.geo import haversine_km
from utils.structures import KDTree

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")
os.makedirs(OUT, exist_ok=True)


def best_of(fn, reps=3):
    t = []
    for _ in range(reps):
        s = time.perf_counter(); fn(); t.append(time.perf_counter() - s)
    return min(t)


def runtime_vs_size():
    rng = random.Random(7)
    rows = []
    for n in (10, 100, 1000, 5000, 20000):
        pts = [(24.7 + rng.random() * 0.4, 66.9 + rng.random() * 0.4, i) for i in range(n)]
        states = [StationState(f"S{i}", p[0], p[1], 10, rng.randint(0, 10)) for i, p in enumerate(pts)]
        q = (24.86, 67.05)
        tree = KDTree(pts)
        scan = best_of(lambda: min(pts, key=lambda p: haversine_km(q[0], q[1], p[0], p[1])))
        kd = best_of(lambda: [tree.nearest(*q) for _ in range(100)]) / 100
        build = best_of(lambda: KDTree(pts), 1)
        route = best_of(lambda: plan_route(q[0], q[1], 10 ** 6, states))
        rows.append(dict(n=n, linear_scan_s=scan, kdtree_query_s=kd, kdtree_build_s=build, plan_route_s=route))
    with open(os.path.join(OUT, "runtime_vs_size.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    ns = [r["n"] for r in rows]
    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    ax[0].loglog(ns, [r["linear_scan_s"] for r in rows], "o-", label="linear scan  O(n)")
    ax[0].loglog(ns, [r["kdtree_query_s"] for r in rows], "s-", label="k-d tree query  ~O(log n)")
    ax[0].set(xlabel="stations (n)", ylabel="seconds per query", title="Nearest station: measured")
    ax[0].legend(); ax[0].grid(True, which="both", alpha=.3)
    base = rows[0]["plan_route_s"] / (ns[0] * math.log2(ns[0]))
    ax[1].loglog(ns, [r["plan_route_s"] for r in rows], "o-", label="plan_route (heap) measured")
    ax[1].loglog(ns, [base * n * math.log2(n) for n in ns], "--", label="theory  O(n log n)")
    ax[1].set(xlabel="stations (n)", ylabel="seconds", title="Route planning: measured vs theory")
    ax[1].legend(); ax[1].grid(True, which="both", alpha=.3)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "runtime_vs_size.png"), dpi=150)
    return rows


def pilot_table():
    keys = ("stockout_rate", "avg_availability", "van_km", "served", "revenue_pkr")
    res = [run_pilot(seed=s) for s in (1, 2, 3, 4, 5)]
    lines = ["| metric | baseline (no rebalancing) | rebalanced (forecast + route) |", "|---|---|---|"]
    for k in keys:
        b = sum(r["baseline"][k] for r in res) / len(res); m = sum(r["rebalanced"][k] for r in res) / len(res)
        lines.append(f"| {k} | {b:,.3f} | {m:,.3f} |")
    lines.append("\nMean of 5 seeds (1 to 5), 6 stations, 7 days, van capacity 20, defaults of modules/pilot.py run_pilot.")
    open(os.path.join(OUT, "pilot_baseline_vs_rebalanced.md"), "w").write("\n".join(lines))
    return "\n".join(lines)


if __name__ == "__main__":
    for r in runtime_vs_size():
        print({k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items()})
    print(pilot_table())
