# Two alternatives per key part

Measured numbers come from `results/runtime_vs_size.csv` (best of 3, seconds). Rows marked "not measured" are design arguments only.

| Part | Alternative A | Alternative B | Chosen | Why |
|---|---|---|---|---|
| Restocking order | FCFS: serve stations in the order they ran low | Shortage queue: min-heap keyed (-priority, distance), `modules/routing.py::plan_route` | B | Serves the emptiest stations first, so fewer stock-outs. Heap pop is O(log n), whole plan O(n log n). FCFS vs heap on stock-outs: not measured; only baseline (no rebalancing) vs rebalanced is in `results/pilot_baseline_vs_rebalanced.md` (0.233 -> 0.140). |
| Nearest station | Linear scan, O(n) | k-d tree, build O(n log n), query about O(log n), `utils/structures.py::KDTree` | Scan in the live route | At n = 20,000: scan 50.8 ms, tree query 0.026 ms, but tree build 165 ms. A per-request build costs more than the scan. The tree pays off only if kept in memory at startup (future work). |
| Demand forecast | Seasonal mean (`SeasonalBaseline`) | XGBoost with weather features (`XGBDemand`) | XGBoost when installed, else seasonal mean | `forecast_engine.train(prefer_xgb=True)` falls back automatically, so the API runs without the ML extras. Accuracy comparison (MAE) of the two: not measured. |
| Payments | Sandbox gateway (`integrations/payment_gateway.py`) | Real gateway | Sandbox | No merchant account; a real gateway returns HTTP 501 "not configured". Real gateway is future work. |
| Priority queue | Sorting the list each time, O(n log n) per pick | Own binary `MinHeap`, O(log n) per push/pop | MinHeap | Own data structure as the CEP requires; tested in `tests/test_structures.py`. |
