"""Business logic: reports."""

from __future__ import annotations

import csv
import io
from datetime import timedelta
from fastapi import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from constants.enums import OrderMode, OrderStatus, PaymentStatus, Role, SubscriptionStatus
from constants.orders import OPEN_ORDER
from models import Alert, Battery, Order, Payment, Station, Subscription, User
from modules.stations import station_stock
from utils.access import scoped_station_id
from utils.dates import now_utc
from utils.numbers import money

def report_summary(actor: User, db: Session):
    since = now_utc() - timedelta(hours=24)
    target_station = scoped_station_id(actor)

    swaps_q = (
        select(func.count())
        .select_from(Order)
        .where(Order.completed_at >= since, Order.status == OrderStatus.COMPLETED)
    )
    if target_station:
        swaps_q = swaps_q.where(Order.station_id == target_station)
    swaps_24h = db.scalar(swaps_q) or 0

    if target_station:
        revenue_24h = (
            db.scalar(
                select(func.coalesce(func.sum(Order.charged_pkr), 0.0)).where(
                    Order.station_id == target_station,
                    Order.completed_at >= since,
                    Order.status == OrderStatus.COMPLETED,
                )
            )
            or 0.0
        )
        packs_q = (
            select(Battery.state, func.count())
            .where(Battery.station_id == target_station)
            .group_by(Battery.state)
        )
        alerts_q = (
            select(func.count())
            .select_from(Alert)
            .where(Alert.resolved.is_(False), Alert.station_id == target_station)
        )
    else:
        revenue_24h = (
            db.scalar(
                select(func.coalesce(func.sum(Payment.amount_pkr - Payment.refunded_pkr), 0.0)).where(
                    Payment.created_at >= since,
                    Payment.status.in_(
                        (PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED, PaymentStatus.REFUNDED)
                    ),
                )
            )
            or 0.0
        )
        packs_q = select(Battery.state, func.count()).group_by(Battery.state)
        alerts_q = select(func.count()).select_from(Alert).where(Alert.resolved.is_(False))

    packs = dict(db.execute(packs_q).all())
    return {
        "swaps_24h": int(swaps_24h),
        "revenue_24h_pkr": money(float(revenue_24h)),
        "open_alerts": int(db.scalar(alerts_q) or 0),
        "active_subscriptions": int(
            db.scalar(
                select(func.count())
                .select_from(Subscription)
                .where(Subscription.status == SubscriptionStatus.ACTIVE, Subscription.ends_at > now_utc())
            )
            or 0
        ),
        "fleet": {k.value.lower(): int(v) for k, v in packs.items()},
    }


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
    return {
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
