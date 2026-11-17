"""Business logic: pricing."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional

from constants.enums import Chemistry
from models import AppSetting, User
from modules.battery_health import health_score
from modules.audit import audit
from utils.dates import PKT
from utils.numbers import money

class PricingConfig(BaseModel):
    """Admin-configurable pricing parameters (persisted in app_settings under key 'pricing')."""
    tariff_grid_pkr_per_kwh: float = Field(65.0, ge=0)
    wear_pkr_per_kwh_lfp: float = Field(6.0, ge=0)
    wear_pkr_per_kwh_nmc: float = Field(12.0, ge=0)
    operating_cost_pkr: float = Field(30.0, ge=0)
    delivery_cost_pkr: float = Field(150.0, ge=0)
    delivery_rate_pkr_per_km: float = Field(50.0, ge=0)       # extra charge per km from the station to the customer
    margin: float = Field(0.20, ge=0, le=2)
    peak_demand_factor: float = Field(1.2, ge=1.0, le=3.0)
    peak_hours_pkt: list[tuple[int, int]] = Field(default_factory=lambda: [(8, 10), (17, 20)])
    scarcity_threshold: float = Field(0.25, ge=0, le=1)      # stock/target below this adds scarcity uplift
    scarcity_uplift: float = Field(0.10, ge=0, le=1)
    max_demand_factor: float = Field(1.5, ge=1.0, le=3.0)
    thermal_adjustment_enabled: bool = False                 # rebate/penalty based on returned pack temp
    green_rebate_max_c: float = 35.0
    green_rebate_pct: float = 0.15
    standard_max_c: float = 42.0
    thermal_penalty_pct: float = 0.10
    soc_rate_pkr_per_unit: float = Field(10.0, ge=0)          # fixed PKR rate per unit (kWh) of energy used in a swap
    # SoH penalties: admin-set PKR per penalty point of each health formula (temperature, cycle, fault)
    same_soh_discount_pct: float = Field(0.10, ge=0, le=0.5)  # off the whole bill when the pack comes back in the same SoH
    penalty_temp_pkr_per_pt: float = Field(2.0, ge=0)
    penalty_cycle_pkr_per_pt: float = Field(1.0, ge=0)
    penalty_fault_pkr_per_pt: float = Field(1.0, ge=0)


class PriceBreakdown(BaseModel):
    energy_kwh: float
    grid_cost_pkr: float
    wear_cost_pkr: float
    operating_cost_pkr: float
    delivery_cost_pkr: float
    delivery_km: Optional[float] = None
    delivery_distance_charge_pkr: float = 0.0
    soc_used_pct: Optional[float] = None
    soh_pct: Optional[float] = None
    health_score: Optional[float] = None
    penalty_temp: float = 0.0
    penalty_cycle: float = 0.0
    penalty_fault: float = 0.0
    soc_charge_pkr: float = 0.0
    soh_charge_pkr: float = 0.0
    cost_base_pkr: float
    margin: float
    margin_amount_pkr: float
    demand_factor: float
    thermal_tier: str = "NONE"
    thermal_adjustment_pkr: float = 0.0                      # negative = rebate, positive = penalty
    delivery_component_pkr: float = 0.0                      # portion of total attributable to delivery
    total_pkr: float
    currency: str = "PKR"


def is_peak(now: datetime, cfg: PricingConfig) -> bool:
    hour = (now.astimezone(PKT) if now.tzinfo else now).hour
    return any(a <= hour < b for a, b in cfg.peak_hours_pkt)


def demand_factor(now: datetime, stock_ratio: Optional[float], cfg: PricingConfig) -> float:
    """>= 1.0. Peak hours scale it up; low station stock adds a scarcity uplift."""
    f = cfg.peak_demand_factor if is_peak(now, cfg) else 1.0
    if stock_ratio is not None and stock_ratio < cfg.scarcity_threshold:
        f += cfg.scarcity_uplift
    return round(min(f, cfg.max_demand_factor), 3)


def calculate_locker_plan_price(
    duration_type: str,
    base_daily_rate: float = 50.0,
    discount_factor: float = 1.0,
) -> float:
    """Locker pass price: whole months (1, 6 or 12) x 30 days x the daily rate, so 6 and 12 months cost 6x and 12x one month."""
    months = {"monthly": 1, "half_yearly": 6, "yearly": 12}
    return round(months.get(duration_type.lower(), 1) * 30 * base_daily_rate * discount_factor, 2)


def calculate_price(
    *,
    voltage: float,
    capacity_used_ah: float,
    chemistry: Chemistry,
    delivery: bool = False,
    demand: float = 1.0,
    cfg: Optional[PricingConfig] = None,
    returned_temp_c: Optional[float] = None,
    is_subscribed: bool = False,
    plan_code: Optional[str] = None,
    soc_used_pct: Optional[float] = None,
    soh_pct: Optional[float] = None,
    pack=None,
    delivery_km: Optional[float] = None,
) -> PriceBreakdown:
    cfg = cfg or PricingConfig()
    kwh = voltage * capacity_used_ah / 1000.0

    # 1. Energy Consumed / SoC Drop
    grid = 0.0 if is_subscribed else kwh * cfg.tariff_grid_pkr_per_kwh

    # 2. Battery Health Wear & Thermal Abuse Surcharge
    chem_enum = Chemistry(chemistry) if not isinstance(chemistry, Chemistry) else chemistry
    wear_rate = cfg.wear_pkr_per_kwh_lfp if chem_enum == Chemistry.LFP else cfg.wear_pkr_per_kwh_nmc
    is_overheated = returned_temp_c is not None and returned_temp_c > cfg.standard_max_c

    if is_subscribed and not is_overheated:
        wear = 0.0
    else:
        wear = kwh * wear_rate
        if is_overheated:
            wear += 100.0

    # 3. Delivery Fee
    if delivery:
        if is_subscribed and plan_code == "PRO_60":
            deliv = 0.0
        elif is_subscribed:
            deliv = cfg.delivery_cost_pkr * 0.5
        else:
            deliv = cfg.delivery_cost_pkr
    else:
        deliv = 0.0

    # 4. Operating Fee & Cost Base
    operating_cost = 0.0 if is_subscribed else cfg.operating_cost_pkr
    soc_charge = kwh * cfg.soc_rate_pkr_per_unit  # SoC pricing is per unit of energy, not per percent
    # SoH: the pack's composite health formulas give three penalty points; the admin sets what each point costs.
    hr = None
    if pack is not None:
        hr = health_score(
            soh=pack.soh, temperature_c=pack.temperature_c, cycles=pack.cycle_count, faults=pack.fault_count,
            operating_hours=pack.operating_hours, chemistry=pack.chemistry,
        )
    soh_charge = (
        hr.penalty_temp * cfg.penalty_temp_pkr_per_pt
        + hr.penalty_cycle * cfg.penalty_cycle_pkr_per_pt
        + hr.penalty_fault * cfg.penalty_fault_pkr_per_pt
    ) if hr else 0.0
    base = grid + wear + operating_cost + deliv
    # Distance, SoC and SoH are billed at exactly the rates set, outside the margin and demand multipliers.
    km_charge = round((delivery_km or 0.0) * cfg.delivery_rate_pkr_per_km, 2) if delivery else 0.0
    total = base * (1 + cfg.margin) * demand + km_charge + soc_charge + soh_charge

    # 5. Optional Thermal Adjustment Rebate / Penalty
    tier, adj = "NONE", 0.0
    if cfg.thermal_adjustment_enabled and returned_temp_c is not None:
        if returned_temp_c <= cfg.green_rebate_max_c:
            tier, adj = "GREEN_REBATE", -total * cfg.green_rebate_pct
        elif returned_temp_c <= cfg.standard_max_c:
            tier = "STANDARD"
        else:
            tier, adj = "THERMAL_PENALTY", total * cfg.thermal_penalty_pct

    final = money(total) + money(adj)

    return PriceBreakdown(
        energy_kwh=round(kwh, 4),
        grid_cost_pkr=money(grid),
        wear_cost_pkr=money(wear),
        operating_cost_pkr=money(operating_cost),
        delivery_cost_pkr=money(deliv),
        delivery_km=round(delivery_km, 2) if delivery and delivery_km is not None else None,
        delivery_distance_charge_pkr=money(km_charge),
        soc_used_pct=None if soc_used_pct is None else round(soc_used_pct, 1),
        soh_pct=None if soh_pct is None else round(soh_pct, 1),
        health_score=hr.score if hr else None,
        penalty_temp=round(hr.penalty_temp, 1) if hr else 0.0,
        penalty_cycle=round(hr.penalty_cycle, 1) if hr else 0.0,
        penalty_fault=round(hr.penalty_fault, 1) if hr else 0.0,
        soc_charge_pkr=money(soc_charge),
        soh_charge_pkr=money(soh_charge),
        cost_base_pkr=money(base),
        margin=cfg.margin,
        margin_amount_pkr=money(base * cfg.margin),
        demand_factor=demand,
        thermal_tier=tier,
        thermal_adjustment_pkr=money(adj),
        delivery_component_pkr=money(deliv * (1 + cfg.margin) * demand + km_charge) if delivery else 0.0,
        total_pkr=money(final),
    )


def get_pricing_config(db: Session) -> PricingConfig:
    row = db.get(AppSetting, "pricing")
    return PricingConfig(**row.value) if row else PricingConfig()


def get_pricing(db: Session):
    return get_pricing_config(db)


def set_pricing(body: PricingConfig, actor: User, db: Session):
    row = db.get(AppSetting, "pricing")
    data = body.model_dump(mode="json")
    if row:
        row.value = data
    else:
        db.add(AppSetting(key="pricing", value=data))
    audit(db, actor, "UPDATE_PRICING", "app_setting", "pricing", data)
    db.commit()
    return body
