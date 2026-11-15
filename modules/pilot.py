"""Business logic: pilot."""

from __future__ import annotations

import math
import random
from typing import Any

from constants.forecasting import AVG_PRICE_PKR
from modules.routing import plan_route, StationState
from utils.geo import haversine_km

def _poisson(rng: random.Random, lam: float) -> int:
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1


def run_pilot(
    *,
    n_stations: int = 6,
    days: int = 7,
    seed: int = 42,
    van_capacity: int = 20,
    stock_per_station: int = 8,
    charge_per_hour: int = 2,
) -> dict[str, Any]:
    rng = random.Random(seed)
    stations = [
        {
            "id": f"S{i + 1}",
            "lat": 24.80 + rng.random() * 0.15,
            "lon": 67.00 + rng.random() * 0.15,
            "pop": 0.5 + rng.random() * 2.0,
        }
        for i in range(n_stations)
    ]
    profile = [0.2] * 6 + [1.0, 2.5, 3.0, 1.8, 1.2, 1.2, 1.5, 1.2, 1.2, 1.6, 2.4, 3.0, 2.6, 1.6, 0.9, 0.5]
    profile = (profile + [0.3] * 24)[:24]
    trace = [[_poisson(rng, s["pop"] * profile[h % 24]) for s in stations] for h in range(days * 24)]

    def simulate(rebalance: bool) -> dict[str, Any]:
        stock = [stock_per_station] * n_stations
        depleted = [0] * n_stations
        demand_total = served_total = 0
        km = 0.0
        availability = []

        for h, row in enumerate(trace):
            if rebalance and h % 24 == 5:
                states = []
                for i, s in enumerate(stations):
                    exp = sum(s["pop"] * profile[(h + k) % 24] for k in range(1, 13))
                    states.append(
                        StationState(
                            s["id"],
                            s["lat"],
                            s["lon"],
                            min(stock_per_station * 2, math.ceil(exp * 1.2)),
                            stock[i],
                            depleted[i],
                        )
                    )
                route = plan_route(24.85, 67.05, van_capacity, states, van_capacity)
                pos = (24.85, 67.05)
                for st in route:
                    i = int(st.station_id[1:]) - 1
                    stock[i] += st.drop
                    depleted[i] -= min(depleted[i], st.pickup)
                    km += haversine_km(pos[0], pos[1], stations[i]["lat"], stations[i]["lon"])
                    pos = (stations[i]["lat"], stations[i]["lon"])

            for i, d in enumerate(row):
                served = min(d, stock[i])
                stock[i] -= served
                depleted[i] += served
                demand_total += d
                served_total += served
                back = min(depleted[i], charge_per_hour)
                depleted[i] -= back
                stock[i] += back
                availability.append(stock[i] / stock_per_station)

        return {
            "stockout_rate": round(1 - served_total / demand_total, 4) if demand_total else 0.0,
            "avg_availability": round(sum(availability) / len(availability), 4),
            "van_km": round(km, 1),
            "revenue_pkr": round(served_total * AVG_PRICE_PKR, 2),
            "demand": demand_total,
            "served": served_total,
        }

    base, reb = simulate(False), simulate(True)
    return {
        "baseline": base,
        "rebalanced": reb,
        "improvement": {
            "stockout_rate_delta": round(base["stockout_rate"] - reb["stockout_rate"], 4),
            "revenue_delta_pkr": round(reb["revenue_pkr"] - base["revenue_pkr"], 2),
        },
    }
