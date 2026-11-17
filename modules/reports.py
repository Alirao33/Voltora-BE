"""Business logic: reports."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from fastapi import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from constants.enums import OrderMode, OrderStatus, PaymentStatus, Role, SubscriptionStatus
from constants.orders import OPEN_ORDER
from models import Alert, Battery, Order, Payment, Station, Subscription, User
from modules.stations import station_stock
from utils.access import scoped_station_id
from utils.dates import now_pkt, now_utc
from utils.numbers import money

PAID_STATES = (PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED, PaymentStatus.REFUNDED)


def _naive_utc(d):
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d and d.tzinfo else d


def _period(start, end):
    end = _naive_utc(end) or now_utc()
    return (_naive_utc(start) or end - timedelta(hours=24)), end


def report_log(actor: User, db: Session, start=None, end=None, limit: int = 500):
    """Money ledger: one row per payment, read live from payments + orders so every swap/plan/bill shows up at once."""
    start, end = _period(start, end)
    station = scoped_station_id(actor)
    q = (
        select(Payment, Order, User)
        .outerjoin(Order, Payment.order_id == Order.id)
        .outerjoin(User, Payment.user_id == User.id)
        .where(Payment.created_at >= start, Payment.created_at <= end, Payment.status.in_(PAID_STATES))
        .order_by(Payment.id.desc())
        .limit(limit)
    )
    if station:
        q = q.where(Order.station_id == station)  # station admins see only their station's orders
    rows = []
    for p, o, u in db.execute(q).all():
        pr = (o.price if o else None) or {}
        kind = "LOCKER_PASS" if p.purpose == "PLAN" else ("HOME_DELIVERY" if o and o.mode == OrderMode.HOME else "SWAP")
        rows.append(
            {
                "payment_id": p.id,
                "date": p.created_at,
                "kind": kind,
                "reference": (o.receipt_no or f"ORD-{o.id}") if o else p.reference,
                "order_id": o.id if o else None,
                "station_id": o.station_id if o else None,
                "rider": (u.full_name or u.username) if u else "Guest",
                "method": p.method.value,
                "soc_used_pct": pr.get("soc_used_pct"),
                "soh_pct": pr.get("soh_pct"),
                "health_score": pr.get("health_score"),
                "energy_kwh": pr.get("energy_kwh"),
                "same_soh_discount_pkr": pr.get("same_soh_discount_pkr"),
                "soc_charge_pkr": pr.get("soc_charge_pkr"),
                "soh_charge_pkr": pr.get("soh_charge_pkr"),
                "demand_factor": pr.get("demand_factor"),
                "paid_pkr": money(p.amount_pkr),
                "refunded_pkr": money(p.refunded_pkr),
                "net_pkr": money(p.amount_pkr - p.refunded_pkr),
                "status": p.status.value,
            }
        )
    return {
        "start": start,
        "end": end,
        "rows": rows,
        "totals": {
            "payments": len(rows),
            "paid_pkr": money(sum(x["paid_pkr"] for x in rows)),
            "refunded_pkr": money(sum(x["refunded_pkr"] for x in rows)),
            "net_pkr": money(sum(x["net_pkr"] for x in rows)),
            "discount_pkr": money(sum(x["same_soh_discount_pkr"] or 0 for x in rows)),
            "soc_charge_pkr": money(sum(x["soc_charge_pkr"] or 0 for x in rows)),
            "soh_charge_pkr": money(sum(x["soh_charge_pkr"] or 0 for x in rows)),
        },
    }


def report_summary(actor: User, db: Session, start=None, end=None):
    start, end = _period(start, end)
    station = scoped_station_id(actor)
    swaps_q = select(func.count()).select_from(Order).where(
        Order.completed_at >= start, Order.completed_at <= end, Order.status == OrderStatus.COMPLETED
    )
    open_q = select(Order).where(Order.payment_due_at.is_not(None), Order.bill_paid_at.is_(None), Order.status == OrderStatus.COMPLETED)
    packs_q = select(Battery.state, func.count()).group_by(Battery.state)
    alerts_q = select(func.count()).select_from(Alert).where(Alert.resolved.is_(False))
    if station:
        swaps_q = swaps_q.where(Order.station_id == station)
        open_q = open_q.where(Order.station_id == station)
        packs_q = packs_q.where(Battery.station_id == station)
        alerts_q = alerts_q.where(Alert.station_id == station)
    totals = report_log(actor, db, start, end, limit=100000)["totals"]
    return {
        "swaps": int(db.scalar(swaps_q) or 0),
        "revenue_pkr": totals["net_pkr"],
        "refunded_pkr": totals["refunded_pkr"],
        "soc_charge_pkr": totals["soc_charge_pkr"],
        "soh_charge_pkr": totals["soh_charge_pkr"],
        "outstanding_bills_pkr": money(sum(o.bill_due_pkr or 0 for o in db.scalars(open_q))),
        "open_alerts": int(db.scalar(alerts_q) or 0),
        "active_subscriptions": int(
            db.scalar(
                select(func.count())
                .select_from(Subscription)
                .where(Subscription.status == SubscriptionStatus.ACTIVE, Subscription.ends_at > now_utc())
            )
            or 0
        ),
        "fleet": {k.value.lower(): int(v) for k, v in db.execute(packs_q).all()},
    }


def report_csv(actor: User, db: Session, start=None, end=None):
    rows = report_log(actor, db, start, end, limit=100000)["rows"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]) if rows else ["payment_id"])
    w.writeheader()
    w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv")


def report_revenue(days: int, format: str, actor: User, db: Session):
    since = now_utc() - timedelta(days=days)
    rows = []
    for p in db.scalars(select(Payment).where(Payment.created_at >= since).order_by(Payment.id.desc())):
        rows.append(
            {
                "id": p.id,
                "date": p.created_at.isoformat(),
                "purpose": p.purpose,
                "method": p.method.value,
                "reference": p.reference,
                "amount_pkr": p.amount_pkr,
                "refunded_pkr": p.refunded_pkr,
                "net_pkr": money(p.amount_pkr - p.refunded_pkr),
                "status": p.status.value,
            }
        )
    if format == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(
            buf,
            fieldnames=["id", "date", "purpose", "method", "reference", "amount_pkr", "refunded_pkr", "net_pkr", "status"],
        )
        w.writeheader()
        w.writerows(rows)
        return Response(buf.getvalue(), media_type="text/csv")
    return rows


def report_station_kpi(days: int, actor: User, db: Session):
    since = now_utc() - timedelta(days=days)
    target_station = scoped_station_id(actor)
    st_q = select(Station).where(Station.is_depot.is_(False)).order_by(Station.id)
    if target_station:
        st_q = st_q.where(Station.id == target_station)

    out = []
    for st in db.scalars(st_q):
        completed = (
            db.scalar(
                select(func.count())
                .select_from(Order)
                .where(
                    Order.station_id == st.id,
                    Order.status == OrderStatus.COMPLETED,
                    Order.completed_at >= since,
                )
            )
            or 0
        )
        rev = (
            db.scalar(
                select(func.coalesce(func.sum(Order.charged_pkr), 0.0)).where(
                    Order.station_id == st.id,
                    Order.status == OrderStatus.COMPLETED,
                    Order.completed_at >= since,
                )
            )
            or 0.0
        )
        out.append(
            {
                "station_id": st.id,
                "name": st.name,
                "completed_swaps": int(completed),
                "gross_revenue_pkr": money(float(rev)),
                "host_share_pkr": money(float(rev) * st.host_revenue_share_pct / 100.0),
                "stock": station_stock(db, st.id),
                "target_stock": st.target_stock,
            }
        )
    return out


def get_analytics_overview(actor: User, db: Session):
    target_station = scoped_station_id(actor)
    total_riders = db.scalar(select(func.count()).select_from(User).where(User.role == Role.RIDER)) or 0

    swaps_q = select(func.count()).select_from(Order).where(Order.status == OrderStatus.COMPLETED)
    deliveries_q = (
        select(func.count())
        .select_from(Order)
        .where(Order.mode == OrderMode.HOME, Order.status.in_(OPEN_ORDER))
    )
    if target_station:
        swaps_q = swaps_q.where(Order.station_id == target_station)
        deliveries_q = deliveries_q.where(Order.station_id == target_station)
        net_rev = (
            db.scalar(
                select(func.coalesce(func.sum(Order.charged_pkr), 0.0)).where(
                    Order.station_id == target_station,
                    Order.status == OrderStatus.COMPLETED,
                )
            )
            or 0.0
        )
    else:
        net_rev = (
            db.scalar(
                select(func.coalesce(func.sum(Payment.amount_pkr - Payment.refunded_pkr), 0.0)).where(
                    Payment.status.in_((PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED))
                )
            )
            or 0.0
        )

    total_swaps = db.scalar(swaps_q) or 0
    active_deliveries = db.scalar(deliveries_q) or 0
    midnight = _naive_utc(now_pkt().replace(hour=0, minute=0, second=0, microsecond=0))
    today_q = select(func.count()).select_from(Order).where(Order.created_at >= midnight)
    stations_q = select(func.count()).select_from(Station).where(Station.is_active.is_(True), Station.is_depot.is_(False))
    if target_station:
        today_q = today_q.where(Order.station_id == target_station)
        stations_q = stations_q.where(Station.id == target_station)
    net_rev = report_log(actor, db, start=datetime(2000, 1, 1), limit=100000)["totals"]["net_pkr"]
    return {
        "orders_today": int(db.scalar(today_q) or 0),
        "operational_stations": int(db.scalar(stations_q) or 0),
        "net_revenue_pkr": money(float(net_rev)),
        "total_revenue_pkr": money(float(net_rev)),
        "active_riders": int(total_riders),
        "total_swaps": int(total_swaps),
        "active_deliveries": int(active_deliveries),
        "fleet": report_summary(actor, db)["fleet"],
    }


def get_finance_summary(actor: User, db: Session):
    gross = (
        db.scalar(
            select(func.coalesce(func.sum(Payment.amount_pkr), 0.0)).where(
                Payment.status.in_(
                    (PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED, PaymentStatus.REFUNDED)
                )
            )
        )
        or 0.0
    )
    refunded = db.scalar(select(func.coalesce(func.sum(Payment.refunded_pkr), 0.0))) or 0.0
    pending_cod = (
        db.scalar(
            select(func.coalesce(func.sum(Order.total_pkr), 0.0)).where(
                Order.status == OrderStatus.PENDING_PAYMENT
            )
        )
        or 0.0
    )
    return {
        "gross_revenue_pkr": money(float(gross)),
        "refunded_pkr": money(float(refunded)),
        "net_revenue_pkr": money(float(gross) - float(refunded)),
        "pending_payments_pkr": money(float(pending_cod)),
    }
