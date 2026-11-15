"""Shared helpers: dates."""

from __future__ import annotations

import datetime as dt

PKT = dt.timezone(dt.timedelta(hours=5))  # Karachi, no DST


UTC = dt.timezone.utc


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC)


def now_pkt() -> dt.datetime:
    return dt.datetime.now(PKT)


def ensure_aware(d: dt.datetime) -> dt.datetime:
    """Naive datetimes coming from clients are interpreted as Karachi local time (PKT)."""
    return d.replace(tzinfo=PKT) if d.tzinfo is None else d
