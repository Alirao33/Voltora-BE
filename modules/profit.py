"""Business logic: SoC and SoH profit, the bootstrap admin's share, and station admin registration fees."""

from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import AppSetting, Order, PlatformFee, ProfitEntry, User
from modules.audit import audit
from utils.access import scoped_station_id
from utils.dates import now_utc
from utils.numbers import money


class PlatformSettings(BaseModel):
    """Set by the bootstrap admin only. Not part of the pricing a station admin can ask to change."""

    bootstrap_share_pct: float = Field(10.0, ge=0, le=100)  # share of the profit that goes to the bootstrap admin
    soc_cost_pkr_per_unit: float = Field(7.0, ge=0)  # what one unit (kWh) costs, so Rs 10 price leaves Rs 3 profit
    soh_cost_pct: float = Field(70.0, ge=0, le=100)  # share of an SoH penalty that is cost, the rest is profit
    station_registration_fee_pkr: float = Field(10000.0, ge=0)


def get_platform_settings(db: Session) -> PlatformSettings:
    row = db.get(AppSetting, "platform")
    return PlatformSettings(**row.value) if row else PlatformSettings()


def set_platform_settings(body: PlatformSettings, actor: User, db: Session):
    row = db.get(AppSetting, "platform")
    data = body.model_dump(mode="json")
    if row:
        row.value = data
    else:
        db.add(AppSetting(key="platform", value=data))
    audit(db, actor, "UPDATE_PLATFORM_SETTINGS", "app_setting", "platform", data)
    db.commit()
    return body


def record_profit(db: Session, order: Order, *, units_kwh: float, soc_charge: float, soh_charge: float, before_discount: float, after_discount: float) -> None:
    """Called when a swap completes. A same-SoH discount shrinks what the rider actually paid, so it shrinks revenue too."""
    if order.id is None or db.scalar(select(ProfitEntry.id).where(ProfitEntry.order_id == order.id)):
        return
    if soc_charge <= 0 and soh_charge <= 0:
        return
    s = get_platform_settings(db)
    paid = after_discount / before_discount if before_discount else 1.0
    soc_rev, soh_rev = soc_charge * paid, soh_charge * paid
    soc_cost = units_kwh * s.soc_cost_pkr_per_unit
    soh_cost = soh_charge * s.soh_cost_pct / 100.0
    profit = soc_rev + soh_rev - soc_cost - soh_cost
    share = max(0.0, profit) * s.bootstrap_share_pct / 100.0
    db.add(
        ProfitEntry(
            order_id=order.id,
            station_id=order.station_id,
            units_kwh=round(units_kwh, 4),
            soc_revenue_pkr=money(soc_rev),
            soc_cost_pkr=money(soc_cost),
            soh_revenue_pkr=money(soh_rev),
            soh_cost_pkr=money(soh_cost),
            profit_pkr=money(profit),
            bootstrap_pct=s.bootstrap_share_pct,
            bootstrap_share_pkr=money(share),
            station_share_pkr=money(profit - share),
        )
    )


def profit_log(actor: User, db: Session, start=None, end=None, limit: int = 500):
    station = scoped_station_id(actor)  # station admins only see their own station
    q = select(ProfitEntry, Order).join(Order, Order.id == ProfitEntry.order_id).order_by(ProfitEntry.id.desc()).limit(limit)
    if station:
        q = q.where(ProfitEntry.station_id == station)
    if start:
        q = q.where(ProfitEntry.created_at >= start)
    if end:
        q = q.where(ProfitEntry.created_at <= end)
    rows = []
    for e, o in db.execute(q).all():
        rows.append(
            {
                "id": e.id,
                "date": e.created_at,
                "order_id": o.id,
                "reference": o.receipt_no or f"ORD-{o.id}",
                "station_id": e.station_id,
                "units_kwh": e.units_kwh,
                "soc_revenue_pkr": e.soc_revenue_pkr,
                "soc_cost_pkr": e.soc_cost_pkr,
                "soc_profit_pkr": money(e.soc_revenue_pkr - e.soc_cost_pkr),
                "soh_revenue_pkr": e.soh_revenue_pkr,
                "soh_cost_pkr": e.soh_cost_pkr,
                "soh_profit_pkr": money(e.soh_revenue_pkr - e.soh_cost_pkr),
                "profit_pkr": e.profit_pkr,
                "bootstrap_pct": e.bootstrap_pct,
                "bootstrap_share_pkr": e.bootstrap_share_pkr,
                "station_share_pkr": e.station_share_pkr,
            }
        )
    tot = lambda k: money(sum(r[k] for r in rows))
    return {
        "rows": rows,
        "totals": {
            "swaps": len(rows),
            "units_kwh": round(sum(r["units_kwh"] for r in rows), 3),
            "revenue_pkr": money(sum(r["soc_revenue_pkr"] + r["soh_revenue_pkr"] for r in rows)),
            "cost_pkr": money(sum(r["soc_cost_pkr"] + r["soh_cost_pkr"] for r in rows)),
            "profit_pkr": tot("profit_pkr"),
            "soc_profit_pkr": tot("soc_profit_pkr"),
            "soh_profit_pkr": tot("soh_profit_pkr"),
            "bootstrap_share_pkr": tot("bootstrap_share_pkr"),
            "station_share_pkr": tot("station_share_pkr"),
        },
    }


# ---- registration fee of a station admin ----

def charge_registration_fee(db: Session, user: User, actor: User) -> None:
    """Called when someone becomes a station admin. Logged as DUE until the bootstrap admin marks it received."""
    if db.scalar(select(PlatformFee.id).where(PlatformFee.user_id == user.id, PlatformFee.kind == "REGISTRATION")):
        return
    fee = get_platform_settings(db).station_registration_fee_pkr
    if fee <= 0:
        return
    db.add(PlatformFee(kind="REGISTRATION", user_id=user.id, user_name=user.full_name or user.username or f"user {user.id}", station_id=user.station_id, amount_pkr=fee))
    audit(db, actor, "STATION_ADMIN_FEE_DUE", "user", user.id, {"amount_pkr": fee, "station_id": user.station_id})


def _fee_out(f: PlatformFee) -> dict:
    return {
        "id": f.id, "kind": f.kind, "user_id": f.user_id, "user_name": f.user_name, "station_id": f.station_id,
        "amount_pkr": f.amount_pkr, "status": f.status, "created_at": f.created_at,
        "received_at": f.received_at, "received_by_name": f.received_by_name,
    }


def list_fees(actor: User, db: Session):
    q = select(PlatformFee).order_by(PlatformFee.id.desc()).limit(200)
    if not actor.is_super_admin:
        q = q.where(PlatformFee.user_id == actor.id)
    fees = [_fee_out(f) for f in db.scalars(q)]
    return {
        "fees": fees,
        "totals": {
            "due_pkr": money(sum(f["amount_pkr"] for f in fees if f["status"] == "DUE")),
            "received_pkr": money(sum(f["amount_pkr"] for f in fees if f["status"] == "RECEIVED")),
        },
    }


def mark_fee_received(fee_id: int, actor: User, db: Session):
    f = db.get(PlatformFee, fee_id)
    if not f:
        raise HTTPException(404, "Fee not found")
    if f.status == "RECEIVED":
        raise HTTPException(409, "This fee is already marked received")
    f.status, f.received_at, f.received_by_name = "RECEIVED", now_utc(), actor.full_name or actor.username
    audit(db, actor, "STATION_ADMIN_FEE_RECEIVED", "platform_fee", f.id, {"amount_pkr": f.amount_pkr})
    db.commit()
    return _fee_out(f)
