from constants.enums import Chemistry
from modules.battery_health import health_score, soc_pct, soh_pct


def test_soc_soh():
    assert soc_pct(50, 100) == 50 and soh_pct(90, 100) == 90


def test_health_formula_perfect_pack():
    r = health_score(soh=100, temperature_c=25, cycles=0, faults=0, operating_hours=0, chemistry=Chemistry.LFP)
    assert r.score == 100.0


def test_hot_worn_pack_scores_lower():
    good = health_score(soh=95, temperature_c=25, cycles=50, faults=0, operating_hours=100, chemistry=Chemistry.LFP)
    bad = health_score(soh=80, temperature_c=58, cycles=2500, faults=3, operating_hours=100, chemistry=Chemistry.LFP)
    assert bad.score < good.score
