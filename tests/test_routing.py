from modules.routing import StationState, plan_route, priority, shortage


def test_priority_and_shortage():
    assert shortage(8, 3) == 5 and shortage(2, 5) == 0
    assert priority(10, 5) == 0.5 and priority(0, 0) == 0.0


def test_route_visits_highest_priority_first_and_respects_van_load():
    st = [
        StationState("A", 24.80, 67.00, target=10, current=9),   # priority 0.1
        StationState("B", 24.90, 67.10, target=10, current=2),   # priority 0.8
        StationState("C", 24.81, 67.01, target=10, current=5),   # priority 0.5
        StationState("D", 24.82, 67.02, target=10, current=10),  # no shortage, skipped
    ]
    route = plan_route(24.85, 67.05, 9, st, van_capacity=20)
    assert [s.station_id for s in route] == ["B", "C"]  # van empties after C
    assert sum(s.drop for s in route) <= 9
    assert route[0].drop == 8  # B is short by 8 and the van has 9


def test_empty_van_makes_no_stops():
    assert plan_route(24.85, 67.05, 0, [StationState("A", 24.8, 67.0, 5, 0)]) == []
