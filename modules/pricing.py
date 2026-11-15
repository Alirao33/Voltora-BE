"""Business logic: pricing."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional

from constants.enums import Chemistry
from models import AppSetting, User
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


class PriceBreakdown(BaseModel):
    energy_kwh: float
    grid_cost_pkr: float
    wear_cost_pkr: float
    operating_cost_pkr: float
    delivery_cost_pkr: float
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
    """Calculate Weekly, Monthly, or Yearly locker pass price from a base daily rate."""
    duration_days = {"weekly": 7, "monthly": 30, "yearly": 365}
    tier_discounts = {"weekly": 1.0, "monthly": 0.85, "yearly": 0.70}

    key = duration_type.lower()
    days = duration_days.get(key, 30)
    discount = tier_discounts.get(key, 1.0)
    return round(days * base_daily_rate * discount * discount_factor, 2)


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
    base = grid + wear + operating_cost + deliv
    total = base * (1 + cfg.margin) * demand

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
        cost_base_pkr=money(base),
        margin=cfg.margin,
        margin_amount_pkr=money(base * cfg.margin),
        demand_factor=demand,
        thermal_tier=tier,
        thermal_adjustment_pkr=money(adj),
        delivery_component_pkr=money(deliv * (1 + cfg.margin) * demand) if delivery else 0.0,
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
