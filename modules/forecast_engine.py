"""Business logic: forecast engine."""

from __future__ import annotations

import math
import pickle
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from constants.forecasting import DEFAULT_RAIN_MM, DEFAULT_TEMP_C

try:
    import numpy as np
    from xgboost import XGBRegressor
    HAS_XGB = True
except Exception:
    HAS_XGB = False


@dataclass
class Row:
    station_id: str
    ts: datetime          # hour bucket (PKT)
    demand: float         # swaps in that hour
    temp_c: float = DEFAULT_TEMP_C
    rain_mm: float = DEFAULT_RAIN_MM


class SeasonalBaseline:
    algorithm = "seasonal_mean"

    def fit(self, rows: list[Row]) -> "SeasonalBaseline":
        acc: dict = defaultdict(list)
        by_station: dict = defaultdict(list)
        for r in rows:
            acc[(r.station_id, r.ts.weekday(), r.ts.hour)].append(r.demand)
            by_station[r.station_id].append(r.demand)
        self.cell = {k: sum(v) / len(v) for k, v in acc.items()}
        self.station = {k: sum(v) / len(v) for k, v in by_station.items()}
        allv = [r.demand for r in rows]
        self.global_mean = sum(allv) / len(allv) if allv else 0.0
        return self

    def predict(
        self, station_id: str, ts: datetime, temp_c: float = DEFAULT_TEMP_C, rain_mm: float = 0.0
    ) -> float:
        return self.cell.get(
            (station_id, ts.weekday(), ts.hour),
            self.station.get(station_id, self.global_mean),
        )


class XGBDemand:
    algorithm = "xgboost"

    def fit(self, rows: list[Row]) -> "XGBDemand":
        self.index = {s: i for i, s in enumerate(sorted({r.station_id for r in rows}))}
        X = np.array([self._x(r.station_id, r.ts, r.temp_c, r.rain_mm) for r in rows])
        y = np.array([r.demand for r in rows])
        self.model = XGBRegressor(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.1,
            subsample=0.9,
            objective="reg:squarederror",
        )
        self.model.fit(X, y)
        return self

    def _x(self, station_id: str, ts: datetime, temp_c: float, rain_mm: float) -> list[float]:
        return [self.index.get(station_id, -1), ts.hour, ts.weekday(), temp_c, rain_mm]

    def predict(
        self, station_id: str, ts: datetime, temp_c: float = DEFAULT_TEMP_C, rain_mm: float = 0.0
    ) -> float:
        return max(0.0, float(self.model.predict(np.array([self._x(station_id, ts, temp_c, rain_mm)]))[0]))


def train(rows: list[Row], prefer_xgb: bool = True):
    """Time-ordered 80/20 split for MAE validation; returned model is refit on all rows."""
    rows = sorted(rows, key=lambda r: r.ts)
    cls = XGBDemand if (prefer_xgb and HAS_XGB and len(rows) >= 50) else SeasonalBaseline
    metrics: dict[str, Any] = {"n_rows": len(rows), "algorithm": cls.algorithm}
    if len(rows) >= 20:
        cut = int(len(rows) * 0.8)
        m = cls().fit(rows[:cut])
        test = rows[cut:]
        err = [abs(m.predict(r.station_id, r.ts, r.temp_c, r.rain_mm) - r.demand) for r in test]
        base = SeasonalBaseline().fit(rows[:cut])
        berr = [abs(base.predict(r.station_id, r.ts) - r.demand) for r in test]
        metrics.update(mae=round(sum(err) / len(err), 4), baseline_mae=round(sum(berr) / len(berr), 4))
    return cls().fit(rows), metrics


def save(model, path: str) -> None:
    with open(path, "wb") as f:
        pickle.dump(model, f)


def load(path: str):
    with open(path, "rb") as f:
        return pickle.load(f)


def forecast_demand(
    model, station_id: str, start: datetime, hours: int, weather: Optional[dict] = None
) -> float:
    total = 0.0
    for h in range(hours):
        ts = start + timedelta(hours=h)
        w = (weather or {}).get(ts.replace(minute=0, second=0, microsecond=0), {})
        total += model.predict(
            station_id, ts, w.get("temp_c", DEFAULT_TEMP_C), w.get("rain_mm", DEFAULT_RAIN_MM)
        )
    return total


def target_stock(predicted_demand: float, *, capacity: int, safety: float = 1.2, min_stock: int = 2) -> int:
    """S_target = predicted demand * safety factor, clamped between min_stock and station capacity."""
    return int(max(min(capacity, min_stock), min(capacity, math.ceil(predicted_demand * safety))))
