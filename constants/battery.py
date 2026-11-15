"""Constants: battery."""

from __future__ import annotations

from constants.enums import Chemistry

RATED_CYCLE_LIFE = {Chemistry.LFP: 1500, Chemistry.NMC: 700}


THERMAL_FREE_BELOW_C = 35.0


THERMAL_SLOPE_PER_C = 2.5


THERMAL_LOCKOUT_C = 55.0
