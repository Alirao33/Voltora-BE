"""Business logic: battery health."""

from __future__ import annotations

from dataclasses import dataclass

from constants.battery import (
    RATED_CYCLE_LIFE,
    THERMAL_FREE_BELOW_C,
    THERMAL_LOCKOUT_C,
    THERMAL_SLOPE_PER_C,
)
from constants.enums import Chemistry, HealthStatus
from utils.numbers import clamp

def soc_pct(remaining_ah: float, full_charge_ah: float) -> float:
    return clamp(remaining_ah / full_charge_ah * 100.0) if full_charge_ah > 0 else 0.0


def soh_pct(full_charge_ah: float, rated_ah: float) -> float:
    return clamp(full_charge_ah / rated_ah * 100.0) if rated_ah > 0 else 0.0


def penalty_temp(t_c: float) -> float:
    if t_c <= THERMAL_FREE_BELOW_C:
        return 0.0
    if t_c <= THERMAL_LOCKOUT_C:
        return THERMAL_SLOPE_PER_C * (t_c - THERMAL_FREE_BELOW_C)
    return 100.0


def penalty_cycle(cycles: int, chemistry: Chemistry) -> float:
    return min(100.0, cycles / RATED_CYCLE_LIFE[Chemistry(chemistry)] * 100.0)


def penalty_fault(faults: int, operating_hours: float) -> float:
    if faults <= 0:
        return 0.0
    if operating_hours <= 0:
        return 100.0
    return min(100.0, faults / operating_hours * 100.0)


@dataclass
class HealthResult:
    score: float
    status: HealthStatus
    lockout: bool
    penalty_temp: float
    penalty_cycle: float
    penalty_fault: float


def classify(
    score: float, temperature_c: float, warning: float = 80.0, retire: float = 70.0
) -> tuple[HealthStatus, bool]:
    lockout = temperature_c > THERMAL_LOCKOUT_C
    if score < retire:
        return HealthStatus.RETIRE, lockout
    if score < warning:
        return HealthStatus.WARNING, lockout
    return HealthStatus.NORMAL, lockout


def health_score(
    *,
    soh: float,
    temperature_c: float,
    cycles: int,
    faults: int,
    operating_hours: float,
    chemistry: Chemistry,
    warning: float = 80.0,
    retire: float = 70.0,
) -> HealthResult:
    """Composite Health = 0.5*SoH + 0.2*(100-Ptemp) + 0.2*(100-Pcycle) + 0.1*(100-Pfault)."""
    pt = penalty_temp(temperature_c)
    pc = penalty_cycle(cycles, chemistry)
    pf = penalty_fault(faults, operating_hours)
    score = round(0.5 * soh + 0.2 * (100.0 - pt) + 0.2 * (100.0 - pc) + 0.1 * (100.0 - pf), 2)
    status_val, lockout = classify(score, temperature_c, warning, retire)
    return HealthResult(score, status_val, lockout, pt, pc, pf)
