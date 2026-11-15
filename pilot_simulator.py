import random
from datetime import datetime

# ==========================================
# VOLTORA CEP PILOT EVALUATION ENGINE
# Compares "With Rebalancing" vs "Without Rebalancing"
# ==========================================

STATIONS = [
    {"id": "ST-01", "name": "Clifton Block 2", "capacity": 15, "initial_stock": 10},
    {"id": "ST-02", "name": "Gulshan 13D", "capacity": 20, "initial_stock": 4},
    {"id": "ST-03", "name": "DHA Phase 6", "capacity": 12, "initial_stock": 2},
    {"id": "ST-04", "name": "Scheme 33 City Mall", "capacity": 18, "initial_stock": 15},
    {"id": "ST-05", "name": "Malir Halt", "capacity": 15, "initial_stock": 3},
]

TOTAL_DEMAND_EVENTS = 100  # Total simulated rider swap requests
SWAP_PRICE_PKR = 120.0     # Average swap fee in PKR

def run_simulation(enable_rebalancing: bool):
    """Runs a demand trace simulation and returns operational metrics."""
    stations = {s["id"]: dict(s) for s in STATIONS}
    
    fulfilled_swaps = 0
    stockout_events = 0
    total_van_distance_km = 0.0
    van_rebalance_drops = 0
    
    for step in range(TOTAL_DEMAND_EVENTS):
        # Pick station based on Karachi traffic distribution
        station_id = random.choice(["ST-01", "ST-02", "ST-02", "ST-03", "ST-03", "ST-03", "ST-04", "ST-05"])
        st = stations[station_id]
        
        # Rider attempts a swap
        if st["initial_stock"] > 0:
            st["initial_stock"] -= 1
            fulfilled_swaps += 1
        else:
            stockout_events += 1
            
        # Greedy Van Rebalancing Trigger (every 10 demand steps)
        if enable_rebalancing and step > 0 and step % 10 == 0:
            needy_stations = [s for s in stations.values() if s["initial_stock"] < (s["capacity"] * 0.4)]
            if needy_stations:
                target_st = min(needy_stations, key=lambda x: x["initial_stock"])
                rebalance_qty = min(6, target_st["capacity"] - target_st["initial_stock"])
                target_st["initial_stock"] += rebalance_qty
                van_rebalance_drops += rebalance_qty
                total_van_distance_km += random.uniform(3.5, 8.2)

    total_revenue_pkr = fulfilled_swaps * SWAP_PRICE_PKR
    stockout_rate_pct = (stockout_events / TOTAL_DEMAND_EVENTS) * 100.0

    return {
        "fulfilled_swaps": fulfilled_swaps,
        "stockout_events": stockout_events,
        "stockout_rate_pct": round(stockout_rate_pct, 2),
        "total_revenue_pkr": round(total_revenue_pkr, 2),
        "van_rebalance_drops": van_rebalance_drops,
        "total_van_distance_km": round(total_van_distance_km, 2)
    }

def execute_pilot_audit():
    print("=" * 65)
    print("   VOLTORA CEP PILOT EVALUATION & REBALANCING AUDIT   ")
    print("=" * 65)
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Simulated Request Trace: {TOTAL_DEMAND_EVENTS} Rider Swaps across 5 Karachi Stations\n")

    baseline_results = run_simulation(enable_rebalancing=False)
    rebalanced_results = run_simulation(enable_rebalancing=True)

    print("📊 [BASELINE RUN: WITHOUT REBALANCING]")
    print(f"   • Fulfilled Swaps:      {baseline_results['fulfilled_swaps']} / {TOTAL_DEMAND_EVENTS}")
    print(f"   • Stock-Out Failures:   {baseline_results['stockout_events']}")
    print(f"   • Stock-Out Rate:       {baseline_results['stockout_rate_pct']}%")
    print(f"   • Total Gross Revenue:  PKR {baseline_results['total_revenue_pkr']:,.2f}\n")

    print("⚡ [OPTIMIZED RUN: WITH GREEDY VAN REBALANCING]")
    print(f"   • Fulfilled Swaps:      {rebalanced_results['fulfilled_swaps']} / {TOTAL_DEMAND_EVENTS}")
    print(f"   • Stock-Out Failures:   {rebalanced_results['stockout_events']}")
    print(f"   • Stock-Out Rate:       {rebalanced_results['stockout_rate_pct']}%")
    print(f"   • Total Gross Revenue:  PKR {rebalanced_results['total_revenue_pkr']:,.2f}")
    print(f"   • Rebalanced Packs:     {rebalanced_results['van_rebalance_drops']} batteries moved")
    print(f"   • Total Van Traveled:   {rebalanced_results['total_van_distance_km']} km\n")

    stockout_reduction = baseline_results['stockout_rate_pct'] - rebalanced_results['stockout_rate_pct']
    revenue_increase = rebalanced_results['total_revenue_pkr'] - baseline_results['total_revenue_pkr']

    print("📈 [EVALUATION CONCLUSION FOR CEP DEFENSE]")
    print(f"   ✅ Stock-Out Rate Reduced by: {stockout_reduction:.2f}%")
    print(f"   ✅ Revenue Yield Increased by:  PKR {revenue_increase:,.2f}")
    print("=" * 65)

if __name__ == "__main__":
    execute_pilot_audit()