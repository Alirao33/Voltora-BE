"""Constants: seed."""

from __future__ import annotations

from constants.enums import PlanKind

DEFAULT_PLANS = [
    ("PER_SWAP", "Pay per swap", PlanKind.PER_SWAP, 0, 0.0),
]

# Swap-count plans are retired: swaps are billed by SoH/SoC, subscriptions are locker-only.
RETIRED_PLAN_CODES = ("MONTHLY_10", "MONTHLY_30", "MONTHLY_60", "LOCKER_30")


DEMO_STATIONS = [
    ("STATION-01", "Saddar Hub", 24.8607, 67.0099, "Saddar, Karachi"),
    ("STATION-02", "Clifton Block 5", 24.8138, 67.0300, "Clifton, Karachi"),
    ("STATION-03", "Gulshan-e-Iqbal", 24.9215, 67.0929, "Gulshan-e-Iqbal, Karachi"),
    ("STATION-04", "North Nazimabad", 24.9400, 67.0400, "North Nazimabad, Karachi"),
    ("STATION-05", "Korangi Crossing", 24.8300, 67.1300, "Korangi, Karachi"),
]
