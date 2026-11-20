from datetime import datetime

from modules.forecast_engine import Row, SeasonalBaseline, target_stock


def test_seasonal_baseline_uses_same_weekday_hour_mean():
    ts = datetime(2026, 11, 16, 9)  # a Monday 09:00
    m = SeasonalBaseline().fit([Row("S1", ts, 4.0), Row("S1", ts, 6.0)])
    assert m.predict("S1", ts) == 5.0
    assert m.predict("S1", datetime(2026, 11, 17, 9)) == 5.0  # unseen cell falls back to the station mean


def test_target_stock_has_safety_margin_and_floor():
    assert target_stock(10, capacity=50) >= 12
    assert target_stock(0, capacity=50) == 2
