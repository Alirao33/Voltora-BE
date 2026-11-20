import random

from utils.geo import haversine_km
from utils.structures import KDTree, MinHeap


def test_heap_pops_in_sorted_order():
    rng = random.Random(1)
    xs = [rng.random() for _ in range(500)]
    h = MinHeap()
    for x in xs:
        h.push(x)
    assert [h.pop() for _ in range(len(xs))] == sorted(xs)


def test_heap_peek_and_empty():
    h = MinHeap()
    h.push(3); h.push(1); h.push(2)
    assert h.peek() == 1 and len(h) == 3


def test_kdtree_matches_brute_force():
    rng = random.Random(2)
    pts = [(24.8 + rng.random() * 0.2, 67.0 + rng.random() * 0.2, i) for i in range(400)]
    t = KDTree(pts)
    for _ in range(50):
        q = (24.8 + rng.random() * 0.2, 67.0 + rng.random() * 0.2)
        brute = min(pts, key=lambda p: (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2)
        assert t.nearest(*q)[2] == brute[2]
        r = 0.03
        assert {p[2] for p in t.within(q[0], q[1], r)} == {p[2] for p in pts if (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 <= r * r}


def test_haversine_known_distance():
    # Saddar to Clifton is roughly 5 to 8 km
    assert 4 < haversine_km(24.8607, 67.0099, 24.8138, 67.0300) < 8
