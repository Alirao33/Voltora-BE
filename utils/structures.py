"""Hand-written data structures for the CEP: a binary min-heap and a 2-D k-d tree (no library calls)."""

from __future__ import annotations

from typing import Any, Callable, Optional


class MinHeap:
    """Array-backed binary min-heap. push/pop are O(log n), peek is O(1), build from a list is O(n log n) here."""

    def __init__(self) -> None:
        self.a: list[Any] = []

    def __len__(self) -> int:
        return len(self.a)

    def push(self, item: Any) -> None:
        a = self.a
        a.append(item)
        i = len(a) - 1
        while i > 0:  # sift up
            parent = (i - 1) // 2
            if a[i] >= a[parent]:
                break
            a[i], a[parent] = a[parent], a[i]
            i = parent

    def peek(self) -> Any:
        return self.a[0]

    def pop(self) -> Any:
        a = self.a
        top, last = a[0], a.pop()
        if a:
            a[0] = last
            i, n = 0, len(a)
            while True:  # sift down
                l, r, m = 2 * i + 1, 2 * i + 2, i
                if l < n and a[l] < a[m]:
                    m = l
                if r < n and a[r] < a[m]:
                    m = r
                if m == i:
                    break
                a[i], a[m] = a[m], a[i]
                i = m
        return top


class KDTree:
    """Static 2-D k-d tree over (x, y, payload). Build O(n log n); nearest and radius search O(log n) on average.
    shortcut: Euclidean on raw lat/lon degrees, fine for a ~50 km city; use haversine at the leaves for exact km."""

    class _Node:
        __slots__ = ("pt", "axis", "left", "right")

        def __init__(self, pt, axis, left, right):
            self.pt, self.axis, self.left, self.right = pt, axis, left, right

    def __init__(self, points: list[tuple[float, float, Any]]) -> None:
        self.root = self._build(list(points), 0)

    def _build(self, pts, depth) -> Optional["KDTree._Node"]:
        if not pts:
            return None
        axis = depth % 2
        pts.sort(key=lambda p: p[axis])
        m = len(pts) // 2
        return self._Node(pts[m], axis, self._build(pts[:m], depth + 1), self._build(pts[m + 1:], depth + 1))

    @staticmethod
    def _d2(a, b) -> float:
        return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2

    def nearest(self, x: float, y: float):
        best: list = [None, float("inf")]

        def go(n):
            if n is None:
                return
            d = self._d2(n.pt, (x, y))
            if d < best[1]:
                best[0], best[1] = n.pt, d
            diff = (x, y)[n.axis] - n.pt[n.axis]
            near, far = (n.left, n.right) if diff < 0 else (n.right, n.left)
            go(near)
            if diff * diff < best[1]:  # the other side can still hold something closer
                go(far)

        go(self.root)
        return best[0]

    def within(self, x: float, y: float, radius: float) -> list:
        out: list = []
        r2 = radius * radius

        def go(n):
            if n is None:
                return
            if self._d2(n.pt, (x, y)) <= r2:
                out.append(n.pt)
            diff = (x, y)[n.axis] - n.pt[n.axis]
            if diff - radius < 0:
                go(n.left)
            if diff + radius >= 0:
                go(n.right)

        go(self.root)
        return out
