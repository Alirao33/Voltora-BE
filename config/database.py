"""Configuration: database."""

from __future__ import annotations

import datetime as dt
import logging
from sqlalchemy import create_engine, Enum as SAEnum, inspect, JSON, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool
from typing import Any

from config.settings import get_settings
from utils.db_types import UTCDateTime

log_database = logging.getLogger("voltora.db")


_db_url = get_settings().database_url


_db_kwargs: dict[str, Any] = {}


if _db_url.startswith("sqlite"):
    _db_kwargs["connect_args"] = {"check_same_thread": False}
    if ":memory:" in _db_url or _db_url in ("sqlite://", "sqlite:///"):
        _db_kwargs["poolclass"] = StaticPool
else:
    _db_kwargs.update(pool_pre_ping=True, pool_size=10, max_overflow=20)


engine = create_engine(_db_url, **_db_kwargs)


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    type_annotation_map = {dt.datetime: UTCDateTime, dict: JSON}


def enum_col(enum_cls):
    return SAEnum(enum_cls, native_enum=False, length=24)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def enable_timescale() -> None:
    """Turn telemetry into a TimescaleDB hypertable where the extension exists; otherwise plain table."""
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
            conn.execute(text("SELECT create_hypertable('telemetry_readings','ts', if_not_exists => TRUE)"))
        log_database.info("TimescaleDB hypertable ready")
    except Exception as exc:
        log_database.warning("TimescaleDB not available (%s); using a plain table", exc.__class__.__name__)


def add_missing_columns() -> None:
    """create_all never alters existing tables, so add model columns an older database lacks.
    shortcut: columns are added nullable (a scalar default is kept); use Alembic for anything fancier."""
    insp = inspect(engine)
    q = engine.dialect.identifier_preparer.quote
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = f"ALTER TABLE {q(table.name)} ADD COLUMN {q(col.name)} {col.type.compile(engine.dialect)}"
                d = col.default.arg if col.default is not None and col.default.is_scalar else None
                if isinstance(d, (int, float)) and not isinstance(d, bool):
                    ddl += f" DEFAULT {d}"
                conn.execute(text(ddl))
                log_database.warning("Added missing column %s.%s", table.name, col.name)


def init_db() -> None:
    """Create all SQLAlchemy tables (dev/test). Production should use Alembic migrations."""
    import models  # noqa: F401  (importing the package registers every table on Base.metadata)
    Base.metadata.create_all(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("users")}
    if "station_id" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN station_id VARCHAR(40)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_station_id ON users (station_id)"))
    add_missing_columns()
    with engine.begin() as conn:  # codes used to be stored as "#1234"; they are plain digits now
        for table, col in (("orders", "pickup_code"), ("orders", "delivery_otp"), ("bookings", "unlock_code")):
            conn.execute(text(f"UPDATE {table} SET {col} = REPLACE({col}, '#', '') WHERE {col} LIKE '#%'"))
    if engine.dialect.name == "postgresql":
        enable_timescale()
