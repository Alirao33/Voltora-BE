"""Business logic: routing."""

from __future__ import annotations

from dataclasses import dataclass

from utils.geo import haversine_km
from utils.structures import MinHeap

def shortage(target: int, current: int) -> int:
    return max(0, target - current)


def priority(target: int, current: int) -> float:
    return shortage(target, current) / target if target > 0 else 0.0


@dataclass
class StationState:
    id: str
    lat: float
    lon: float
    target: int
    current: int          # charged packs in stock
    depleted: int = 0     # depleted packs waiting for pick-up


@dataclass
class Stop:
    station_id: str
    drop: int
    pickup: int
    priority: float
    shortage: int
    distance_km: float


def plan_route(
    van_lat: float,
    van_lon: float,
    van_packs: int,
    stations: list[StationState],
    van_capacity: int = 10**6,
    dist=haversine_km,
) -> list[Stop]:
    """Build shortage queue sorted by priority DESC then distance ASC; allocate drops & pickups along route."""
    # Min-heap keyed (-priority, distance, id): pops the highest priority first, nearest first on ties.
    heap = MinHeap()
    for s in stations:
        sh = shortage(s.target, s.current)
        if sh > 0:
            heap.push((-priority(s.target, s.current), dist(van_lat, van_lon, s.lat, s.lon), s.id, sh, s))

    route: list[Stop] = []
    pos = (van_lat, van_lon)
    on_board = van_packs
    picked = 0

    while len(heap):
        npr, _, _, sh, s = heap.pop()
        pr = -npr
        if on_board <= 0:
            break
        drop = min(sh, on_board)
        on_board -= drop
        pickup = min(s.depleted, max(0, van_capacity - on_board - picked))
        picked += pickup
        route.append(
            Stop(
                s.id,
                drop,
                pickup,
                round(pr, 4),
                sh,
                round(dist(pos[0], pos[1], s.lat, s.lon), 3),
            )
        )
        pos = (s.lat, s.lon)
    return route
