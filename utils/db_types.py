"""Shared helpers: db types."""

from __future__ import annotations

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator

from utils.dates import ensure_aware, UTC

class UTCDateTime(TypeDecorator):
    """Stores naive UTC, returns timezone-aware UTC (works identically on SQLite and PostgreSQL)."""
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else ensure_aware(value).astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=UTC)
