"""
Voltora Backend - Complete Modularized Single-File FastAPI Application.

Install : pip install fastapi "uvicorn[standard]" sqlalchemy pydantic pydantic-settings bcrypt pyjwt \
                      python-multipart prometheus-client paho-mqtt psycopg2-binary xgboost numpy
Run     : python main.py   (http://127.0.0.1:8000, Swagger UI at /docs)
CLI Jobs: python main.py job daily_targets_and_routes | retrain_forecast | sweep
MQTT    : python main.py mqtt
"""
from __future__ import annotations

import asyncio
import contextlib
import csv
import datetime as dt
import hashlib
import hmac
import io
import json
import logging
import math
import os
import pickle
import random
import re
import secrets
import sys
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

import bcrypt
import jwt
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
    OAuth2PasswordBearer,
    OAuth2PasswordRequestForm,
)
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator
from starlette.websockets import WebSocketState

try:
    from routes.subscription import router as subscription_router
except ImportError:
    subscription_router = None


# ####################################################################################################
# SECTION 01: CONFIGURATION & SETTINGS (.env LOADER)
# ####################################################################################################
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"                                   # dev | test | prod
    database_url: str = "sqlite:///./voltora.db"       # prod: postgresql+psycopg2://user:pass@host/db
    secret_key: str = "change-me-in-production"
    device_api_key: str = "change-me-device-key"       # ESP32 / simulator / ingestion service
    cors_origins: str = "*"

    access_token_minutes: int = 30
    refresh_token_days: int = 7
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    otp_max_requests_per_10min: int = 5
    otp_dev_echo: bool = False                         # return OTP in API response (dev only)

    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "Admin@2026!"
    bootstrap_admin_phone: str = "+923001234567"
    seed_demo_data: bool = True

    auto_register_packs: bool = True                   # unknown pack_id in telemetry -> create it (dev/simulator)
    mqtt_enabled: bool = False
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_telemetry_topic: str = "voltora/+/+/telemetry"
    mqtt_command_topic: str = "voltora/{station_id}/{slot_id}/cmd"

    payment_gateway: str = "sandbox"
    payment_webhook_secret: str = "change-me-webhook"

    booking_grace_minutes: int = 15
    max_booking_advance_hours: int = 24
    sweeper_interval_s: int = 60

    overheat_trip_c: float = 45.0                      # WebSocket TRIP_RELAY command threshold
    lockout_c: float = 55.0                            # slot locked, pack cannot be issued
    low_supply_v: float = 180.0                        # mains voltage alert threshold
    min_issue_soc: float = 20.0                        # swaps allowed in 20-100 % window
    warning_health: float = 80.0
    retire_health: float = 70.0
    rider_points_per_swap: int = 10

    ws_heartbeat_s: float = 15.0
    ws_queue_size: int = 100
    model_dir: str = "models_store"

    @property
    def is_prod(self) -> bool:
        return self.env.lower() == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ####################################################################################################
# SECTION 02: DATE & TIME UTILITIES (PKT / UTC)
# ####################################################################################################
PKT = dt.timezone(dt.timedelta(hours=5))  # Karachi, no DST
UTC = dt.timezone.utc


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC)


def now_pkt() -> dt.datetime:
    return dt.datetime.now(PKT)


def ensure_aware(d: dt.datetime) -> dt.datetime:
    """Naive datetimes coming from clients are interpreted as Karachi local time (PKT)."""
    return d.replace(tzinfo=PKT) if d.tzinfo is None else d


class UTCDateTime(TypeDecorator):
    """Stores naive UTC, returns timezone-aware UTC (works identically on SQLite and PostgreSQL)."""
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else ensure_aware(value).astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=UTC)


# ####################################################################################################
# SECTION 03: DATABASE ENGINE, SESSION & TIMESCALEDB
# ####################################################################################################
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

from sqlalchemy import inspect

def init_db() -> None:
    """Create all SQLAlchemy tables (dev/test). Production should use Alembic migrations."""
    Base.metadata.create_all(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("users")}
    if "station_id" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN station_id VARCHAR(40)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_station_id ON users (station_id)"))
    if engine.dialect.name == "postgresql":
        enable_timescale()


# ####################################################################################################
# SECTION 04: DOMAIN ENUMS & CONSTANTS
# ####################################################################################################
class Role(str, Enum):
    ADMIN = "ADMIN"
    STATION_ADMIN = "STATION_ADMIN"
    DRIVER = "DRIVER"
    RIDER = "RIDER"


class LockerStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    ONE_LEFT = "ONE_LEFT"
    FULL = "FULL"
    RESERVED_VIP = "RESERVED_VIP"
    MAINTENANCE = "MAINTENANCE"


class DoorLockState(str, Enum):
    LOCKED = "LOCKED"
    UNLOCKED = "UNLOCKED"


class ActionType(str, Enum):
    SWAP_DROP_AND_PICK = "SWAP_DROP_AND_PICK"
    RESERVE_VIP_SLOT = "RESERVE_VIP_SLOT"
    GUEST_PAY_SWAP = "GUEST_PAY_SWAP"


class BookingStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class RiderTier(str, Enum):
    VIP = "VIP"
    SUBSCRIBED = "SUBSCRIBED"
    GUEST = "GUEST"


class SiteAvailability(str, Enum):
    AVAILABLE = "AVAILABLE"
    ONE_LEFT = "ONE_LEFT"
    FULL = "FULL"


class Chemistry(str, Enum):
    LFP = "LFP"
    NMC = "NMC"


class PaymentMethod(str, Enum):
    RAAST = "RAAST"
    WALLET = "WALLET"
    CARD = "CARD"


class PaymentStatus(str, Enum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    REFUNDED = "REFUNDED"


class BatteryState(str, Enum):
    CHARGED = "CHARGED"        # in a station slot, ready to issue
    CHARGING = "CHARGING"
    DEPLETED = "DEPLETED"      # returned, waiting for a charger
    RESERVED = "RESERVED"      # held for a paid order
    IN_USE = "IN_USE"          # with a rider
    ON_VAN = "ON_VAN"
    LOCKED = "LOCKED"          # over-temperature lockout (> 55 C)
    RETIRED = "RETIRED"        # health < 70 -> second-life stationary storage


class HealthStatus(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    RETIRE = "RETIRE"


class OrderMode(str, Enum):
    STATION = "STATION"
    LOCKER = "LOCKER"
    HOME = "HOME"


class OrderStatus(str, Enum):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    PAID = "PAID"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class PlanKind(str, Enum):
    PER_SWAP = "PER_SWAP"
    MONTHLY = "MONTHLY"
    LOCKER = "LOCKER"


class SubscriptionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class AlertType(str, Enum):
    WEAK_PACK = "WEAK_PACK"
    PACK_RETIRED = "PACK_RETIRED"
    STOCK_OUT = "STOCK_OUT"
    OVER_TEMP = "OVER_TEMP"
    LOCKOUT = "LOCKOUT"
    LOW_SUPPLY = "LOW_SUPPLY"


class AlertSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class TaskKind(str, Enum):
    REBALANCE = "REBALANCE"
    DELIVERY = "DELIVERY"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
# ####################################################################################################
# SECTION 05: SQLALCHEMY DATABASE MODELS (19 TABLES)
# ####################################################################################################
# ====================================================================================================
# MODEL 01: USERS & ROLES
# ====================================================================================================
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20), unique=True, index=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(120))
    hashed_password: Mapped[Optional[str]] = mapped_column(String(100))
    role: Mapped[Role] = mapped_column(enum_col(Role), default=Role.RIDER)
    station_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("stations.id"), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_vip: Mapped[bool] = mapped_column(Boolean, default=False)
    points: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc)
    last_login_at: Mapped[Optional[dt.datetime]]

    @property
    def is_admin(self) -> bool:
        return self.role in {Role.ADMIN, Role.STATION_ADMIN}

    @property
    def is_super_admin(self) -> bool:
        return self.role == Role.ADMIN


# ====================================================================================================
# MODEL 02: OTP CHALLENGES (RIDER LOGIN & ADMIN 2FA)
# ====================================================================================================
class OtpChallenge(Base):
    __tablename__ = "otp_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    purpose: Mapped[str] = mapped_column(String(20))           # LOGIN | ADMIN_2FA
    phone: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    code_hash: Mapped[str] = mapped_column(String(64), default="")
    expires_at: Mapped[dt.datetime]
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc)


# ====================================================================================================
# MODEL 03: SWAP STATIONS & DEPOTS
# ====================================================================================================
class Station(Base):
    __tablename__ = "stations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    address: Mapped[str] = mapped_column(String(300))
    total_lockers: Mapped[int] = mapped_column(Integer, default=0)
    host_name: Mapped[Optional[str]] = mapped_column(String(120))          # shop / fuel station / mall
    host_revenue_share_pct: Mapped[float] = mapped_column(Float, default=0.0)
    is_depot: Mapped[bool] = mapped_column(Boolean, default=False)         # van loading point, not public
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    target_stock: Mapped[int] = mapped_column(Integer, default=0)          # S_target (set by forecast/admin)
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc)


# ====================================================================================================
# MODEL 04: STATION LOCKERS & DOOR STATES
# ====================================================================================================
class Locker(Base):
    __tablename__ = "lockers"
    __table_args__ = (UniqueConstraint("station_id", "door_number"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    station_id: Mapped[str] = mapped_column(ForeignKey("stations.id"), index=True)
    door_number: Mapped[str] = mapped_column(String(40))
    max_battery_capacity: Mapped[int] = mapped_column(Integer, default=2)
    current_occupied_batteries: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[LockerStatus] = mapped_column(enum_col(LockerStatus), default=LockerStatus.AVAILABLE)
    door_lock_state: Mapped[DoorLockState] = mapped_column(enum_col(DoorLockState), default=DoorLockState.LOCKED)
    reserved_for_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    reserved_booking_id: Mapped[Optional[int]] = mapped_column(Integer)

    @property
    def has_vacancy(self) -> bool:
        return self.current_occupied_batteries < self.max_battery_capacity


# ====================================================================================================
# MODEL 05: REBALANCING & DELIVERY VANS
# ====================================================================================================
class Van(Base):
    __tablename__ = "vans"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    driver_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    capacity: Mapped[int] = mapped_column(Integer, default=20)
    depot_station_id: Mapped[Optional[str]] = mapped_column(ForeignKey("stations.id"))
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ====================================================================================================
# MODEL 06: BATTERY PACKS
# ====================================================================================================
class Battery(Base):
    __tablename__ = "batteries"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)          # pack_id
    chemistry: Mapped[Chemistry] = mapped_column(enum_col(Chemistry), default=Chemistry.LFP)
    rated_capacity_ah: Mapped[float] = mapped_column(Float, default=20.0)
    nominal_voltage: Mapped[float] = mapped_column(Float, default=48.0)
    full_charge_capacity_ah: Mapped[float] = mapped_column(Float, default=20.0)
    remaining_ah: Mapped[float] = mapped_column(Float, default=20.0)
    voltage: Mapped[float] = mapped_column(Float, default=48.0)
    current_a: Mapped[float] = mapped_column(Float, default=0.0)
    temperature_c: Mapped[float] = mapped_column(Float, default=25.0)
    soc: Mapped[float] = mapped_column(Float, default=100.0)
    soh: Mapped[float] = mapped_column(Float, default=100.0)
    cycle_count: Mapped[int] = mapped_column(Integer, default=0)
    fault_count: Mapped[int] = mapped_column(Integer, default=0)
    operating_hours: Mapped[float] = mapped_column(Float, default=0.0)
    supply_voltage_v: Mapped[Optional[float]] = mapped_column(Float)
    health_score: Mapped[float] = mapped_column(Float, default=100.0)
    health_status: Mapped[HealthStatus] = mapped_column(enum_col(HealthStatus), default=HealthStatus.NORMAL)
    state: Mapped[BatteryState] = mapped_column(enum_col(BatteryState), default=BatteryState.CHARGED, index=True)
    station_id: Mapped[Optional[str]] = mapped_column(ForeignKey("stations.id"), index=True)
    locker_id: Mapped[Optional[str]] = mapped_column(ForeignKey("lockers.id"))     # slot housing the pack
    van_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vans.id"))
    reserved_order_id: Mapped[Optional[int]] = mapped_column(Integer)
    last_seen_at: Mapped[Optional[dt.datetime]]
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc)


# ====================================================================================================
# MODEL 07: TELEMETRY READINGS (TIME-SERIES / HYPERTABLE)
# ====================================================================================================
class TelemetryReading(Base):
    """Time-series table (hypertable on PostgreSQL+TimescaleDB). Composite PK deduplicates readings."""
    __tablename__ = "telemetry_readings"
    __table_args__ = (Index("ix_telemetry_ts", "ts"),)

    pack_id: Mapped[str] = mapped_column(ForeignKey("batteries.id"), primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(primary_key=True)
    received_at: Mapped[dt.datetime] = mapped_column(default=now_utc)
    station_id: Mapped[Optional[str]] = mapped_column(String(40))
    slot_id: Mapped[Optional[str]] = mapped_column(String(40))
    voltage: Mapped[float] = mapped_column(Float)
    current_a: Mapped[float] = mapped_column(Float, default=0.0)
    temperature_c: Mapped[float] = mapped_column(Float)
    soc: Mapped[float] = mapped_column(Float)
    soh: Mapped[float] = mapped_column(Float)
    remaining_ah: Mapped[Optional[float]] = mapped_column(Float)
    cycle_count: Mapped[int] = mapped_column(Integer, default=0)
    supply_voltage_v: Mapped[Optional[float]] = mapped_column(Float)
    health_score: Mapped[float] = mapped_column(Float, default=100.0)


# ====================================================================================================
# MODEL 08: SUBSCRIPTION & LOCKER PLANS
# ====================================================================================================
class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[PlanKind] = mapped_column(enum_col(PlanKind))
    swaps_included: Mapped[int] = mapped_column(Integer, default=0)
    price_pkr: Mapped[float] = mapped_column(Float, default=0.0)
    duration_type: Mapped[str] = mapped_column(String(20), default="monthly")  # 'weekly', 'monthly', 'yearly'
    duration_days: Mapped[int] = mapped_column(Integer, default=30)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ====================================================================================================
# MODEL 09: RIDER SUBSCRIPTIONS
# ====================================================================================================
class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    swaps_remaining: Mapped[int] = mapped_column(Integer, default=0)
    starts_at: Mapped[dt.datetime] = mapped_column(default=now_utc)
    ends_at: Mapped[dt.datetime]
    status: Mapped[SubscriptionStatus] = mapped_column(
        enum_col(SubscriptionStatus), default=SubscriptionStatus.ACTIVE
    )


# ====================================================================================================
# MODEL 10: LOCKER BOOKINGS
# ====================================================================================================
class Booking(Base):
    """A held locker slot with an unlock code."""
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)
    guest_ref: Mapped[Optional[str]] = mapped_column(String(40))
    station_id: Mapped[str] = mapped_column(ForeignKey("stations.id"))
    locker_id: Mapped[str] = mapped_column(ForeignKey("lockers.id"))
    action_type: Mapped[ActionType] = mapped_column(enum_col(ActionType))
    scheduled_time: Mapped[dt.datetime]
    hold_expires_at: Mapped[dt.datetime]
    unlock_code: Mapped[str] = mapped_column(String(8))
    status: Mapped[BookingStatus] = mapped_column(enum_col(BookingStatus), default=BookingStatus.PENDING)
    order_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc)

    @property
    def booking_id(self) -> str:
        return f"BK-{1000 + self.id}"


# ====================================================================================================
# MODEL 11: SWAP & DELIVERY ORDERS
# ====================================================================================================
class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)
    guest_ref: Mapped[Optional[str]] = mapped_column(String(40))
    guest_name: Mapped[Optional[str]] = mapped_column(String(80))
    guest_phone: Mapped[Optional[str]] = mapped_column(String(20))
    station_id: Mapped[str] = mapped_column(ForeignKey("stations.id"), index=True)
    mode: Mapped[OrderMode] = mapped_column(enum_col(OrderMode))
    status: Mapped[OrderStatus] = mapped_column(enum_col(OrderStatus), default=OrderStatus.PENDING_PAYMENT, index=True)
    plan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("plans.id"))
    subscription_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subscriptions.id"))
    pack_out_id: Mapped[Optional[str]] = mapped_column(ForeignKey("batteries.id"))
    pack_in_id: Mapped[Optional[str]] = mapped_column(ForeignKey("batteries.id"))
    booking_id: Mapped[Optional[int]] = mapped_column(Integer)
    slot_id: Mapped[Optional[str]] = mapped_column(String(40))             # slot / locker shown to the rider
    pickup_code: Mapped[Optional[str]] = mapped_column(String(8))          # unlock PIN shown in the app
    price: Mapped[dict] = mapped_column(JSON, default=dict)
    total_pkr: Mapped[float] = mapped_column(Float, default=0.0)
    charged_pkr: Mapped[float] = mapped_column(Float, default=0.0)         # upfront amount paid
    actual_total_pkr: Mapped[Optional[float]] = mapped_column(Float)       # recomputed upon old pack return
    settlement_delta_pkr: Mapped[Optional[float]] = mapped_column(Float)
    delivery_address: Mapped[Optional[str]] = mapped_column(String(300))
    delivery_lat: Mapped[Optional[float]] = mapped_column(Float)
    delivery_lon: Mapped[Optional[float]] = mapped_column(Float)
    delivery_otp: Mapped[Optional[str]] = mapped_column(String(8))
    rating: Mapped[Optional[int]] = mapped_column(Integer)
    review: Mapped[Optional[str]] = mapped_column(String(500))
    receipt_no: Mapped[Optional[str]] = mapped_column(String(20), unique=True)
    hold_expires_at: Mapped[Optional[dt.datetime]]
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc, index=True)
    paid_at: Mapped[Optional[dt.datetime]]
    completed_at: Mapped[Optional[dt.datetime]]

    @property
    def order_id(self) -> str:
        return f"ORD-{self.id}"


# ====================================================================================================
# MODEL 12: PAYMENTS & REFUNDS
# ====================================================================================================
class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("orders.id"), index=True)
    subscription_id: Mapped[Optional[int]] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String(10), default="ORDER")     # ORDER | PLAN
    method: Mapped[PaymentMethod] = mapped_column(enum_col(PaymentMethod))
    reference: Mapped[str] = mapped_column(String(100), unique=True)
    amount_pkr: Mapped[float] = mapped_column(Float)
    refunded_pkr: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[PaymentStatus] = mapped_column(enum_col(PaymentStatus), default=PaymentStatus.PENDING)
    refund_reason: Mapped[Optional[str]] = mapped_column(String(200))
    provider_ref: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc)


# ====================================================================================================
# MODEL 13: SYSTEM & HARDWARE ALERTS
# ====================================================================================================
class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[AlertType] = mapped_column(enum_col(AlertType), index=True)
    severity: Mapped[AlertSeverity] = mapped_column(enum_col(AlertSeverity))
    message: Mapped[str] = mapped_column(String(300))
    station_id: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    pack_id: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc)


# ====================================================================================================
# MODEL 14: ADMIN & SYSTEM AUDIT LOGS
# ====================================================================================================
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    actor_name: Mapped[Optional[str]] = mapped_column(String(80))
    station_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[Optional[str]] = mapped_column(String(60))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(default=now_utc, index=True)


# ====================================================================================================
# MODEL 15: DYNAMIC APPLICATION SETTINGS (KEY-VALUE JSON)
# ====================================================================================================
class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


# ====================================================================================================
# MODEL 16: VAN REBALANCING & HOME DELIVERY TASKS
# ====================================================================================================
class VanTask(Base):
    __tablename__ = "van_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    van_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vans.id"), index=True)
    kind: Mapped[TaskKind] = mapped_column(enum_col(TaskKind))
    status: Mapped[TaskStatus] = mapped_column(enum_col(TaskStatus), default=TaskStatus.PENDING, index=True)
    plan_date: Mapped[dt.datetime] = mapped_column(default=now_utc)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    station_id: Mapped[Optional[str]] = mapped_column(ForeignKey("stations.id"))
    order_id: Mapped[Optional[int]] = mapped_column(Integer)
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)
    address: Mapped[Optional[str]] = mapped_column(String(300))
    drop_count: Mapped[int] = mapped_column(Integer, default=0)
    pickup_count: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[float] = mapped_column(Float, default=0.0)
    distance_km: Mapped[float] = mapped_column(Float, default=0.0)
    confirmed_drop: Mapped[Optional[int]] = mapped_column(Integer)
    confirmed_pickup: Mapped[Optional[int]] = mapped_column(Integer)
    confirmed_at: Mapped[Optional[dt.datetime]]


# ====================================================================================================
# MODEL 17: WEATHER OBSERVATIONS (FORECAST FEATURES)
# ====================================================================================================
class WeatherObservation(Base):
    __tablename__ = "weather_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(index=True)
    temp_c: Mapped[float] = mapped_column(Float)
    rain_mm: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(40), default="manual")


# ====================================================================================================
# MODEL 18 & 19: DEMAND FORECAST MODEL VERSIONS & RECORDS
# ====================================================================================================
class ForecastModelVersion(Base):
    __tablename__ = "forecast_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    algorithm: Mapped[str] = mapped_column(String(40))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    artifact_path: Mapped[str] = mapped_column(String(300))
    n_rows: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    trained_at: Mapped[dt.datetime] = mapped_column(default=now_utc)


class ForecastRecord(Base):
    __tablename__ = "forecast_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[str] = mapped_column(ForeignKey("stations.id"), index=True)
    model_id: Mapped[Optional[int]] = mapped_column(ForeignKey("forecast_models.id"))
    generated_at: Mapped[dt.datetime] = mapped_column(default=now_utc, index=True)
    horizon_hours: Mapped[int] = mapped_column(Integer)
    predicted_demand: Mapped[float] = mapped_column(Float)
    target_stock: Mapped[int] = mapped_column(Integer)
# ####################################################################################################
# SECTION 06: MATH ENGINES & CORE ALGORITHMS
# ####################################################################################################


# ====================================================================================================
# ENGINE 01: BATTERY HEALTH & DEGRADATION SCORING
# ====================================================================================================
RATED_CYCLE_LIFE = {Chemistry.LFP: 1500, Chemistry.NMC: 700}
THERMAL_FREE_BELOW_C = 35.0
THERMAL_SLOPE_PER_C = 2.5
THERMAL_LOCKOUT_C = 55.0


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def soc_pct(remaining_ah: float, full_charge_ah: float) -> float:
    return _clamp(remaining_ah / full_charge_ah * 100.0) if full_charge_ah > 0 else 0.0


def soh_pct(full_charge_ah: float, rated_ah: float) -> float:
    return _clamp(full_charge_ah / rated_ah * 100.0) if rated_ah > 0 else 0.0


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


# ====================================================================================================
# ENGINE 02: DYNAMIC PRICING & LOCKER PLAN RATE CALCULATOR
# ====================================================================================================
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


def money(x: float) -> float:
    return float(Decimal(repr(float(x))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


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


# ====================================================================================================
# ENGINE 03: FLEET REBALANCING & GREEDY ROUTING
# ====================================================================================================
def shortage(target: int, current: int) -> int:
    return max(0, target - current)


def priority(target: int, current: int) -> float:
    return shortage(target, current) / target if target > 0 else 0.0


def grid_distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x1 - x2, y1 - y2)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(max(0.0, min(1.0, a))))


@dataclass
class StationState:
    id: str
    lat: float
    lon: float
    target: int
    current: int          # charged packs in stock
    depleted: int = 0     # depleted packs waiting for pick-up


@dataclass
class Stop:
    station_id: str
    drop: int
    pickup: int
    priority: float
    shortage: int
    distance_km: float


def plan_route(
    van_lat: float,
    van_lon: float,
    van_packs: int,
    stations: list[StationState],
    van_capacity: int = 10**6,
    dist=haversine_km,
) -> list[Stop]:
    """Build shortage queue sorted by priority DESC then distance ASC; allocate drops & pickups along route."""
    queue = []
    for s in stations:
        sh = shortage(s.target, s.current)
        if sh > 0:
            queue.append((priority(s.target, s.current), dist(van_lat, van_lon, s.lat, s.lon), sh, s))
    queue.sort(key=lambda t: (-t[0], t[1], t[3].id))

    route: list[Stop] = []
    pos = (van_lat, van_lon)
    on_board = van_packs
    picked = 0

    for pr, _, sh, s in queue:
        if on_board <= 0:
            break
        drop = min(sh, on_board)
        on_board -= drop
        pickup = min(s.depleted, max(0, van_capacity - on_board - picked))
        picked += pickup
        route.append(
            Stop(
                s.id,
                drop,
                pickup,
                round(pr, 4),
                sh,
                round(dist(pos[0], pos[1], s.lat, s.lon), 3),
            )
        )
        pos = (s.lat, s.lon)
    return route


# ====================================================================================================
# ENGINE 04: DEMAND FORECASTING (SEASONAL BASELINE + XGBOOST)
# ====================================================================================================
from types import SimpleNamespace

try:
    import numpy as np
    from xgboost import XGBRegressor
    HAS_XGB = True
except Exception:
    HAS_XGB = False

DEFAULT_TEMP_C, DEFAULT_RAIN_MM = 32.0, 0.0


@dataclass
class Row:
    station_id: str
    ts: datetime          # hour bucket (PKT)
    demand: float         # swaps in that hour
    temp_c: float = DEFAULT_TEMP_C
    rain_mm: float = DEFAULT_RAIN_MM


class SeasonalBaseline:
    algorithm = "seasonal_mean"

    def fit(self, rows: list[Row]) -> "SeasonalBaseline":
        acc: dict = defaultdict(list)
        by_station: dict = defaultdict(list)
        for r in rows:
            acc[(r.station_id, r.ts.weekday(), r.ts.hour)].append(r.demand)
            by_station[r.station_id].append(r.demand)
        self.cell = {k: sum(v) / len(v) for k, v in acc.items()}
        self.station = {k: sum(v) / len(v) for k, v in by_station.items()}
        allv = [r.demand for r in rows]
        self.global_mean = sum(allv) / len(allv) if allv else 0.0
        return self

    def predict(
        self, station_id: str, ts: datetime, temp_c: float = DEFAULT_TEMP_C, rain_mm: float = 0.0
    ) -> float:
        return self.cell.get(
            (station_id, ts.weekday(), ts.hour),
            self.station.get(station_id, self.global_mean),
        )


class XGBDemand:
    algorithm = "xgboost"

    def fit(self, rows: list[Row]) -> "XGBDemand":
        self.index = {s: i for i, s in enumerate(sorted({r.station_id for r in rows}))}
        X = np.array([self._x(r.station_id, r.ts, r.temp_c, r.rain_mm) for r in rows])
        y = np.array([r.demand for r in rows])
        self.model = XGBRegressor(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.1,
            subsample=0.9,
            objective="reg:squarederror",
        )
        self.model.fit(X, y)
        return self

    def _x(self, station_id: str, ts: datetime, temp_c: float, rain_mm: float) -> list[float]:
        return [self.index.get(station_id, -1), ts.hour, ts.weekday(), temp_c, rain_mm]

    def predict(
        self, station_id: str, ts: datetime, temp_c: float = DEFAULT_TEMP_C, rain_mm: float = 0.0
    ) -> float:
        return max(0.0, float(self.model.predict(np.array([self._x(station_id, ts, temp_c, rain_mm)]))[0]))


def train(rows: list[Row], prefer_xgb: bool = True):
    """Time-ordered 80/20 split for MAE validation; returned model is refit on all rows."""
    rows = sorted(rows, key=lambda r: r.ts)
    cls = XGBDemand if (prefer_xgb and HAS_XGB and len(rows) >= 50) else SeasonalBaseline
    metrics: dict[str, Any] = {"n_rows": len(rows), "algorithm": cls.algorithm}
    if len(rows) >= 20:
        cut = int(len(rows) * 0.8)
        m = cls().fit(rows[:cut])
        test = rows[cut:]
        err = [abs(m.predict(r.station_id, r.ts, r.temp_c, r.rain_mm) - r.demand) for r in test]
        base = SeasonalBaseline().fit(rows[:cut])
        berr = [abs(base.predict(r.station_id, r.ts) - r.demand) for r in test]
        metrics.update(mae=round(sum(err) / len(err), 4), baseline_mae=round(sum(berr) / len(berr), 4))
    return cls().fit(rows), metrics


def save(model, path: str) -> None:
    with open(path, "wb") as f:
        pickle.dump(model, f)


def load(path: str):
    with open(path, "rb") as f:
        return pickle.load(f)


def forecast_demand(
    model, station_id: str, start: datetime, hours: int, weather: Optional[dict] = None
) -> float:
    total = 0.0
    for h in range(hours):
        ts = start + timedelta(hours=h)
        w = (weather or {}).get(ts.replace(minute=0, second=0, microsecond=0), {})
        total += model.predict(
            station_id, ts, w.get("temp_c", DEFAULT_TEMP_C), w.get("rain_mm", DEFAULT_RAIN_MM)
        )
    return total


def target_stock(predicted_demand: float, *, capacity: int, safety: float = 1.2, min_stock: int = 2) -> int:
    """S_target = predicted demand * safety factor, clamped between min_stock and station capacity."""
    return int(max(min(capacity, min_stock), min(capacity, math.ceil(predicted_demand * safety))))


fc = SimpleNamespace(
    DEFAULT_TEMP_C=DEFAULT_TEMP_C,
    DEFAULT_RAIN_MM=DEFAULT_RAIN_MM,
    Row=Row,
    SeasonalBaseline=SeasonalBaseline,
    XGBDemand=XGBDemand,
    train=train,
    save=save,
    load=load,
    forecast_demand=forecast_demand,
    target_stock=target_stock,
)


# ====================================================================================================
# ENGINE 05: PILOT REBALANCING SIMULATOR
# ====================================================================================================
AVG_PRICE_PKR = 118.0


def _poisson(rng: random.Random, lam: float) -> int:
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1


def run_pilot(
    *,
    n_stations: int = 6,
    days: int = 7,
    seed: int = 42,
    van_capacity: int = 20,
    stock_per_station: int = 8,
    charge_per_hour: int = 2,
) -> dict[str, Any]:
    rng = random.Random(seed)
    stations = [
        {
            "id": f"S{i + 1}",
            "lat": 24.80 + rng.random() * 0.15,
            "lon": 67.00 + rng.random() * 0.15,
            "pop": 0.5 + rng.random() * 2.0,
        }
        for i in range(n_stations)
    ]
    profile = [0.2] * 6 + [1.0, 2.5, 3.0, 1.8, 1.2, 1.2, 1.5, 1.2, 1.2, 1.6, 2.4, 3.0, 2.6, 1.6, 0.9, 0.5]
    profile = (profile + [0.3] * 24)[:24]
    trace = [[_poisson(rng, s["pop"] * profile[h % 24]) for s in stations] for h in range(days * 24)]

    def simulate(rebalance: bool) -> dict[str, Any]:
        stock = [stock_per_station] * n_stations
        depleted = [0] * n_stations
        demand_total = served_total = 0
        km = 0.0
        availability = []

        for h, row in enumerate(trace):
            if rebalance and h % 24 == 5:
                states = []
                for i, s in enumerate(stations):
                    exp = sum(s["pop"] * profile[(h + k) % 24] for k in range(1, 13))
                    states.append(
                        StationState(
                            s["id"],
                            s["lat"],
                            s["lon"],
                            min(stock_per_station * 2, math.ceil(exp * 1.2)),
                            stock[i],
                            depleted[i],
                        )
                    )
                route = plan_route(24.85, 67.05, van_capacity, states, van_capacity)
                pos = (24.85, 67.05)
                for st in route:
                    i = int(st.station_id[1:]) - 1
                    stock[i] += st.drop
                    depleted[i] -= min(depleted[i], st.pickup)
                    km += haversine_km(pos[0], pos[1], stations[i]["lat"], stations[i]["lon"])
                    pos = (stations[i]["lat"], stations[i]["lon"])

            for i, d in enumerate(row):
                served = min(d, stock[i])
                stock[i] -= served
                depleted[i] += served
                demand_total += d
                served_total += served
                back = min(depleted[i], charge_per_hour)
                depleted[i] -= back
                stock[i] += back
                availability.append(stock[i] / stock_per_station)

        return {
            "stockout_rate": round(1 - served_total / demand_total, 4) if demand_total else 0.0,
            "avg_availability": round(sum(availability) / len(availability), 4),
            "van_km": round(km, 1),
            "revenue_pkr": round(served_total * AVG_PRICE_PKR, 2),
            "demand": demand_total,
            "served": served_total,
        }

    base, reb = simulate(False), simulate(True)
    return {
        "baseline": base,
        "rebalanced": reb,
        "improvement": {
            "stockout_rate_delta": round(base["stockout_rate"] - reb["stockout_rate"], 4),
            "revenue_delta_pkr": round(reb["revenue_pkr"] - base["revenue_pkr"], 2),
        },
    }
# ####################################################################################################
# SECTION 07: DUAL-KEY JSON SERIALIZATION (SNAKE_CASE + CAMELCASE)
# ####################################################################################################
def _camel(key: str) -> str:
    head, *tail = key.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in tail)


def dual_keys(obj: Any) -> Any:
    """Emit both snake_case and camelCase keys so Python and Next.js/TypeScript clients work seamlessly."""
    if isinstance(obj, dict):
        out = {k: dual_keys(v) for k, v in obj.items()}
        for key in list(out):
            if isinstance(key, str) and "_" in key.strip("_"):
                out.setdefault(_camel(key), out[key])
        return out
    if isinstance(obj, (list, tuple)):
        return [dual_keys(i) for i in obj]
    return obj


class DualKeyJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return super().render(dual_keys(jsonable_encoder(content)))


def to_wire(payload: Any) -> Any:
    return dual_keys(jsonable_encoder(payload))


# ####################################################################################################
# SECTION 08: REAL-TIME WEBSOCKET PUB/SUB HUB
# ####################################################################################################
def offer(q: asyncio.Queue, msg: dict) -> None:
    """Non-blocking enqueue: drop oldest frame if consumer is slow so telemetry producers never stall."""
    if q.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            q.get_nowait()
    q.put_nowait(msg)


class Hub:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, topic: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=get_settings().ws_queue_size)
        self._subs[topic].add(q)
        return q

    def unsubscribe(self, topic: str, q: asyncio.Queue) -> None:
        self._subs[topic].discard(q)
        if not self._subs[topic]:
            self._subs.pop(topic, None)

    def _broadcast(self, topic: str, msg: dict) -> None:
        for q in list(self._subs.get(topic, ())):
            offer(q, msg)

    def publish(self, topic: str, msg: dict) -> None:
        """Thread-safe publish callable from MQTT threads or FastAPI worker threads."""
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._broadcast, topic, msg)


hub = Hub()


# ####################################################################################################
# SECTION 09: PROMETHEUS OBSERVABILITY METRICS
# ####################################################################################################
METRICS_REGISTRY = CollectorRegistry()
HTTP_REQUESTS = Counter(
    "voltora_http_requests_total", "HTTP requests", ["method", "path", "status"], registry=METRICS_REGISTRY
)
HTTP_LATENCY = Histogram(
    "voltora_http_request_seconds", "HTTP latency", ["method", "path"], registry=METRICS_REGISTRY
)
TELEMETRY_MSGS = Counter(
    "voltora_telemetry_messages_total", "Telemetry messages", ["result"], registry=METRICS_REGISTRY
)
ALERTS = Counter(
    "voltora_alerts_total", "Alerts raised", ["type", "severity"], registry=METRICS_REGISTRY
)
ORDERS = Counter(
    "voltora_orders_total", "Orders by final state", ["status"], registry=METRICS_REGISTRY
)


# ####################################################################################################
# SECTION 10: HARDWARE DEVICE COMMAND DISPATCHER (MQTT / RELAY / LOCKER)
# ####################################################################################################
log_commands = logging.getLogger("voltora.commands")
_publisher: Optional[Callable[[str, str, dict], None]] = None


def register_publisher(fn: Optional[Callable[[str, str, dict], None]]) -> None:
    global _publisher
    _publisher = fn


def send_command(station_id: Optional[str], slot_id: Optional[str], command: dict) -> None:
    log_commands.warning("DEVICE COMMAND station=%s slot=%s %s", station_id, slot_id, command)
    if _publisher and station_id and slot_id:
        try:
            _publisher(station_id, slot_id, command)
        except Exception:
            log_commands.exception("Failed to publish device command")


# ####################################################################################################
# SECTION 11: ADMIN & OPERATIONS AUDIT LOGGING
# ####################################################################################################
def audit(
    db: Session,
    actor: Optional[User],
    action: str,
    entity: str,
    entity_id: Any = None,
    details: Optional[dict] = None,
    station_id: Optional[str] = None,
) -> None:
    """Append-only audit record committed alongside the caller's database transaction."""
    resolved_station = station_id or (actor.station_id if actor else None)
    if not resolved_station and isinstance(details, dict):
        resolved_station = details.get("station_id")

    db.add(
        AuditLog(
            actor_id=actor.id if actor else None,
            actor_name=(actor.username or actor.phone) if actor else "system",
            station_id=resolved_station,
            action=action,
            entity=entity,
            entity_id=None if entity_id is None else str(entity_id),
            details=details or {},
        )
    )


# ####################################################################################################
# SECTION 12: SECURITY, PASSWORD HASHING, JWT TOKENS, RBAC & OTP AUTHENTICATION
# ####################################################################################################
log_security = logging.getLogger("voltora.security")
ALGORITHM = "HS256"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
oauth2_optional = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)
bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Paste the access_token here (admins log in via two-step 2FA, so use this header).",
)


def _pick_token(oauth: Optional[str], bearer: Optional[HTTPAuthorizationCredentials]) -> Optional[str]:
    return oauth or (bearer.credentials if bearer else None)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: Optional[str]) -> bool:
    return bool(hashed) and bcrypt.checkpw(password.encode(), hashed.encode())


def normalize_phone(raw: str) -> str:
    v = re.sub(r"[\s\-()]", "", raw)
    if not re.fullmatch(r"(\+92|0)3\d{9}", v):
        raise ValueError("Enter a valid Pakistani mobile number, e.g. 0300 1234567")
    return "+92" + v[-10:]


# ====================================================================================================
# SECURITY SUB-SECTION A: JWT TOKEN ISSUANCE & VERIFICATION
# ====================================================================================================
def _encode(user: User, kind: str, delta: timedelta) -> str:
    payload = {"sub": str(user.id), "role": user.role.value, "type": kind, "exp": now_utc() + delta}
    return jwt.encode(payload, get_settings().secret_key, algorithm=ALGORITHM)


def create_access_token(user: User) -> str:
    return _encode(user, "access", timedelta(minutes=get_settings().access_token_minutes))


def create_refresh_token(user: User) -> str:
    return _encode(user, "refresh", timedelta(days=get_settings().refresh_token_days))


def token_pair(user: User) -> dict[str, str]:
    return {
        "access_token": create_access_token(user),
        "refresh_token": create_refresh_token(user),
        "token_type": "bearer",
    }


def decode_token(token: str, kind: str = "access") -> dict[str, Any]:
    payload = jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])
    if payload.get("type") != kind:
        raise jwt.InvalidTokenError("wrong token type")
    return payload


def user_from_token(db: Session, token: str, kind: str = "access") -> User:
    err = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Could not validate credentials",
        {"WWW-Authenticate": "Bearer"},
    )
    try:
        uid = int(decode_token(token, kind)["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise err
    user = db.get(User, uid)
    if user is None:
        raise err
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User is disabled")
    return user


# ====================================================================================================
# SECURITY SUB-SECTION B: FASTAPI ROLE & DEVICE DEPENDENCIES
# ====================================================================================================
def get_current_user(
    oauth: Optional[str] = Depends(oauth2_optional),
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    token = _pick_token(oauth, bearer)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", {"WWW-Authenticate": "Bearer"})
    return user_from_token(db, token)


def require_roles(*roles: Role):
    def dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires role: {', '.join(r.value for r in roles)}",
            )
        return user
    return dep


ADMIN_ROLES = (Role.ADMIN, Role.STATION_ADMIN)
require_admin = require_roles(Role.ADMIN, Role.STATION_ADMIN)
require_super_admin = require_roles(Role.ADMIN)
require_driver = require_roles(Role.DRIVER, Role.ADMIN)


def scoped_station_id(user: User, requested_station_id: Optional[str] = None) -> Optional[str]:
    """Locks STATION_ADMIN to their assigned station_id while allowing Super Admin full access."""
    if user.role == Role.STATION_ADMIN:
        if requested_station_id and requested_station_id != user.station_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access restricted to assigned station ({user.station_id})",
            )
        return user.station_id
    return requested_station_id


def require_device_or_admin(
    x_device_key: Optional[str] = Header(None),
    oauth: Optional[str] = Depends(oauth2_optional),
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> str:
    """Devices/ingestion service authenticate with X-Device-Key; human operators require an Admin JWT."""
    if x_device_key and secrets.compare_digest(x_device_key, get_settings().device_api_key):
        return "device"
    token = _pick_token(oauth, bearer)
    if token:
        user = user_from_token(db, token)
        if user.role in ADMIN_ROLES:
            return f"admin:{user.id}"
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Device key or admin token required")


# ====================================================================================================
# SECURITY SUB-SECTION C: OTP CHALLENGE GENERATION & VERIFICATION
# ====================================================================================================
def _otp_hash(code: str, challenge_id: int) -> str:
    return hmac.new(
        get_settings().secret_key.encode(),
        f"{challenge_id}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


def send_sms(phone: Optional[str], message: str) -> None:
    """SMS gateway integration point; logs OTP in non-production environments."""
    log_security.info("SMS to %s: %s", phone, message if not get_settings().is_prod else "[redacted]")


def issue_otp(
    db: Session, *, purpose: str, phone: Optional[str], user: Optional[User] = None
) -> tuple[OtpChallenge, str]:
    s = get_settings()
    recent = (
        db.scalar(
            select(func.count())
            .select_from(OtpChallenge)
            .where(OtpChallenge.phone == phone, OtpChallenge.created_at > now_utc() - timedelta(minutes=10))
        )
        if phone
        else 0
    )
    if recent >= s.otp_max_requests_per_10min:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many OTP requests, try again later")

    code = "".join(secrets.choice("0123456789") for _ in range(6))
    ch = OtpChallenge(
        purpose=purpose,
        phone=phone,
        user_id=user.id if user else None,
        expires_at=now_utc() + timedelta(seconds=s.otp_ttl_seconds),
    )
    db.add(ch)
    db.flush()
    ch.code_hash = _otp_hash(code, ch.id)
    send_sms(phone, f"Your Voltora verification code is {code}")
    return ch, code


def check_otp(db: Session, ch: Optional[OtpChallenge], code: str) -> bool:
    """Constant-time OTP verification with attempt counter persistence."""
    s = get_settings()
    if ch is None or ch.consumed or ch.expires_at < now_utc() or ch.attempts >= s.otp_max_attempts:
        return False
    ch.attempts += 1
    ok = hmac.compare_digest(ch.code_hash, _otp_hash(code, ch.id))
    if ok:
        ch.consumed = True
    db.commit()
    return ok
# ####################################################################################################
# SECTION 13: PYDANTIC REQUEST & RESPONSE SCHEMAS
# ####################################################################################################
ORM = ConfigDict(from_attributes=True)


def _phone(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    return normalize_phone(v)


# ====================================================================================================
# SCHEMAS 01: AUTHENTICATION & USER MANAGEMENT
# ====================================================================================================
class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)
    phone: Optional[str] = None
    full_name: Optional[str] = Field(None, max_length=120)

    _p = field_validator("phone")(_phone)

    @field_validator("password")
    @classmethod
    def validate_password_complexity(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if not re.search(r"[A-Z]", value):
            raise ValueError("Password must contain at least one uppercase letter (A-Z)")
        if not re.search(r"[a-z]", value):
            raise ValueError("Password must contain at least one lowercase letter (a-z)")
        if not re.search(r"\d", value):
            raise ValueError("Password must contain at least one number (0-9)")
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', value):
            raise ValueError("Password must contain at least one special character (!@#$%^&*)")
        return value


class UserOut(BaseModel):
    model_config = ORM
    id: int
    username: Optional[str] = None
    phone: Optional[str] = None
    full_name: Optional[str] = None
    role: Role
    station_id: Optional[str] = None
    is_active: bool
    is_admin: bool
    is_vip: bool
    points: int


class Token(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"


class OtpRequest(BaseModel):
    phone: str
    _p = field_validator("phone")(_phone)


class OtpVerify(BaseModel):
    phone: str
    code: str = Field(..., min_length=4, max_length=8)
    full_name: Optional[str] = Field(None, max_length=120)
    _p = field_validator("phone")(_phone)


class AdminLogin(BaseModel):
    username: str
    password: str


class AdminVerify(BaseModel):
    challenge_id: int
    code: str = Field(..., min_length=4, max_length=8)


class RefreshRequest(BaseModel):
    refresh_token: str


class UserAdminUpdate(BaseModel):
    role: Optional[Role] = None
    station_id: Optional[str] = None
    is_vip: Optional[bool] = None
    full_name: Optional[str] = None
    phone: Optional[str] = None
    _p = field_validator("phone")(_phone)


# ====================================================================================================
# SCHEMAS 02: STATIONS & LOCKERS
# ====================================================================================================
class StationCreate(BaseModel):
    id: Optional[str] = Field(None, min_length=1, max_length=40)
    name: str = Field(..., min_length=1, max_length=120)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    address: str = Field(..., min_length=1, max_length=300)
    total_lockers: int = Field(..., ge=0, le=500)
    host_name: Optional[str] = Field(None, max_length=120)
    host_revenue_share_pct: float = Field(0.0, ge=0, le=100)
    is_depot: bool = False


class StationUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    address: Optional[str] = Field(None, max_length=300)
    total_lockers: Optional[int] = Field(None, ge=0, le=500)
    host_name: Optional[str] = None
    host_revenue_share_pct: Optional[float] = Field(None, ge=0, le=100)
    target_stock: Optional[int] = Field(None, ge=0, le=1000)
    is_active: Optional[bool] = None


class LockerCreate(BaseModel):
    id: Optional[str] = Field(None, min_length=1, max_length=40)
    site_id: str
    door_number: Optional[str] = Field(None, min_length=1, max_length=40)
    max_battery_capacity: int = Field(2, ge=1, le=2)
    current_occupied_batteries: int = Field(0, ge=0)
    status: LockerStatus = LockerStatus.AVAILABLE
    door_lock_state: DoorLockState = DoorLockState.LOCKED
    reserved_for_user_id: Optional[int] = None

    @model_validator(mode="after")
    def _checks(self):
        if self.current_occupied_batteries > self.max_battery_capacity:
            raise ValueError("current_occupied_batteries exceeds max_battery_capacity")
        if self.status == LockerStatus.RESERVED_VIP and not self.reserved_for_user_id:
            raise ValueError("reserved_for_user_id is required for RESERVED_VIP lockers")
        return self


class LockerUpdate(BaseModel):
    status: Optional[LockerStatus] = Field(
        None, description="Only AVAILABLE or MAINTENANCE can be set manually."
    )
    max_battery_capacity: Optional[int] = Field(None, ge=1, le=2)
    door_lock_state: Optional[DoorLockState] = None

    @field_validator("status")
    @classmethod
    def _admin_statuses(cls, v):
        if v is not None and v not in (LockerStatus.AVAILABLE, LockerStatus.MAINTENANCE):
            raise ValueError("status can only be set to AVAILABLE or MAINTENANCE; other states are automatic")
        return v


class LockerOut(BaseModel):
    model_config = ORM
    id: str
    station_id: str
    door_number: str
    max_battery_capacity: int
    current_occupied_batteries: int
    status: LockerStatus
    door_lock_state: DoorLockState
    has_vacancy: bool


# ====================================================================================================
# SCHEMAS 03: BATTERIES & BMS TELEMETRY
# ====================================================================================================
class BatteryCreate(BaseModel):
    id: str = Field(..., min_length=1, max_length=40, examples=["VOLT-0199"])
    chemistry: Chemistry = Chemistry.LFP
    rated_capacity_ah: float = Field(20.0, gt=0, le=500)
    nominal_voltage: float = Field(48.0, gt=0, le=120)
    station_id: Optional[str] = None
    locker_id: Optional[str] = None
    state: BatteryState = BatteryState.CHARGED


class BatteryStateUpdate(BaseModel):
    state: BatteryState
    station_id: Optional[str] = None
    locker_id: Optional[str] = None


class BatteryOut(BaseModel):
    model_config = ORM
    id: str
    chemistry: Chemistry
    rated_capacity_ah: float
    nominal_voltage: float
    full_charge_capacity_ah: float
    remaining_ah: float
    voltage: float
    current_a: float
    temperature_c: float
    soc: float
    soh: float
    cycle_count: int
    fault_count: int
    operating_hours: float
    supply_voltage_v: Optional[float] = None
    health_score: float
    health_status: HealthStatus
    state: BatteryState
    station_id: Optional[str] = None
    locker_id: Optional[str] = None
    van_id: Optional[int] = None
    last_seen_at: Optional[datetime] = None


class TelemetryIn(BaseModel):
    """One telemetry frame per slot. Supports aliases: current_a/current_amperage, temperature_c/temp_c."""
    pack_id: str = Field(..., min_length=1, max_length=40)
    ts: Optional[datetime] = None
    voltage: float = Field(..., ge=0, le=120)
    current_a: float = Field(0.0, ge=-300, le=300)
    temperature_c: float = Field(..., ge=-40, le=150)
    soc: Optional[float] = Field(None, ge=0, le=100)
    soh: Optional[float] = Field(None, ge=0, le=100)
    remaining_ah: Optional[float] = Field(None, ge=0, le=500)
    full_charge_ah: Optional[float] = Field(None, gt=0, le=500)
    rated_ah: Optional[float] = Field(None, gt=0, le=500)
    cycle_count: Optional[int] = Field(None, ge=0)
    fault_count: Optional[int] = Field(None, ge=0)
    operating_hours: Optional[float] = Field(None, ge=0)
    supply_voltage_v: Optional[float] = Field(None, ge=0, le=500)
    station_id: Optional[str] = None
    slot_id: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _aliases(cls, data: Any):
        if isinstance(data, dict):
            data = dict(data)
            for old, new in (("current_amperage", "current_a"), ("temp_c", "temperature_c")):
                if old in data and new not in data:
                    data[new] = data.pop(old)
        return data


class TelemetryOut(BaseModel):
    model_config = ORM
    pack_id: str
    ts: datetime
    voltage: float
    current_a: float
    temperature_c: float
    soc: float
    soh: float
    cycle_count: int
    health_score: float
    supply_voltage_v: Optional[float] = None


# ====================================================================================================
# SCHEMAS 04: PLANS, SUBSCRIPTIONS & PAYMENTS
# ====================================================================================================
class PlanCreate(BaseModel):
    code: str = Field(..., min_length=2, max_length=30)
    name: str = Field(..., min_length=1, max_length=80)
    kind: PlanKind
    swaps_included: int = Field(0, ge=0, le=1000)
    price_pkr: float = Field(0.0, ge=0)
    duration_type: str = Field("monthly", max_length=20)
    duration_days: int = Field(30, ge=1, le=366)


class PlanUpdate(BaseModel):
    name: Optional[str] = None
    swaps_included: Optional[int] = Field(None, ge=0, le=1000)
    price_pkr: Optional[float] = Field(None, ge=0)
    duration_days: Optional[int] = Field(None, ge=1, le=366)
    is_active: Optional[bool] = None


class PlanOut(BaseModel):
    model_config = ORM
    id: int
    code: str
    name: str
    kind: PlanKind
    swaps_included: int
    price_pkr: float
    duration_type: str
    duration_days: int
    is_active: bool


class LockerRateUpdateRequest(BaseModel):
    base_daily_rate: float = Field(..., gt=0)
    discount_factor: float = Field(1.0, gt=0, le=2.0)


class SubscriptionOut(BaseModel):
    model_config = ORM
    id: int
    plan_id: int
    swaps_remaining: int
    starts_at: datetime
    ends_at: datetime
    status: SubscriptionStatus


class SubscribeRequest(BaseModel):
    payment_method: PaymentMethod
    payment_reference: str = Field(..., min_length=4, max_length=100)


class PaymentOut(BaseModel):
    model_config = ORM
    id: int
    order_id: Optional[int] = None
    purpose: str
    method: PaymentMethod
    reference: str
    amount_pkr: float
    refunded_pkr: float
    status: PaymentStatus
    refund_reason: Optional[str] = None
    created_at: datetime


class RefundRequest(BaseModel):
    amount_pkr: Optional[float] = Field(None, gt=0)
    reason: str = Field("admin refund", max_length=200)


class PaymentWebhook(BaseModel):
    reference: str
    status: str                       # SUCCEEDED | FAILED
    provider_ref: Optional[str] = None


# ====================================================================================================
# SCHEMAS 05: ORDERS, GUEST SWAPS & VAN DELIVERY WORKFLOW
# ====================================================================================================
class OrderCreate(BaseModel):
    station_id: Optional[str] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    mode: OrderMode = OrderMode.STATION
    use_subscription: bool = True
    capacity_used_ah: Optional[float] = Field(
        None, gt=0, le=200, description="Defaults to 80 % of the issued pack's capacity."
    )
    payment_method: Optional[PaymentMethod] = None
    payment_reference: Optional[str] = Field(None, min_length=4, max_length=100)
    scheduled_time: Optional[datetime] = None
    locker_id: Optional[str] = None
    delivery_address: Optional[str] = Field(None, max_length=300)
    delivery_lat: Optional[float] = Field(None, ge=-90, le=90)
    delivery_lon: Optional[float] = Field(None, ge=-180, le=180)

    @model_validator(mode="after")
    def _checks(self):
        if not self.station_id and (self.latitude is None or self.longitude is None):
            raise ValueError("Provide station_id, or both latitude and longitude")
        if self.mode == OrderMode.HOME and not (
            self.delivery_address and self.delivery_lat is not None and self.delivery_lon is not None
        ):
            raise ValueError("Home delivery needs delivery_address, delivery_lat and delivery_lon")
        return self


class OrderOut(BaseModel):
    model_config = ORM
    id: int
    order_id: str
    station_id: str
    mode: OrderMode
    status: OrderStatus
    slot_id: Optional[str] = None
    pickup_code: Optional[str] = None
    pack_out_id: Optional[str] = None
    pack_in_id: Optional[str] = None
    price: dict
    total_pkr: float
    charged_pkr: float
    actual_total_pkr: Optional[float] = None
    settlement_delta_pkr: Optional[float] = None
    delivery_address: Optional[str] = None
    delivery_otp: Optional[str] = None
    rating: Optional[int] = None
    receipt_no: Optional[str] = None
    hold_expires_at: Optional[datetime] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class UnlockRequest(BaseModel):
    pickup_code: str = Field(..., min_length=3, max_length=8)


class CompleteOrderRequest(BaseModel):
    returned_pack_id: Optional[str] = Field(None, description="Depleted pack handed back (QR scan).")


class RateRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    review: Optional[str] = Field(None, max_length=500)


class GuestQuoteRequest(BaseModel):
    site_id: Optional[str] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    pack_id: Optional[str] = Field(None, description="Returned (depleted) pack")
    voltage: Optional[float] = Field(None, gt=0, le=120)
    capacity_used_ah: float = Field(..., gt=0, le=200)
    chemistry: Chemistry = Chemistry.LFP
    temperature_c: Optional[float] = Field(None, ge=-40, le=150)
    delivery: bool = False
    is_subscribed: bool = False
    plan_code: Optional[str] = None

    @model_validator(mode="after")
    def _need_location(self):
        if not self.site_id and (self.latitude is None or self.longitude is None):
            raise ValueError("Provide site_id, or both latitude and longitude")
        return self


class GuestSwapRequest(GuestQuoteRequest):
    guest_name: Optional[str] = Field(None, max_length=80)
    phone: Optional[str] = None
    payment_method: PaymentMethod
    payment_reference: str = Field(..., min_length=4, max_length=100)
    amount_paid_pkr: Optional[float] = Field(None, ge=0)
    mode: OrderMode = OrderMode.STATION
    delivery_address: Optional[str] = None
    delivery_lat: Optional[float] = Field(None, ge=-90, le=90)
    delivery_lon: Optional[float] = Field(None, ge=-180, le=180)
    _p = field_validator("phone")(_phone)


class AddressValidationRequest(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    address_text: str


class BatteryOrderRequest(BaseModel):
    is_locker_customer: bool
    delivery_address: str
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    battery_model: Optional[str] = "Voltora-NMC-72V"
    payment_method: str
    delivery_time_slot: Optional[str] = "ASAP"
    special_instructions: Optional[str] = None


class DeliveryHandoverOTP(BaseModel):
    order_id: str
    otp_code: str
    cash_collected_amount: float = 0.0
    returned_pack_id: Optional[str] = None


class SwapRequest(BaseModel):
    rider_id: str
    station_id: str


class PhysicalSwapReturn(BaseModel):
    rider_id: str
    station_id: str
    returned_battery_id: str
    returned_soc: float = Field(..., ge=0, le=100)
    returned_soh: float = Field(..., ge=0, le=100)


# ====================================================================================================
# SCHEMAS 06: LOCKER BOOKINGS & SMART SWAP
# ====================================================================================================
class BookLockerRequest(BaseModel):
    site_id: str
    locker_id: Optional[str] = None
    action_type: ActionType = ActionType.SWAP_DROP_AND_PICK
    scheduled_time: Optional[datetime] = None

    @model_validator(mode="after")
    def _no_guest_here(self):
        if self.action_type == ActionType.GUEST_PAY_SWAP:
            raise ValueError("Guest swaps are created via /api/guest/pay-and-swap")
        return self


class SmartSwapRequest(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    max_distance_km: float = Field(25.0, gt=0, le=100)
    auto_book: bool = True
    scheduled_time: Optional[datetime] = None


class BookingActionRequest(BaseModel):
    unlock_code: Optional[str] = None


class BookingOut(BaseModel):
    model_config = ORM
    id: int
    booking_id: str
    user_id: Optional[int] = None
    station_id: str
    locker_id: str
    action_type: ActionType
    scheduled_time: datetime
    hold_expires_at: datetime
    unlock_code: str
    status: BookingStatus


# ====================================================================================================
# SCHEMAS 07: OPERATIONS, VANS, FORECASTING & ADMIN DASHBOARD CONTROLS
# ====================================================================================================
class AlertOut(BaseModel):
    model_config = ORM
    id: int
    type: AlertType
    severity: AlertSeverity
    message: str
    station_id: Optional[str] = None
    pack_id: Optional[str] = None
    acknowledged: bool
    resolved: bool
    created_at: datetime


class AuditOut(BaseModel):
    model_config = ORM
    id: int
    actor_id: Optional[int] = None
    actor_name: Optional[str] = None
    action: str
    entity: str
    entity_id: Optional[str] = None
    details: dict
    created_at: datetime


class VanCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    driver_id: Optional[int] = None
    capacity: int = Field(20, ge=1, le=200)
    depot_station_id: Optional[str] = None
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)


class VanUpdate(BaseModel):
    driver_id: Optional[int] = None
    capacity: Optional[int] = Field(None, ge=1, le=200)
    is_active: Optional[bool] = None
    depot_station_id: Optional[str] = None


class VanOut(BaseModel):
    model_config = ORM
    id: int
    name: str
    driver_id: Optional[int] = None
    capacity: int
    depot_station_id: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_active: bool


class VanTaskOut(BaseModel):
    model_config = ORM
    id: int
    van_id: Optional[int] = None
    kind: TaskKind
    status: TaskStatus
    seq: int
    station_id: Optional[str] = None
    order_id: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    address: Optional[str] = None
    drop_count: int
    pickup_count: int
    priority: float
    distance_km: float
    confirmed_drop: Optional[int] = None
    confirmed_pickup: Optional[int] = None
    confirmed_at: Optional[datetime] = None


class TaskConfirm(BaseModel):
    delivery_otp: Optional[str] = None
    confirmed_drop: Optional[int] = Field(None, ge=0)
    confirmed_pickup: Optional[int] = Field(None, ge=0)
    returned_pack_id: Optional[str] = None


class LocationUpdate(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class WeatherIn(BaseModel):
    ts: Optional[datetime] = None
    temp_c: float = Field(..., ge=-20, le=60)
    rain_mm: float = Field(0.0, ge=0, le=500)


class PlanRouteRequest(BaseModel):
    van_id: int


class TrainRequest(BaseModel):
    prefer_xgboost: bool = True


class PilotRequest(BaseModel):
    n_stations: int = Field(6, ge=2, le=30)
    days: int = Field(7, ge=1, le=60)
    seed: int = 42
    van_capacity: int = Field(20, ge=1, le=200)


class RiderStatusUpdate(BaseModel):
    status: str
    reason: Optional[str] = None


class OrderReviewRequest(BaseModel):
    action: str
    reason: Optional[str] = None


class StationCreateRequest(BaseModel):
    name: str
    city: str
    address: str
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    status: str = "OPERATIONAL"
    total_lockers: int = Field(10, ge=0, le=500)
# ####################################################################################################
# SECTION 14: DOMAIN SERVICES & HARDWARE / BACKGROUND INTEGRATIONS
# ####################################################################################################


# ====================================================================================================
# SERVICE 01: ALERTS & REAL-TIME ADMIN NOTIFICATIONS
# ====================================================================================================
def raise_alert(
    db: Session,
    type_: AlertType,
    severity: AlertSeverity,
    message: str,
    *,
    station_id: Optional[str] = None,
    pack_id: Optional[str] = None,
) -> Optional[Alert]:
    existing = db.scalar(
        select(Alert).where(
            Alert.type == type_,
            Alert.resolved.is_(False),
            Alert.station_id == station_id,
            Alert.pack_id == pack_id,
        )
    )
    if existing:
        return None
    alert = Alert(type=type_, severity=severity, message=message, station_id=station_id, pack_id=pack_id)
    db.add(alert)
    db.flush()
    ALERTS.labels(type_.value, severity.value).inc()
    hub.publish(
        "alerts",
        to_wire(
            {
                "type": "ALERT",
                "id": alert.id,
                "alert_type": type_.value,
                "severity": severity.value,
                "message": message,
                "station_id": station_id,
                "pack_id": pack_id,
                "timestamp": now_utc().isoformat(),
            }
        ),
    )
    return alert


def resolve_alerts(
    db: Session,
    type_: AlertType,
    *,
    station_id: Optional[str] = None,
    pack_id: Optional[str] = None,
) -> int:
    n = 0
    for a in db.scalars(
        select(Alert).where(
            Alert.type == type_,
            Alert.resolved.is_(False),
            Alert.station_id == station_id,
            Alert.pack_id == pack_id,
        )
    ):
        a.resolved = True
        n += 1
    return n


# ====================================================================================================
# SERVICE 02: BATTERY INVENTORY, LOCKOUTS & SECOND-LIFE RETIREMENT
# ====================================================================================================
def station_stock(db: Session, station_id: str) -> dict[str, int]:
    rows = dict(
        db.execute(
            select(Battery.state, func.count())
            .where(Battery.station_id == station_id)
            .group_by(Battery.state)
        ).all()
    )
    get = lambda s: int(rows.get(s, 0))
    return {
        "charged": get(BatteryState.CHARGED),
        "charging": get(BatteryState.CHARGING),
        "depleted": get(BatteryState.DEPLETED),
        "reserved": get(BatteryState.RESERVED),
        "locked": get(BatteryState.LOCKED),
        "retired": get(BatteryState.RETIRED),
        "total": int(sum(rows.values())),
    }


def charged_count(db: Session, station_id: str) -> int:
    return station_stock(db, station_id)["charged"]


def lock_pack(db: Session, b: Battery, reason: str) -> None:
    """Over-temperature lockout: slot is locked via MQTT/device command and pack cannot be issued."""
    b.state = BatteryState.LOCKED
    raise_alert(
        db,
        AlertType.LOCKOUT,
        AlertSeverity.CRITICAL,
        f"Pack {b.id} locked out: {reason}",
        station_id=b.station_id,
        pack_id=b.id,
    )
    send_command(b.station_id, b.locker_id, {"command": "LOCK_SLOT", "pack_id": b.id})


def retire_pack(db: Session, b: Battery) -> None:
    """Health < 70 -> retire pack to second-life stationary storage."""
    b.state = BatteryState.RETIRED
    b.reserved_order_id = None
    raise_alert(
        db,
        AlertType.PACK_RETIRED,
        AlertSeverity.WARNING,
        f"Pack {b.id} retired to second-life storage (health {b.health_score})",
        station_id=b.station_id,
        pack_id=b.id,
    )


def pick_pack(db: Session, station_id: str) -> Optional[Battery]:
    """Select best issuable pack: CHARGED, SoC >= min_issue_soc, safe temp, and healthy."""
    s = get_settings()
    q = (
        select(Battery)
        .where(
            Battery.station_id == station_id,
            Battery.state == BatteryState.CHARGED,
            Battery.soc >= s.min_issue_soc,
        )
        .order_by(Battery.health_score.desc(), Battery.soc.desc(), Battery.id)
        .with_for_update(skip_locked=True)
    )
    for b in list(db.scalars(q)):
        if b.temperature_c > s.lockout_c:
            lock_pack(db, b, f"{b.temperature_c} C")
            continue
        if b.health_score < s.retire_health:
            retire_pack(db, b)
            continue
        return b
    return None


def check_stock_alert(db: Session, station: Optional[Station]) -> None:
    if not station or station.is_depot or not station.is_active:
        return
    stock = station_stock(db, station.id)
    if stock["charged"] == 0 and (station.target_stock > 0 or stock["total"] > 0):
        raise_alert(
            db,
            AlertType.STOCK_OUT,
            AlertSeverity.CRITICAL,
            f"{station.name}: no charged packs left",
            station_id=station.id,
        )
    elif stock["charged"] > 0:
        resolve_alerts(db, AlertType.STOCK_OUT, station_id=station.id)


def apply_health(b: Battery) -> None:
    cfg = get_settings()
    r = health_score(
        soh=b.soh,
        temperature_c=b.temperature_c,
        cycles=b.cycle_count,
        faults=b.fault_count,
        operating_hours=b.operating_hours,
        chemistry=b.chemistry,
        warning=cfg.warning_health,
        retire=cfg.retire_health,
    )
    b.health_score, b.health_status = r.score, r.status


def second_life_packs(db: Session) -> list[Battery]:
    return list(db.scalars(select(Battery).where(Battery.state == BatteryState.RETIRED)))


def evaluate_pack_health(db: Session, b: Battery) -> None:
    """Trigger maintenance warnings or retirement after a health score recalculation."""
    if b.state == BatteryState.RETIRED:
        return
    if b.health_status == HealthStatus.RETIRE and b.state not in (BatteryState.IN_USE, BatteryState.RESERVED):
        retire_pack(db, b)
    elif b.health_status == HealthStatus.WARNING:
        raise_alert(
            db,
            AlertType.WEAK_PACK,
            AlertSeverity.WARNING,
            f"Pack {b.id} needs maintenance (health {b.health_score})",
            station_id=b.station_id,
            pack_id=b.id,
        )
    elif b.health_status == HealthStatus.NORMAL:
        resolve_alerts(db, AlertType.WEAK_PACK, station_id=b.station_id, pack_id=b.id)


# ====================================================================================================
# SERVICE 03: LOCKERS, SLOT RESERVATIONS & SITE AVAILABILITY
# ====================================================================================================
ACTIVE_BOOKING = (BookingStatus.PENDING, BookingStatus.CONFIRMED)
BOOKABLE = (LockerStatus.AVAILABLE, LockerStatus.ONE_LEFT)


def derive_status(l: Locker) -> LockerStatus:
    if l.status == LockerStatus.MAINTENANCE:
        return LockerStatus.MAINTENANCE
    if l.reserved_for_user_id is not None:
        return LockerStatus.RESERVED_VIP
    free = l.max_battery_capacity - l.current_occupied_batteries
    if free <= 0:
        return LockerStatus.FULL
    return LockerStatus.ONE_LEFT if free == 1 and l.max_battery_capacity > 1 else LockerStatus.AVAILABLE


def refresh_status(l: Locker) -> None:
    l.status = derive_status(l)


def lockers_at(db: Session, station_id: str, lock: bool = False) -> list[Locker]:
    q = select(Locker).where(Locker.station_id == station_id).order_by(Locker.id)
    return list(db.scalars(q.with_for_update() if lock else q))


def free_slots(db: Session, station_id: str) -> int:
    return sum(
        l.max_battery_capacity - l.current_occupied_batteries
        for l in lockers_at(db, station_id)
        if l.status in BOOKABLE
    )


def availability(free: int) -> SiteAvailability:
    return (
        SiteAvailability.FULL
        if free <= 0
        else SiteAvailability.ONE_LEFT
        if free == 1
        else SiteAvailability.AVAILABLE
    )


def station_view(db: Session, st: Station, include_lockers: bool = False) -> dict[str, Any]:
    lockers = lockers_at(db, st.id)
    counts = {s.value.lower(): 0 for s in LockerStatus}
    for l in lockers:
        counts[l.status.value.lower()] += 1
    free = sum(l.max_battery_capacity - l.current_occupied_batteries for l in lockers if l.status in BOOKABLE)
    out: dict[str, Any] = {
        "id": st.id,
        "name": st.name,
        "latitude": st.latitude,
        "longitude": st.longitude,
        "address": st.address,
        "total_lockers": st.total_lockers,
        "occupancy_status": availability(free),
        "free_slots": free,
        "lockers_registered": len(lockers),
        "locker_status_counts": counts,
        "is_active": st.is_active,
        "is_depot": st.is_depot,
        "target_stock": st.target_stock,
        "host_name": st.host_name,
        "stock": station_stock(db, st.id),
    }
    if include_lockers:
        out["lockers"] = lockers
    return out


def active_subscription(db: Session, user: Optional[User]) -> Optional[Subscription]:
    if not user:
        return None
    return db.scalar(
        select(Subscription)
        .where(
            Subscription.user_id == user.id,
            Subscription.status == SubscriptionStatus.ACTIVE,
            Subscription.swaps_remaining > 0,
            Subscription.ends_at > now_utc(),
        )
        .order_by(Subscription.ends_at)
    )


def new_unlock_code(db: Session, locker_id: str) -> str:
    in_use = set(
        db.scalars(
            select(Booking.unlock_code).where(
                Booking.locker_id == locker_id, Booking.status.in_(ACTIVE_BOOKING)
            )
        )
    )
    while True:
        code = f"#{secrets.randbelow(10000):04d}"
        if code not in in_use:
            return code


def pick_locker(db: Session, station_id: str, user: Optional[User], action: ActionType) -> Locker:
    lockers = lockers_at(db, station_id, lock=True)
    if action == ActionType.SWAP_DROP_AND_PICK and user:
        mine = [
            l
            for l in lockers
            if l.reserved_for_user_id == user.id
            and l.status != LockerStatus.MAINTENANCE
            and l.has_vacancy
        ]
        if mine:
            return min(mine, key=lambda l: (l.current_occupied_batteries, l.id))
    public = [l for l in lockers if l.status in BOOKABLE and l.has_vacancy]
    if not public:
        raise HTTPException(409, "No available locker at this site (full, reserved or under maintenance)")
    return min(public, key=lambda l: (l.current_occupied_batteries, l.id))


def release_booking(db: Session, b: Booking, new_status: BookingStatus) -> None:
    if b.status not in ACTIVE_BOOKING:
        return
    b.status = new_status
    l = db.get(Locker, b.locker_id)
    if not l:
        return
    if b.action_type == ActionType.RESERVE_VIP_SLOT:
        if l.reserved_booking_id == b.id:
            l.reserved_for_user_id = None
            l.reserved_booking_id = None
    else:
        l.current_occupied_batteries = max(0, l.current_occupied_batteries - 1)
    refresh_status(l)


def expire_stale_bookings(db: Session, now: Optional[datetime] = None) -> int:
    """Release standalone slot holds past their grace window."""
    now = now or now_utc()
    n = 0
    for b in db.scalars(
        select(Booking).where(
            Booking.status.in_(ACTIVE_BOOKING),
            Booking.hold_expires_at < now,
            Booking.order_id.is_(None),
        )
    ):
        release_booking(db, b, BookingStatus.EXPIRED)
        n += 1
    return n


def place_booking(
    db: Session,
    *,
    user: Optional[User],
    guest_ref: Optional[str] = None,
    station_id: str,
    locker_id: Optional[str] = None,
    action: ActionType = ActionType.SWAP_DROP_AND_PICK,
    scheduled_time: Optional[datetime] = None,
    status: BookingStatus = BookingStatus.CONFIRMED,
    order_id: Optional[int] = None,
    code: Optional[str] = None,
) -> Booking:
    s = get_settings()
    now = now_utc()
    when = ensure_aware(scheduled_time) if scheduled_time else now
    if when < now - timedelta(minutes=5):
        raise HTTPException(422, "scheduled_time is in the past")
    if when > now + timedelta(hours=s.max_booking_advance_hours):
        raise HTTPException(422, f"Bookings can be made at most {s.max_booking_advance_hours} hours ahead")

    expire_stale_bookings(db, now)
    st = db.get(Station, station_id)
    if not st:
        raise HTTPException(404, f"Site {station_id} not found")
    if not st.is_active:
        raise HTTPException(409, "Station is not active")
    if action == ActionType.RESERVE_VIP_SLOT and not (user and user.is_vip):
        raise HTTPException(403, "Only VIP riders can reserve a VIP locker")
    if user and action in (ActionType.SWAP_DROP_AND_PICK, ActionType.RESERVE_VIP_SLOT):
        if db.scalar(
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.user_id == user.id,
                Booking.action_type == action,
                Booking.status.in_(ACTIVE_BOOKING),
            )
        ):
            raise HTTPException(409, f"You already have an active {action.value} booking")

    if locker_id:
        locker = db.get(Locker, locker_id, with_for_update=True)
        if not locker:
            raise HTTPException(404, f"Locker {locker_id} not found")
        if locker.station_id != station_id:
            raise HTTPException(400, "Locker does not belong to this site")
    else:
        locker = pick_locker(db, station_id, user, action)

    if locker.status == LockerStatus.MAINTENANCE:
        raise HTTPException(409, "Locker is under maintenance")
    if not locker.has_vacancy:
        raise HTTPException(409, "Locker is full")
    if locker.reserved_for_user_id is not None and (not user or locker.reserved_for_user_id != user.id):
        raise HTTPException(409, "Locker is reserved for a VIP rider")
    if action == ActionType.RESERVE_VIP_SLOT and locker.reserved_for_user_id is not None:
        raise HTTPException(409, "Locker is already reserved")

    b = Booking(
        user_id=user.id if user else None,
        guest_ref=guest_ref,
        station_id=station_id,
        locker_id=locker.id,
        action_type=action,
        scheduled_time=when,
        hold_expires_at=when + timedelta(minutes=s.booking_grace_minutes),
        unlock_code=code or new_unlock_code(db, locker.id),
        status=status,
        order_id=order_id,
    )
    db.add(b)
    db.flush()
    if action == ActionType.RESERVE_VIP_SLOT:
        locker.reserved_for_user_id = user.id
        locker.reserved_booking_id = b.id
    else:
        locker.current_occupied_batteries += 1
    refresh_status(locker)
    return b


def rank_public_sites(db: Session, lat: float, lon: float, max_km: float) -> list[tuple[float, Station, int]]:
    ranked = []
    for st in db.scalars(select(Station).where(Station.is_active.is_(True), Station.is_depot.is_(False))):
        free = free_slots(db, st.id)
        if free <= 0:
            continue
        d = haversine_km(lat, lon, st.latitude, st.longitude)
        if d <= max_km:
            ranked.append((d, st, free))
    ranked.sort(key=lambda t: (t[0], t[1].id))
    return ranked


def nearest_vip_locker(db: Session, user: User, lat: float, lon: float, max_km: float):
    best = None
    for l in db.scalars(select(Locker).where(Locker.reserved_for_user_id == user.id)):
        if l.status == LockerStatus.MAINTENANCE or not l.has_vacancy:
            continue
        st = db.get(Station, l.station_id)
        if not st or not st.is_active:
            continue
        d = haversine_km(lat, lon, st.latitude, st.longitude)
        if d <= max_km and (best is None or d < best[0]):
            best = (d, st, l.id)
    return best


# ====================================================================================================
# SERVICE 04: PAYMENTS GATEWAY, CHARGES & REFUNDS
# ====================================================================================================
log_payments = logging.getLogger("voltora.payments")


@dataclass
class GatewayResult:
    ok: bool
    provider_ref: Optional[str] = None
    message: str = ""


class PaymentGateway(Protocol):
    def charge(self, reference: str, method: PaymentMethod, amount_pkr: float) -> GatewayResult: ...
    def refund(self, reference: str, amount_pkr: float) -> GatewayResult: ...


class SandboxGateway:
    """Accepts every reference except those starting with FAIL (for demos and automated tests)."""

    def charge(self, reference: str, method: PaymentMethod, amount_pkr: float) -> GatewayResult:
        if reference.upper().startswith("FAIL"):
            return GatewayResult(False, None, "Declined by sandbox")
        return GatewayResult(True, f"SBX-{reference}")

    def refund(self, reference: str, amount_pkr: float) -> GatewayResult:
        return GatewayResult(True, f"SBX-RF-{reference}")


def get_gateway() -> PaymentGateway:
    name = get_settings().payment_gateway
    if name == "sandbox":
        return SandboxGateway()
    raise HTTPException(501, f"Payment gateway '{name}' is not configured")


def charge(
    db: Session,
    *,
    user: Optional[User],
    order: Optional[Order],
    purpose: str,
    method: PaymentMethod,
    reference: str,
    amount_pkr: float,
) -> Payment:
    if db.scalar(select(Payment).where(Payment.reference == reference)):
        raise HTTPException(409, "This payment reference has already been used")
    p = Payment(
        user_id=user.id if user else None,
        order_id=order.id if order else None,
        purpose=purpose,
        method=method,
        reference=reference,
        amount_pkr=amount_pkr,
    )
    db.add(p)
    res = get_gateway().charge(reference, method, amount_pkr)
    p.status = PaymentStatus.SUCCEEDED if res.ok else PaymentStatus.FAILED
    p.provider_ref = res.provider_ref
    db.flush()
    return p


def refund(db: Session, payment: Payment, amount: Optional[float] = None, reason: str = "refund") -> Payment:
    if payment.status not in (PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED):
        raise HTTPException(409, f"Payment is {payment.status.value}; nothing to refund")
    left = round(payment.amount_pkr - payment.refunded_pkr, 2)
    amount = left if amount is None else min(round(amount, 2), left)
    if amount <= 0:
        raise HTTPException(409, "Payment is already fully refunded")
    res = get_gateway().refund(payment.reference, amount)
    if not res.ok:
        raise HTTPException(502, f"Gateway refused refund: {res.message}")
    payment.refunded_pkr = round(payment.refunded_pkr + amount, 2)
    payment.status = (
        PaymentStatus.REFUNDED
        if payment.refunded_pkr >= payment.amount_pkr - 0.005
        else PaymentStatus.PARTIALLY_REFUNDED
    )
    payment.refund_reason = reason[:200]
    log_payments.info("Refunded PKR %.2f on %s (%s)", amount, payment.reference, reason)
    return payment


def verify_webhook(body: bytes, signature: Optional[str]) -> bool:
    expected = hmac.new(get_settings().payment_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature)


def order_payment(db: Session, order_id: int) -> Optional[Payment]:
    return db.scalar(
        select(Payment)
        .where(Payment.order_id == order_id, Payment.purpose == "ORDER")
        .order_by(Payment.id.desc())
    )


payments = SimpleNamespace(
    log_payments=log_payments,
    GatewayResult=GatewayResult,
    PaymentGateway=PaymentGateway,
    SandboxGateway=SandboxGateway,
    get_gateway=get_gateway,
    charge=charge,
    refund=refund,
    verify_webhook=verify_webhook,
    order_payment=order_payment,
)


# ====================================================================================================
# SERVICE 05: TELEMETRY INGESTION, GUARDRAILS & LIVE FRAME BROADCASTING
# ====================================================================================================
log_telemetry = logging.getLogger("voltora.telemetry")


@dataclass
class IngestResult:
    pack_id: str
    duplicate: bool = False
    trip_relay: bool = False
    lockout: bool = False
    health_score: Optional[float] = None


def telemetry_frame(b: Battery, ts=None) -> dict[str, Any]:
    return {
        "type": "TELEMETRY",
        "pack_id": b.id,
        "voltage": b.voltage,
        "current_a": b.current_a,
        "soc": b.soc,
        "soh": b.soh,
        "temp_c": b.temperature_c,
        "cycle_count": b.cycle_count,
        "composite_health_score": b.health_score,
        "health_status": b.health_status.value,
        "state": b.state.value,
        "current_amperage": b.current_a,
        "temperature_c": b.temperature_c,
        "timestamp": (ts or now_utc()).isoformat(),
    }


def emergency_frame(b: Battery, command: str, alert: str, threshold: float) -> dict[str, Any]:
    return {
        "type": "EMERGENCY",
        "command": command,
        "alert": alert,
        "pack_id": b.id,
        "temp_c": b.temperature_c,
        "temperature_c": b.temperature_c,
        "threshold_c": threshold,
        "timestamp": now_utc().isoformat(),
    }


def frames_for(b: Battery) -> list[dict[str, Any]]:
    s = get_settings()
    frames = [telemetry_frame(b)]
    if b.temperature_c > s.lockout_c:
        frames.append(emergency_frame(b, "LOCK_SLOT", "OVERHEAT_LOCKOUT", s.lockout_c))
    elif b.temperature_c > s.overheat_trip_c:
        frames.append(emergency_frame(b, "TRIP_RELAY", "OVERHEAT_CRITICAL", s.overheat_trip_c))
    return frames


def merge_partial(db: Session, pack_id: str, raw: dict[str, Any]) -> TelemetryIn:
    """Fill gaps in partial WebSocket device messages from the pack's last stored state."""
    b = db.get(Battery, pack_id)
    base: dict[str, Any] = {}
    if b:
        base = {"voltage": b.voltage, "current_a": b.current_a, "temperature_c": b.temperature_c}
    raw = dict(raw)
    for old, new in (("current_amperage", "current_a"), ("temp_c", "temperature_c")):
        if old in raw:
            raw.setdefault(new, raw.pop(old))
    return TelemetryIn.model_validate({**base, **raw, "pack_id": pack_id})


def ingest_reading(db: Session, r: TelemetryIn) -> IngestResult:
    s = get_settings()
    now = now_utc()
    ts = ensure_aware(r.ts) if r.ts else now
    if ts > now + timedelta(minutes=5):
        TELEMETRY_MSGS.labels("rejected").inc()
        raise ValueError("timestamp is in the future")

    b = db.get(Battery, r.pack_id, with_for_update=True)
    if b is None:
        if not s.auto_register_packs:
            TELEMETRY_MSGS.labels("rejected").inc()
            raise LookupError(f"Unknown pack {r.pack_id}")
        b = Battery(id=r.pack_id, station_id=r.station_id, locker_id=r.slot_id)
        db.add(b)
        db.flush()

    if db.get(TelemetryReading, (r.pack_id, ts)) is not None:
        TELEMETRY_MSGS.labels("duplicate").inc()
        return IngestResult(r.pack_id, duplicate=True)

    b.voltage, b.current_a, b.temperature_c = r.voltage, r.current_a, r.temperature_c
    if r.rated_ah:
        b.rated_capacity_ah = r.rated_ah
    if r.full_charge_ah:
        b.full_charge_capacity_ah = r.full_charge_ah
    if r.remaining_ah is not None:
        b.remaining_ah = r.remaining_ah
        b.soc = soc_pct(b.remaining_ah, b.full_charge_capacity_ah)
    elif r.soc is not None:
        b.soc = r.soc
        b.remaining_ah = r.soc / 100.0 * b.full_charge_capacity_ah
    if r.full_charge_ah or r.rated_ah:
        b.soh = soh_pct(b.full_charge_capacity_ah, b.rated_capacity_ah)
    elif r.soh is not None:
        b.soh = r.soh
        b.full_charge_capacity_ah = r.soh / 100.0 * b.rated_capacity_ah
    if r.cycle_count is not None:
        b.cycle_count = r.cycle_count
    if r.fault_count is not None:
        b.fault_count = r.fault_count
    if r.operating_hours is not None:
        b.operating_hours = r.operating_hours
    if r.supply_voltage_v is not None:
        b.supply_voltage_v = r.supply_voltage_v
    if r.station_id and not b.station_id:
        b.station_id = r.station_id
    if r.slot_id and not b.locker_id:
        b.locker_id = r.slot_id
    b.last_seen_at = now
    apply_health(b)

    db.add(
        TelemetryReading(
            pack_id=b.id,
            ts=ts,
            received_at=now,
            station_id=b.station_id,
            slot_id=b.locker_id,
            voltage=b.voltage,
            current_a=b.current_a,
            temperature_c=b.temperature_c,
            soc=b.soc,
            soh=b.soh,
            remaining_ah=b.remaining_ah,
            cycle_count=b.cycle_count,
            supply_voltage_v=b.supply_voltage_v,
            health_score=b.health_score,
        )
    )

    result = IngestResult(b.id, health_score=b.health_score)
    if b.temperature_c > s.lockout_c:
        result.lockout = result.trip_relay = True
        if b.state != BatteryState.LOCKED:
            lock_pack(db, b, f"{b.temperature_c} C > {s.lockout_c} C")
    elif b.temperature_c > s.overheat_trip_c:
        result.trip_relay = True
        raise_alert(
            db,
            AlertType.OVER_TEMP,
            AlertSeverity.WARNING,
            f"Pack {b.id} at {b.temperature_c} C",
            station_id=b.station_id,
            pack_id=b.id,
        )
        send_command(b.station_id, b.locker_id, {"command": "TRIP_RELAY", "pack_id": b.id})
    else:
        resolve_alerts(db, AlertType.OVER_TEMP, station_id=b.station_id, pack_id=b.id)

    if b.supply_voltage_v is not None and b.supply_voltage_v < s.low_supply_v:
        raise_alert(
            db,
            AlertType.LOW_SUPPLY,
            AlertSeverity.WARNING,
            f"Low mains voltage {b.supply_voltage_v} V at {b.station_id or 'unknown station'}",
            station_id=b.station_id,
            pack_id=None,
        )
    elif b.supply_voltage_v is not None:
        resolve_alerts(db, AlertType.LOW_SUPPLY, station_id=b.station_id, pack_id=None)

    if b.state == BatteryState.CHARGING and b.soc >= 95 and b.temperature_c <= s.lockout_c:
        b.state = BatteryState.CHARGED
    evaluate_pack_health(db, b)
    if b.station_id:
        st = db.get(Station, b.station_id)
        if st:
            db.flush()
            check_stock_alert(db, st)
    db.commit()

    TELEMETRY_MSGS.labels("accepted").inc()
    for f in frames_for(b):
        hub.publish(f"pack:{b.id}", to_wire(f))
    return result


# ====================================================================================================
# SERVICE 06: SWAP ORDER LIFECYCLE, SUBSCRIPTIONS & SETTLEMENT
# ====================================================================================================
log_swap = logging.getLogger("voltora.swap")
OPEN_ORDER = (OrderStatus.PAID, OrderStatus.IN_PROGRESS)
DEFAULT_CAPACITY_FRACTION = 0.8


def get_pricing_config(db: Session) -> PricingConfig:
    row = db.get(AppSetting, "pricing")
    return PricingConfig(**row.value) if row else PricingConfig()


def stock_ratio(db: Session, st: Station) -> Optional[float]:
    return station_stock(db, st.id)["charged"] / st.target_stock if st.target_stock > 0 else None


def resolve_station(db: Session, station_id: Optional[str], lat: Optional[float], lon: Optional[float]) -> Station:
    if station_id:
        st = db.get(Station, station_id)
        if not st:
            raise HTTPException(404, f"Station {station_id} not found")
        return st
    best = None
    for st in db.scalars(select(Station).where(Station.is_active.is_(True), Station.is_depot.is_(False))):
        if station_stock(db, st.id)["charged"] <= 0:
            continue
        d = haversine_km(lat, lon, st.latitude, st.longitude)
        if d <= 25 and (best is None or d < best[0]):
            best = (d, st)
    if not best:
        raise HTTPException(404, "No station with a charged pack within 25 km")
    return best[1]


def _code() -> str:
    return f"#{secrets.randbelow(10000):04d}"


def _guest_ref() -> str:
    return f"GUEST-{secrets.token_hex(3).upper()}"


def subscribe(db: Session, user: User, plan: Plan, method: PaymentMethod, reference: str) -> Subscription:
    if plan.kind == PlanKind.PER_SWAP or not plan.is_active:
        raise HTTPException(400, "This plan cannot be subscribed to")
    if db.scalar(
        select(func.count())
        .select_from(Subscription)
        .where(
            Subscription.user_id == user.id,
            Subscription.status == SubscriptionStatus.ACTIVE,
            Subscription.ends_at > now_utc(),
        )
    ):
        raise HTTPException(409, "You already have an active subscription")
    pay = payments.charge(
        db,
        user=user,
        order=None,
        purpose="PLAN",
        method=method,
        reference=reference,
        amount_pkr=plan.price_pkr,
    )
    if pay.status != PaymentStatus.SUCCEEDED:
        db.commit()
        raise HTTPException(402, "Payment could not be verified")
    sub = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        swaps_remaining=plan.swaps_included,
        ends_at=now_utc() + timedelta(days=plan.duration_days),
    )
    db.add(sub)
    db.flush()
    pay.subscription_id = sub.id
    db.commit()
    return sub


def release_order(
    db: Session, order: Order, new_status: OrderStatus, *, refund: bool = True, reason: str = ""
) -> None:
    """Release reserved pack, locker hold, subscription credit, and pending delivery tasks."""
    if order.pack_out_id:
        pack = db.get(Battery, order.pack_out_id)
        if pack and pack.state == BatteryState.RESERVED:
            pack.state, pack.reserved_order_id = BatteryState.CHARGED, None
    if order.booking_id:
        b = db.get(Booking, order.booking_id)
        if b:
            release_booking(
                db,
                b,
                BookingStatus.CANCELLED if new_status == OrderStatus.CANCELLED else BookingStatus.EXPIRED,
            )
    if order.subscription_id and order.status in OPEN_ORDER:
        sub = db.get(Subscription, order.subscription_id)
        if sub:
            sub.swaps_remaining += 1
    for t in db.scalars(
        select(VanTask).where(VanTask.order_id == order.id, VanTask.status == TaskStatus.PENDING)
    ):
        t.status = TaskStatus.CANCELLED
    if refund:
        pay = payments.order_payment(db, order.id)
        if pay and pay.status in (PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED):
            payments.refund(db, pay, None, reason or f"order {new_status.value.lower()}")
    order.status = new_status
    ORDERS.labels(new_status.value).inc()


def _create_delivery_task(db: Session, order: Order, st: Station) -> VanTask:
    vans = list(db.scalars(select(Van).where(Van.is_active.is_(True), Van.latitude.is_not(None))))
    van = min(
        vans,
        key=lambda v: haversine_km(v.latitude, v.longitude, order.delivery_lat, order.delivery_lon),
        default=None,
    )
    t = VanTask(
        van_id=van.id if van else None,
        kind=TaskKind.DELIVERY,
        station_id=st.id,
        order_id=order.id,
        latitude=order.delivery_lat,
        longitude=order.delivery_lon,
        address=order.delivery_address,
        drop_count=1,
        pickup_count=1,
    )
    db.add(t)
    db.flush()
    return t


def create_order(
    db: Session,
    *,
    user: Optional[User],
    req: OrderCreate,
    guest_name: Optional[str] = None,
    guest_phone: Optional[str] = None,
    capacity_used_ah: Optional[float] = None,
) -> Order:
    s = get_settings()
    if user and db.scalar(
        select(func.count())
        .select_from(Order)
        .where(Order.user_id == user.id, Order.status.in_(OPEN_ORDER))
    ):
        raise HTTPException(409, "You already have an open swap order")

    st = resolve_station(db, req.station_id, req.latitude, req.longitude)
    if not st.is_active or st.is_depot:
        raise HTTPException(409, "Station is not open to riders")

    pack = pick_pack(db, st.id)
    if pack is None:
        raise_alert(
            db,
            AlertType.STOCK_OUT,
            AlertSeverity.CRITICAL,
            f"{st.name}: swap refused, no charged pack",
            station_id=st.id,
        )
        db.commit()
        raise HTTPException(409, "No charged pack available at this station")

    cfg = get_pricing_config(db)
    now = now_utc()
    delivery = req.mode == OrderMode.HOME
    ah = capacity_used_ah or req.capacity_used_ah or round(
        pack.full_charge_capacity_ah * DEFAULT_CAPACITY_FRACTION, 2
    )
    df = demand_factor(now, stock_ratio(db, st), cfg)
    price = calculate_price(
        voltage=pack.nominal_voltage,
        capacity_used_ah=ah,
        chemistry=pack.chemistry,
        delivery=delivery,
        demand=df,
        cfg=cfg,
    )
    sub = active_subscription(db, user) if (user and req.use_subscription) else None
    charge_amount = price.delivery_component_pkr if sub else price.total_pkr

    order = Order(
        user_id=user.id if user else None,
        guest_ref=None if user else _guest_ref(),
        guest_name=guest_name,
        guest_phone=guest_phone,
        station_id=st.id,
        mode=req.mode,
        status=OrderStatus.PENDING_PAYMENT,
        subscription_id=sub.id if sub else None,
        plan_id=sub.plan_id if sub else None,
        pack_out_id=pack.id,
        price=price.model_dump(),
        total_pkr=price.total_pkr,
        charged_pkr=charge_amount,
        pickup_code=_code(),
        delivery_address=req.delivery_address,
        delivery_lat=req.delivery_lat,
        delivery_lon=req.delivery_lon,
        delivery_otp=_code() if delivery else None,
        hold_expires_at=None if delivery else now + timedelta(minutes=s.booking_grace_minutes),
    )
    db.add(order)
    db.flush()
    pack.state, pack.reserved_order_id = BatteryState.RESERVED, order.id
    order.slot_id = pack.locker_id

    if req.mode == OrderMode.LOCKER:
        bk = place_booking(
            db,
            user=user,
            guest_ref=order.guest_ref,
            station_id=st.id,
            locker_id=req.locker_id,
            action=ActionType.SWAP_DROP_AND_PICK if user else ActionType.GUEST_PAY_SWAP,
            scheduled_time=req.scheduled_time,
            status=BookingStatus.PENDING,
            order_id=order.id,
            code=order.pickup_code,
        )
        order.booking_id, order.slot_id = bk.id, bk.locker_id
        order.hold_expires_at = bk.hold_expires_at

    if charge_amount > 0:
        if not (req.payment_method and req.payment_reference):
            raise HTTPException(
                402,
                f"Pay first: PKR {charge_amount:.2f} due (payment_method and payment_reference required)",
            )
        pay = payments.charge(
            db,
            user=user,
            order=order,
            purpose="ORDER",
            method=req.payment_method,
            reference=req.payment_reference,
            amount_pkr=charge_amount,
        )
        if pay.status != PaymentStatus.SUCCEEDED:
            release_order(db, order, OrderStatus.FAILED, refund=False)
            db.commit()
            raise HTTPException(402, "Payment could not be verified")

    if sub:
        sub.swaps_remaining -= 1
    order.status, order.paid_at = OrderStatus.PAID, now
    if order.booking_id:
        bk = db.get(Booking, order.booking_id)
        if bk:
            bk.status = BookingStatus.CONFIRMED
    if delivery:
        _create_delivery_task(db, order, st)
    db.commit()
    ORDERS.labels("PAID").inc()
    return order


def unlock_order(db: Session, order: Order, code: str) -> Order:
    if order.status != OrderStatus.PAID:
        raise HTTPException(409, f"Order is {order.status.value}")
    if order.mode == OrderMode.HOME:
        raise HTTPException(400, "Home-delivery orders are handed over by the van driver")
    if not order.pickup_code or not secrets.compare_digest(code.encode(), order.pickup_code.encode()):
        raise HTTPException(403, "Invalid pickup code")
    slot = db.get(Locker, order.slot_id) if order.slot_id else None
    if slot:
        slot.door_lock_state = DoorLockState.UNLOCKED
    order.status = OrderStatus.IN_PROGRESS
    db.commit()
    return order


def complete_order(db: Session, order: Order, returned_pack_id: Optional[str] = None) -> Order:
    """Finalize swap: release fresh pack, inspect returned pack, settle price delta, and award points."""
    if order.status not in OPEN_ORDER:
        raise HTTPException(409, f"Order is {order.status.value}")
    cfg = get_settings()
    out = db.get(Battery, order.pack_out_id) if order.pack_out_id else None
    if out:
        out.state, out.station_id, out.locker_id, out.van_id, out.reserved_order_id = (
            BatteryState.IN_USE,
            None,
            None,
            None,
            None,
        )

    if returned_pack_id:
        old = db.get(Battery, returned_pack_id)
        if not old:
            raise HTTPException(404, f"Returned pack {returned_pack_id} not found")
        order.pack_in_id = old.id
        old.station_id = order.station_id
        old.state = BatteryState.DEPLETED
        old.van_id = None
        apply_health(old)
        evaluate_pack_health(db, old)
        ah_used = max(0.0, old.full_charge_capacity_ah - old.remaining_ah)
        if ah_used > 0:
            price_cfg = get_pricing_config(db)
            actual = calculate_price(
                voltage=old.nominal_voltage,
                capacity_used_ah=ah_used,
                chemistry=old.chemistry,
                delivery=order.mode == OrderMode.HOME,
                demand=order.price.get("demand_factor", 1.0),
                cfg=price_cfg,
                returned_temp_c=old.temperature_c,
            )
            order.actual_total_pkr = actual.total_pkr
            if order.subscription_id is None:
                order.settlement_delta_pkr = money(actual.total_pkr - order.charged_pkr)
                if order.settlement_delta_pkr < -1.0:
                    pay = payments.order_payment(db, order.id)
                    if pay and pay.status == PaymentStatus.SUCCEEDED:
                        payments.refund(
                            db, pay, -order.settlement_delta_pkr, "settlement on actual energy used"
                        )

    if order.booking_id:
        b = db.get(Booking, order.booking_id)
        if b:
            release_booking(db, b, BookingStatus.COMPLETED)

    order.status, order.completed_at = OrderStatus.COMPLETED, now_utc()
    order.receipt_no = f"RCP-{order.id:06d}"
    if order.user_id:
        u = db.get(User, order.user_id)
        if u:
            u.points += cfg.rider_points_per_swap
    st = db.get(Station, order.station_id)
    db.flush()
    check_stock_alert(db, st)
    db.commit()
    ORDERS.labels("COMPLETED").inc()
    return order


def cancel_order(db: Session, order: Order, reason: str = "cancelled by rider") -> Order:
    if order.status != OrderStatus.PAID:
        raise HTTPException(
            409,
            f"Only paid, not-yet-collected orders can be cancelled (order is {order.status.value})",
        )
    release_order(db, order, OrderStatus.CANCELLED, refund=True, reason=reason)
    db.commit()
    return order


def expire_stale_orders(db: Session) -> int:
    now = now_utc()
    n = 0
    for o in db.scalars(
        select(Order).where(
            Order.status == OrderStatus.PAID,
            Order.hold_expires_at.is_not(None),
            Order.hold_expires_at < now,
        )
    ):
        release_order(db, o, OrderStatus.EXPIRED, refund=True, reason="not collected in time")
        n += 1
    if n:
        db.commit()
    return n


def receipt(db: Session, order: Order) -> dict[str, Any]:
    pay = payments.order_payment(db, order.id)
    return {
        "receipt_no": order.receipt_no,
        "order_id": order.order_id,
        "date": order.completed_at or order.created_at,
        "station_id": order.station_id,
        "mode": order.mode,
        "pack_issued": order.pack_out_id,
        "pack_returned": order.pack_in_id,
        "breakdown": order.price,
        "total_pkr": order.total_pkr,
        "charged_pkr": order.charged_pkr,
        "actual_total_pkr": order.actual_total_pkr,
        "settlement_delta_pkr": order.settlement_delta_pkr,
        "covered_by_subscription": order.subscription_id is not None,
        "payment": {
            "method": pay.method,
            "reference": pay.reference,
            "status": pay.status,
            "refunded_pkr": pay.refunded_pkr,
        }
        if pay
        else None,
    }


swap = SimpleNamespace(
    log_swap=log_swap,
    OPEN_ORDER=OPEN_ORDER,
    DEFAULT_CAPACITY_FRACTION=DEFAULT_CAPACITY_FRACTION,
    get_pricing_config=get_pricing_config,
    stock_ratio=stock_ratio,
    resolve_station=resolve_station,
    _code=_code,
    _guest_ref=_guest_ref,
    subscribe=subscribe,
    release_order=release_order,
    create_order=create_order,
    _create_delivery_task=_create_delivery_task,
    unlock_order=unlock_order,
    complete_order=complete_order,
    cancel_order=cancel_order,
    expire_stale_orders=expire_stale_orders,
    receipt=receipt,
)


# ====================================================================================================
# SERVICE 07: FLEET REBALANCING, DRIVER TASKS & CHARGING QUEUE
# ====================================================================================================
def station_states(db: Session) -> list[StationState]:
    out = []
    for st in db.scalars(select(Station).where(Station.is_active.is_(True), Station.is_depot.is_(False))):
        k = station_stock(db, st.id)
        out.append(StationState(st.id, st.latitude, st.longitude, st.target_stock, k["charged"], k["depleted"]))
    return out


def _van_packs(db: Session, van: Van) -> list[Battery]:
    return list(
        db.scalars(
            select(Battery)
            .where(Battery.van_id == van.id, Battery.state == BatteryState.ON_VAN)
            .order_by(Battery.soc.desc())
        )
    )


def generate_plan(db: Session, van: Van) -> list[VanTask]:
    """Load charged packs from depot, build greedy rebalancing route, and persist VanTask stops."""
    if van.latitude is None or van.longitude is None:
        raise HTTPException(409, "Van has no known position yet (driver must send a location, or set one)")

    for t in db.scalars(
        select(VanTask).where(
            VanTask.van_id == van.id,
            VanTask.kind == TaskKind.REBALANCE,
            VanTask.status == TaskStatus.PENDING,
        )
    ):
        t.status = TaskStatus.CANCELLED

    depot = db.get(Station, van.depot_station_id) if van.depot_station_id else None
    onboard = _van_packs(db, van)
    if depot and len(onboard) < van.capacity:
        free = van.capacity - len(onboard)
        q = (
            select(Battery)
            .where(Battery.station_id == depot.id, Battery.state == BatteryState.CHARGED)
            .order_by(Battery.health_score.desc())
            .limit(free)
        )
        for b in db.scalars(q):
            b.state, b.van_id, b.station_id, b.locker_id = BatteryState.ON_VAN, van.id, None, None
            onboard.append(b)
    db.flush()

    stops = plan_route(van.latitude, van.longitude, len(onboard), station_states(db), van.capacity)
    tasks = []
    for i, s in enumerate(stops, 1):
        st = db.get(Station, s.station_id)
        if not st:
            continue
        t = VanTask(
            van_id=van.id,
            kind=TaskKind.REBALANCE,
            station_id=s.station_id,
            seq=i,
            latitude=st.latitude,
            longitude=st.longitude,
            address=st.address,
            drop_count=s.drop,
            pickup_count=s.pickup,
            priority=s.priority,
            distance_km=s.distance_km,
        )
        db.add(t)
        tasks.append(t)
    db.commit()
    return tasks


def _check_van(task: VanTask, actor: User, db: Session) -> Optional[Van]:
    van = db.get(Van, task.van_id) if task.van_id else None
    if actor.is_admin:
        return van
    if not van or van.driver_id != actor.id:
        raise HTTPException(403, "This task is not assigned to your van")
    return van


def confirm_task(
    db: Session,
    task: VanTask,
    actor: User,
    *,
    delivery_otp: Optional[str] = None,
    confirmed_drop: Optional[int] = None,
    confirmed_pickup: Optional[int] = None,
    returned_pack_id: Optional[str] = None,
) -> VanTask:
    if task.status != TaskStatus.PENDING:
        raise HTTPException(409, f"Task is {task.status.value}")
    van = _check_van(task, actor, db)

    if task.kind == TaskKind.DELIVERY:
        order = db.get(Order, task.order_id) if task.order_id else None
        if not order:
            raise HTTPException(404, "Delivery order not found")
        if order.status not in (OrderStatus.PAID, OrderStatus.IN_PROGRESS):
            raise HTTPException(409, f"Order is {order.status.value}")
        if not delivery_otp or delivery_otp != order.delivery_otp:
            raise HTTPException(403, "Invalid delivery OTP")
        order.status = OrderStatus.IN_PROGRESS
        task.status, task.confirmed_at, task.confirmed_drop = TaskStatus.DONE, now_utc(), 1
        db.flush()
        swap.complete_order(db, order, returned_pack_id)
        db.refresh(task)
        return task

    drop = task.drop_count if confirmed_drop is None else confirmed_drop
    pick = task.pickup_count if confirmed_pickup is None else confirmed_pickup
    st = db.get(Station, task.station_id) if task.station_id else None
    if not st:
        raise HTTPException(404, "Target station not found")

    if van:
        carried = _van_packs(db, van)
        loaded = [b for b in carried if b.soc >= 90][:drop]
        if len(loaded) < drop:
            raise HTTPException(409, f"Van carries only {len(loaded)} charged packs, cannot drop {drop}")
        for b in loaded:
            b.state, b.van_id, b.station_id = BatteryState.CHARGED, None, st.id
        depleted = list(
            db.scalars(
                select(Battery)
                .where(Battery.station_id == st.id, Battery.state == BatteryState.DEPLETED)
                .limit(pick)
            )
        )
        for b in depleted:
            b.state, b.van_id, b.station_id, b.locker_id = BatteryState.ON_VAN, van.id, None, None

    task.confirmed_drop, task.confirmed_pickup = drop, pick
    task.status, task.confirmed_at = TaskStatus.DONE, now_utc()
    db.flush()
    check_stock_alert(db, st)
    db.commit()
    return task


def return_to_depot(db: Session, van: Van) -> int:
    """Unload van packs back to depot: depleted packs enter CHARGING, >=95% SoC remain CHARGED."""
    n = 0
    for b in _van_packs(db, van):
        b.state = BatteryState.CHARGED if b.soc >= 95 else BatteryState.CHARGING
        b.station_id, b.van_id, b.locker_id = van.depot_station_id, None, None
        n += 1
    db.commit()
    return n


def charging_plan(db: Session) -> list[dict[str, Any]]:
    """Prioritize charging depleted packs at high-shortage stations first, then lowest SoC."""
    pri = {s.id: priority(s.target, s.current) for s in station_states(db)}
    rows = []
    for b in db.scalars(select(Battery).where(Battery.state.in_((BatteryState.DEPLETED, BatteryState.CHARGING)))):
        rows.append(
            {
                "pack_id": b.id,
                "station_id": b.station_id,
                "state": b.state,
                "soc": b.soc,
                "health_score": b.health_score,
                "station_priority": round(pri.get(b.station_id, 0.0), 4),
                "supply_voltage_v": b.supply_voltage_v,
            }
        )
    rows.sort(key=lambda r: (-r["station_priority"], r["soc"], r["pack_id"]))
    return rows


fleet = SimpleNamespace(
    station_states=station_states,
    _van_packs=_van_packs,
    generate_plan=generate_plan,
    _check_van=_check_van,
    confirm_task=confirm_task,
    return_to_depot=return_to_depot,
    charging_plan=charging_plan,
)


# ====================================================================================================
# SERVICE 08: FORECASTING PIPELINE, DRIFT CHECK & TARGET REFRESH
# ====================================================================================================
log_forecasting = logging.getLogger("voltora.forecast")
HORIZON_HOURS = 12


def _bucket(d: datetime) -> datetime:
    return d.astimezone(PKT).replace(minute=0, second=0, microsecond=0, tzinfo=None)


def build_rows(db: Session, days: int = 90) -> list[fc.Row]:
    since = now_utc() - timedelta(days=days)
    counts: dict[tuple[str, datetime], int] = defaultdict(int)
    for o in db.scalars(
        select(Order).where(
            Order.created_at >= since,
            Order.status.in_((OrderStatus.PAID, OrderStatus.IN_PROGRESS, OrderStatus.COMPLETED)),
        )
    ):
        counts[(o.station_id, _bucket(o.created_at))] += 1

    weather = {
        _bucket(w.ts): w
        for w in db.scalars(select(WeatherObservation).where(WeatherObservation.ts >= since))
    }
    rows = []
    for (sid, hour), n in counts.items():
        w = weather.get(hour)
        rows.append(fc.Row(sid, hour, float(n), w.temp_c if w else fc.DEFAULT_TEMP_C, w.rain_mm if w else 0.0))
    return rows


def _log_mlflow(mv: ForecastModelVersion) -> None:
    try:
        import mlflow

        with mlflow.start_run(run_name=f"demand_v{mv.id}"):
            mlflow.log_params({"algorithm": mv.algorithm, "n_rows": mv.n_rows})
            mlflow.log_metrics({k: v for k, v in mv.metrics.items() if isinstance(v, (int, float))})
    except Exception:
        log_forecasting.debug("MLflow logging skipped")


def train_and_register(
    db: Session, prefer_xgb: bool = True, rows: Optional[list[fc.Row]] = None
) -> ForecastModelVersion:
    rows = rows if rows is not None else build_rows(db)
    if not rows:
        raise ValueError("No order history to train on yet")
    model, metrics = fc.train(rows, prefer_xgb)
    os.makedirs(get_settings().model_dir, exist_ok=True)
    mv = ForecastModelVersion(algorithm=model.algorithm, metrics=metrics, artifact_path="", n_rows=len(rows))
    db.add(mv)
    db.flush()
    mv.artifact_path = os.path.join(get_settings().model_dir, f"demand_v{mv.id}.pkl")
    fc.save(model, mv.artifact_path)
    for old in db.scalars(select(ForecastModelVersion).where(ForecastModelVersion.is_active.is_(True))):
        old.is_active = False
    mv.is_active = True
    db.commit()
    _log_mlflow(mv)
    return mv


def active_model(db: Session):
    mv = db.scalar(select(ForecastModelVersion).where(ForecastModelVersion.is_active.is_(True)))
    return (fc.load(mv.artifact_path), mv) if mv and os.path.exists(mv.artifact_path) else (None, None)


def refresh_targets(db: Session, safety: float = 1.2) -> list[ForecastRecord]:
    """Forecast next-12-hour demand per station and update Station.target_stock."""
    model, mv = active_model(db)
    if model is None:
        model = fc.SeasonalBaseline().fit(build_rows(db) or [fc.Row("_", datetime(2000, 1, 3), 0.0)])
    start = _bucket(now_utc()) + timedelta(hours=1)
    weather = {
        _bucket(w.ts): {"temp_c": w.temp_c, "rain_mm": w.rain_mm}
        for w in db.scalars(select(WeatherObservation).where(WeatherObservation.ts >= now_utc() - timedelta(hours=1)))
    }
    out = []
    for st in db.scalars(select(Station).where(Station.is_active.is_(True), Station.is_depot.is_(False))):
        demand = fc.forecast_demand(model, st.id, start, HORIZON_HOURS, weather)
        target = fc.target_stock(demand, capacity=max(st.total_lockers * 2, 1), safety=safety)
        st.target_stock = target
        rec = ForecastRecord(
            station_id=st.id,
            model_id=mv.id if mv else None,
            horizon_hours=HORIZON_HOURS,
            predicted_demand=round(demand, 3),
            target_stock=target,
        )
        db.add(rec)
        out.append(rec)
    db.commit()
    return out


def drift_check(db: Session, window_days: int = 7, tolerance: float = 1.5) -> dict[str, Any]:
    model, mv = active_model(db)
    if model is None:
        return {"status": "NO_MODEL", "retrain": True}
    recent = build_rows(db, window_days)
    if not recent:
        return {"status": "NO_RECENT_DATA", "retrain": False}
    mae = sum(abs(model.predict(r.station_id, r.ts, r.temp_c, r.rain_mm) - r.demand) for r in recent) / len(recent)
    ref = mv.metrics.get("mae")
    drift = ref is not None and ref > 0 and mae > ref * tolerance
    return {"status": "DRIFT" if drift else "OK", "recent_mae": round(mae, 4), "training_mae": ref, "retrain": bool(drift)}


forecasting = SimpleNamespace(
    log_forecasting=log_forecasting,
    HORIZON_HOURS=HORIZON_HOURS,
    _bucket=_bucket,
    build_rows=build_rows,
    train_and_register=train_and_register,
    _log_mlflow=_log_mlflow,
    active_model=active_model,
    refresh_targets=refresh_targets,
    drift_check=drift_check,
)


# ====================================================================================================
# SERVICE 09: DATABASE BOOTSTRAP & DEMO SEEDING
# ====================================================================================================
log_bootstrap = logging.getLogger("voltora.bootstrap")

DEFAULT_PLANS = [
    ("PER_SWAP", "Pay per swap", PlanKind.PER_SWAP, 0, 0.0),
    ("MONTHLY_10", "Monthly 10 swaps", PlanKind.MONTHLY, 10, 1000.0),
    ("MONTHLY_30", "Monthly 30 swaps", PlanKind.MONTHLY, 30, 2700.0),
    ("MONTHLY_60", "Monthly 60 swaps", PlanKind.MONTHLY, 60, 5000.0),
    ("LOCKER_30", "Locker plan (30 swaps)", PlanKind.LOCKER, 30, 3200.0),
]


def seed_defaults(db: Session) -> None:
    if not db.get(AppSetting, "pricing"):
        db.add(AppSetting(key="pricing", value=PricingConfig().model_dump(mode="json")))
    for code, name, kind, swaps, price in DEFAULT_PLANS:
        if not db.scalar(select(Plan).where(Plan.code == code)):
            db.add(Plan(code=code, name=name, kind=kind, swaps_included=swaps, price_pkr=price))
    db.commit()


def bootstrap_admin(db: Session) -> None:
    s = get_settings()

    # 1. Create Super Admin if configured
    if s.bootstrap_admin_password and not db.scalar(
        select(User).where(User.username == s.bootstrap_admin_username)
    ):
        phone = normalize_phone(s.bootstrap_admin_phone) if s.bootstrap_admin_phone else None
        db.add(
            User(
                username=s.bootstrap_admin_username,
                hashed_password=hash_password(s.bootstrap_admin_password),
                role=Role.ADMIN,
                phone=phone,
                full_name="Bootstrap admin",
            )
        )
        db.commit()
        log_bootstrap.warning("Bootstrap admin '%s' created", s.bootstrap_admin_username)

    # 2. Seed Default Locker Pass Plans
    locker_plans = [
        {
            "code": "LOCKER_WK",
            "name": "Weekly Locker Pass",
            "kind": PlanKind.LOCKER,
            "duration_type": "weekly",
            "duration_days": 7,
            "swaps_included": 0,
            "price_pkr": calculate_locker_plan_price("weekly", base_daily_rate=50.0),
            "is_active": True,
        },
        {
            "code": "LOCKER_MO",
            "name": "Monthly Locker Pass",
            "kind": PlanKind.LOCKER,
            "duration_type": "monthly",
            "duration_days": 30,
            "swaps_included": 0,
            "price_pkr": calculate_locker_plan_price("monthly", base_daily_rate=50.0),
            "is_active": True,
        },
        {
            "code": "LOCKER_YR",
            "name": "Yearly Locker Pass",
            "kind": PlanKind.LOCKER,
            "duration_type": "yearly",
            "duration_days": 365,
            "swaps_included": 0,
            "price_pkr": calculate_locker_plan_price("yearly", base_daily_rate=50.0),
            "is_active": True,
        },
    ]

    for p in locker_plans:
        existing_plan = db.scalar(select(Plan).where(Plan.code == p["code"]))
        if not existing_plan:
            db.add(Plan(**p))
        else:
            existing_plan.name = p["name"]
            existing_plan.kind = p["kind"]
            existing_plan.duration_type = p["duration_type"]
            existing_plan.duration_days = p["duration_days"]
            existing_plan.price_pkr = p["price_pkr"]
            existing_plan.swaps_included = p["swaps_included"]
    db.commit()

    # 3. Seed 5 Default Riders
    riders = [
        ("rider1", "+923001111001", "Rider One"),
        ("rider2", "+923001111002", "Rider Two"),
        ("rider3", "+923001111003", "Rider Three"),
        ("rider4", "+923001111004", "Rider Four"),
        ("rider5", "+923001111005", "Rider Five"),
    ]
    for uname, phone, name in riders:
        if not db.scalar(select(User).where(User.username == uname)):
            db.add(
                User(
                    username=uname,
                    phone=normalize_phone(phone),
                    hashed_password=hash_password("Rider@2026!"),
                    role=Role.RIDER,
                    full_name=name,
                )
            )
    db.commit()

    # 4. Seed 3 Delivery Van Drivers
    drivers = [
        ("driver1", "+923002222001", "Delivery Driver One"),
        ("driver2", "+923002222002", "Delivery Driver Two"),
        ("driver3", "+923002222003", "Delivery Driver Three"),
    ]
    for uname, phone, name in drivers:
        if not db.scalar(select(User).where(User.username == uname)):
            db.add(
                User(
                    username=uname,
                    phone=normalize_phone(phone),
                    hashed_password=hash_password("Driver@2026!"),
                    role=Role.DRIVER,
                    full_name=name,
                )
            )
    db.commit()


DEMO_STATIONS = [
    ("STATION-01", "Saddar Hub", 24.8607, 67.0099, "Saddar, Karachi"),
    ("STATION-02", "Clifton Block 5", 24.8138, 67.0300, "Clifton, Karachi"),
    ("STATION-03", "Gulshan-e-Iqbal", 24.9215, 67.0929, "Gulshan-e-Iqbal, Karachi"),
    ("STATION-04", "North Nazimabad", 24.9400, 67.0400, "North Nazimabad, Karachi"),
    ("STATION-05", "Korangi Crossing", 24.8300, 67.1300, "Korangi, Karachi"),
]


def seed_demo(db: Session) -> None:
    n_pack = 0
    for sid, name, lat, lon, addr in DEMO_STATIONS + [
        ("DEPOT-01", "Central Depot", 24.8800, 67.0700, "Depot, Karachi")
    ]:
        if db.get(Station, sid):
            continue
        depot = sid.startswith("DEPOT")
        db.add(
            Station(
                id=sid,
                name=name,
                latitude=lat,
                longitude=lon,
                address=addr,
                total_lockers=4,
                is_depot=depot,
                target_stock=0 if depot else 4,
            )
        )
        db.flush()

        station_lockers: dict[str, Locker] = {}
        for d in range(1, 5):
            lid = f"{sid}-L{d}"
            locker = Locker(id=lid, station_id=sid, door_number=f"Door {d:02d}")
            db.add(locker)
            station_lockers[lid] = locker
        db.flush()

        for k in range(8 if depot else 4):
            n_pack += 1
            target_lid = f"{sid}-L{(k % 4) + 1}"
            db.add(
                Battery(
                    id=f"VOLT-{n_pack:04d}",
                    station_id=sid,
                    locker_id=target_lid,
                    state=BatteryState.CHARGED,
                )
            )
            locker_obj = station_lockers[target_lid]
            locker_obj.current_occupied_batteries = min(
                locker_obj.max_battery_capacity, locker_obj.current_occupied_batteries + 1
            )
            refresh_status(locker_obj)

    if not db.scalar(select(Van).where(Van.name == "Van-1")):
        driver = db.scalar(select(User).where(User.username == "driver1"))
        db.add(
            Van(
                name="Van-1",
                driver_id=driver.id if driver else None,
                capacity=20,
                depot_station_id="DEPOT-01",
                latitude=24.88,
                longitude=67.07,
            )
        )
        # Seed 6 Station Admin Accounts (1 per station)
    station_admins = [
        ("admin_depot", "Depot@2026!", "Central Depot Admin", "+923003330000", "DEPOT-01"),
        ("admin_saddar", "Saddar@2026!", "Saddar Hub Admin", "+923003330001", "STATION-01"),
        ("admin_clifton", "Clifton@2026!", "Clifton Block 5 Admin", "+923003330002", "STATION-02"),
        ("admin_gulshan", "Gulshan@2026!", "Gulshan-e-Iqbal Admin", "+923003330003", "STATION-03"),
        ("admin_nazimabad", "Nazimabad@2026!", "North Nazimabad Admin", "+923003330004", "STATION-04"),
        ("admin_korangi", "Korangi@2026!", "Korangi Crossing Admin", "+923003330005", "STATION-05"),
    ]
    for uname, pwd, full_name, phone, st_id in station_admins:
        if not db.scalar(select(User).where(User.username == uname)):
            db.add(
                User(
                    username=uname,
                    phone=normalize_phone(phone),
                    hashed_password=hash_password(pwd),
                    role=Role.STATION_ADMIN,
                    station_id=st_id,
                    full_name=full_name,
                    is_active=True,
                )
            )
    db.commit()


# ====================================================================================================
# SERVICE 10: MQTT BROKER INGESTOR & COMMAND PUBLISHER
# ====================================================================================================
log_mqtt_ingest = logging.getLogger("voltora.mqtt")


def handle_message(topic: str, payload: bytes) -> str:
    """Validate and store a single MQTT telemetry payload without crashing the network loop."""
    parts = topic.split("/")
    try:
        data = json.loads(payload)
        if len(parts) >= 4:
            data.setdefault("station_id", parts[1])
            data.setdefault("slot_id", parts[2])
        reading = TelemetryIn.model_validate(data)
        with SessionLocal() as db:
            res = ingest_reading(db, reading)
        return "duplicate" if res.duplicate else "accepted"
    except (json.JSONDecodeError, ValidationError, ValueError, LookupError, AttributeError, TypeError) as exc:
        TELEMETRY_MSGS.labels("rejected").inc()
        log_mqtt_ingest.warning("Rejected telemetry on %s: %s", topic, str(exc)[:160])
        return "rejected"
    except Exception:
        log_mqtt_ingest.exception("Telemetry handler failed on %s", topic)
        return "rejected"


class MqttIngestor:
    def __init__(self) -> None:
        import paho.mqtt.client as mqtt

        s = get_settings()
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        except AttributeError:
            self.client = mqtt.Client()
        if s.mqtt_username:
            self.client.username_pw_set(s.mqtt_username, s.mqtt_password)
        self.client.on_connect = self._on_connect
        self.client.on_message = lambda c, u, m: handle_message(m.topic, m.payload)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

    def _on_connect(self, client, userdata, flags, rc, *args):
        log_mqtt_ingest.info("MQTT connected (rc=%s); subscribing", rc)
        client.subscribe(get_settings().mqtt_telemetry_topic, qos=1)

    def publish_command(self, station_id: str, slot_id: str, command: dict[str, Any]) -> None:
        topic = get_settings().mqtt_command_topic.format(station_id=station_id, slot_id=slot_id)
        self.client.publish(topic, json.dumps(command), qos=1)

    def start(self) -> None:
        s = get_settings()
        self.client.connect_async(s.mqtt_host, s.mqtt_port, keepalive=30)
        self.client.loop_start()
        register_publisher(self.publish_command)

    def stop(self) -> None:
        register_publisher(None)
        self.client.loop_stop()
        self.client.disconnect()


# ====================================================================================================
# SERVICE 11: BACKGROUND JOBS & PERIODIC SWEEPER
# ====================================================================================================
log_jobs = logging.getLogger("voltora.jobs")


def retrain_forecast() -> dict[str, Any]:
    with SessionLocal() as db:
        drift = forecasting.drift_check(db)
        if drift.get("retrain"):
            mv = forecasting.train_and_register(db)
            return {"retrained": mv.id, "drift": drift}
        return {"retrained": None, "drift": drift}


def daily_targets_and_routes() -> dict[str, Any]:
    """Forecast 12h demand -> update S_target -> generate greedy rebalancing routes for active vans."""
    with SessionLocal() as db:
        recs = forecasting.refresh_targets(db)
        planned: dict[str, Any] = {}
        for van in db.scalars(select(Van).where(Van.is_active.is_(True))):
            try:
                planned[van.name] = len(fleet.generate_plan(db, van))
            except Exception as exc:
                planned[van.name] = f"skipped: {exc}"
        return {"targets": len(recs), "routes": planned}


def sweep() -> dict[str, int]:
    with SessionLocal() as db:
        n = expire_stale_bookings(db)
        o = swap.expire_stale_orders(db)
        db.commit()
        return {"bookings_expired": n, "orders_expired": o}


JOBS = {
    "retrain_forecast": retrain_forecast,
    "daily_targets_and_routes": daily_targets_and_routes,
    "sweep": sweep,
}
# ####################################################################################################
# SECTION 15: API ROUTERS (AUTH, RIDER, GUEST & ADMIN)
# ####################################################################################################


# ====================================================================================================
# ROUTER 01: AUTHENTICATION & USER MANAGEMENT (/register, /token, /auth/*, /users/*)
# ====================================================================================================
auth_router = APIRouter(tags=["auth"])


@auth_router.post("/register", response_model=UserOut, status_code=201)
def register(body: UserCreate, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(409, "Username is already registered")
    if body.phone and db.scalar(select(User).where(User.phone == body.phone)):
        raise HTTPException(409, "Phone number is already registered")

    user = User(
        username=body.username,
        phone=body.phone,
        full_name=body.full_name,
        hashed_password=hash_password(body.password),
        role=Role.RIDER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@auth_router.post("/token", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == form.username))
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect username or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User is disabled")
    if user.role in ADMIN_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin accounts must sign in with 2FA via /auth/admin/login")
    user.last_login_at = now_utc()
    db.commit()
    return token_pair(user)


@auth_router.post("/auth/refresh", response_model=Token)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    user = user_from_token(db, body.refresh_token, kind="refresh")
    return token_pair(user)


@auth_router.post("/auth/otp/request")
def otp_request(body: OtpRequest, db: Session = Depends(get_db)):
    ch, code = issue_otp(db, purpose="LOGIN", phone=body.phone)
    db.commit()
    s = get_settings()
    out: dict[str, Any] = {
        "challenge_id": ch.id,
        "expires_in": s.otp_ttl_seconds,
        "message": "OTP sent",
    }
    if s.otp_dev_echo or not s.is_prod:
        out["dev_code"] = code
    return out


@auth_router.post("/auth/otp/verify", response_model=Token)
def otp_verify(body: OtpVerify, db: Session = Depends(get_db)):
    ch = db.scalar(
        select(OtpChallenge)
        .where(
            OtpChallenge.phone == body.phone,
            OtpChallenge.purpose == "LOGIN",
            OtpChallenge.consumed.is_(False),
        )
        .order_by(OtpChallenge.id.desc())
    )
    if not check_otp(db, ch, body.code):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired OTP")

    user = db.scalar(select(User).where(User.phone == body.phone))
    if user is None:
        user = User(phone=body.phone, full_name=body.full_name, role=Role.RIDER)
        db.add(user)
        db.flush()
    elif body.full_name and not user.full_name:
        user.full_name = body.full_name

    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User is disabled")
    user.last_login_at = now_utc()
    db.commit()
    return token_pair(user)


@auth_router.post("/auth/admin/login")
def admin_login(body: AdminLogin, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == body.username))
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    if user.role not in ADMIN_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not an admin account")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User is disabled")

    ch, code = issue_otp(db, purpose="ADMIN_2FA", phone=user.phone, user=user)
    db.commit()
    s = get_settings()
    out: dict[str, Any] = {
        "challenge_id": ch.id,
        "expires_in": s.otp_ttl_seconds,
        "message": "2FA code sent",
    }
    if s.otp_dev_echo or not s.is_prod:
        out["dev_code"] = code
    return out


@auth_router.post("/auth/admin/verify", response_model=Token)
def admin_verify(body: AdminVerify, db: Session = Depends(get_db)):
    ch = db.get(OtpChallenge, body.challenge_id)
    if not ch or ch.purpose != "ADMIN_2FA" or not check_otp(db, ch, body.code):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired 2FA code")
    user = db.get(User, ch.user_id) if ch.user_id else None
    if not user or user.role not in ADMIN_ROLES or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid admin session")
    user.last_login_at = now_utc()
    audit(db, user, "ADMIN_LOGIN", "user", user.id)
    db.commit()
    return token_pair(user)


@auth_router.get("/users/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@auth_router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    return list(db.scalars(select(User).order_by(User.id)))


@auth_router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UserAdminUpdate,
    actor: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    old_role = user.role
    updates = body.model_dump(exclude_unset=True)
    target_role = updates.get("role", user.role)

    if target_role == Role.STATION_ADMIN:
        target_station = updates.get("station_id", user.station_id)
        if not target_station:
            raise HTTPException(422, "station_id is required when assigning STATION_ADMIN")
        if not db.get(Station, target_station):
            raise HTTPException(404, f"Station '{target_station}' not found")
    elif "role" in updates and target_role != Role.STATION_ADMIN:
        updates["station_id"] = None

    for k, v in updates.items():
        setattr(user, k, v)
    if body.role and body.role != old_role:
        audit(
            db,
            actor,
            "ROLE_CHANGE",
            "user",
            user.id,
            {"from": old_role.value, "to": body.role.value, "station_id": user.station_id},
        )
    db.commit()
    db.refresh(user)
    return user


# ====================================================================================================
# ROUTER 02: RIDER PORTAL (/api/rider/*)
# ====================================================================================================
rider_router = APIRouter(prefix="/api/rider", tags=["rider"])


def _own_order(db: Session, user: User, order_id: int) -> Order:
    o = db.get(Order, order_id)
    if not o or (o.user_id != user.id and not user.is_admin):
        raise HTTPException(404, "Order not found")
    return o


def _own_booking(db: Session, user: User, booking_id: int) -> Booking:
    b = db.get(Booking, booking_id)
    if not b or (b.user_id != user.id and not user.is_admin):
        raise HTTPException(404, "Booking not found")
    return b


@rider_router.get("/subscription", response_model=Optional[SubscriptionOut])
def my_subscription(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return active_subscription(db, user)


@rider_router.post("/plans/{plan_id}/subscribe", response_model=SubscriptionOut, status_code=201)
def subscribe_plan(
    plan_id: int,
    body: SubscribeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    plan = db.get(Plan, plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    return swap.subscribe(db, user, plan, body.payment_method, body.payment_reference)


@rider_router.post("/orders", response_model=OrderOut, status_code=201)
def create_rider_order(
    body: OrderCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return swap.create_order(db, user=user, req=body)


@rider_router.get("/orders", response_model=list[OrderOut])
def list_orders(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return list(db.scalars(select(Order).where(Order.user_id == user.id).order_by(Order.id.desc()).limit(100)))


@rider_router.get("/orders/{order_id}", response_model=OrderOut)
def get_order(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _own_order(db, user, order_id)


@rider_router.post("/orders/{order_id}/unlock", response_model=OrderOut)
def unlock_rider_order(
    order_id: int,
    body: UnlockRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return swap.unlock_order(db, _own_order(db, user, order_id), body.pickup_code)


@rider_router.post("/orders/{order_id}/complete", response_model=OrderOut)
def complete_rider_order(
    order_id: int,
    body: CompleteOrderRequest = CompleteOrderRequest(),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return swap.complete_order(db, _own_order(db, user, order_id), body.returned_pack_id)


@rider_router.post("/orders/{order_id}/cancel", response_model=OrderOut)
def cancel_rider_order(
    order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return swap.cancel_order(db, _own_order(db, user, order_id))


@rider_router.post("/orders/{order_id}/rate", response_model=OrderOut)
def rate_order(
    order_id: int,
    body: RateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    o = _own_order(db, user, order_id)
    if o.status != OrderStatus.COMPLETED:
        raise HTTPException(409, "Only completed orders can be rated")
    o.rating, o.review = body.rating, body.review
    db.commit()
    return o


@rider_router.get("/orders/{order_id}/receipt")
def get_receipt(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    o = _own_order(db, user, order_id)
    if o.status != OrderStatus.COMPLETED:
        raise HTTPException(409, "Receipt is available once the order is completed")
    return swap.receipt(db, o)


@rider_router.post("/book-locker", response_model=BookingOut, status_code=201)
def book_locker(
    body: BookLockerRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    b = place_booking(
        db,
        user=user,
        station_id=body.site_id,
        locker_id=body.locker_id,
        action=body.action_type,
        scheduled_time=body.scheduled_time,
    )
    db.commit()
    db.refresh(b)
    return b


@rider_router.post("/smart-swap")
def smart_swap(
    body: SmartSwapRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    expire_stale_bookings(db)
    vip = nearest_vip_locker(db, user, body.latitude, body.longitude, body.max_distance_km)
    if vip:
        dist_km, st, locker_id = vip
        tier = RiderTier.VIP
    else:
        ranked = rank_public_sites(db, body.latitude, body.longitude, body.max_distance_km)
        if not ranked:
            raise HTTPException(404, f"No site with a free locker within {body.max_distance_km} km")
        dist_km, st, _ = ranked[0]
        locker_id = None
        tier = RiderTier.SUBSCRIBED if active_subscription(db, user) else RiderTier.GUEST

    booking = None
    if body.auto_book:
        booking = place_booking(
            db,
            user=user,
            station_id=st.id,
            locker_id=locker_id,
            action=ActionType.SWAP_DROP_AND_PICK,
            scheduled_time=body.scheduled_time,
        )
        locker_id = booking.locker_id
    db.commit()
    return {
        "rider_tier": tier,
        "site": station_view(db, st),
        "distance_km": round(dist_km, 2),
        "locker_id": locker_id,
        "booking": BookingOut.model_validate(booking) if booking else None,
    }


@rider_router.get("/bookings", response_model=list[BookingOut])
def my_bookings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    expire_stale_bookings(db)
    db.commit()
    return list(db.scalars(select(Booking).where(Booking.user_id == user.id).order_by(Booking.id.desc())))


@rider_router.post("/bookings/{booking_id}/confirm", response_model=BookingOut)
def confirm_booking(
    booking_id: int,
    body: BookingActionRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    b = _own_booking(db, user, booking_id)
    if b.status != BookingStatus.PENDING:
        raise HTTPException(409, f"Booking is {b.status.value}")
    if not body.unlock_code or body.unlock_code != b.unlock_code:
        raise HTTPException(403, "Invalid unlock code")
    b.status = BookingStatus.CONFIRMED
    db.commit()
    return b


@rider_router.post("/bookings/{booking_id}/cancel", response_model=BookingOut)
def cancel_booking(
    booking_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    b = _own_booking(db, user, booking_id)
    if b.status not in ACTIVE_BOOKING:
        raise HTTPException(409, f"Booking is {b.status.value}")
    release_booking(db, b, BookingStatus.CANCELLED)
    db.commit()
    return b


@rider_router.post("/delivery/validate-address")
def validate_delivery_address(
    req: AddressValidationRequest,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stations = list(db.scalars(select(Station).where(Station.is_active.is_(True))))
    if not stations:
        return {"is_serviceable": False, "message": "No active service stations configured."}
    nearest = min(stations, key=lambda s: haversine_km(req.latitude, req.longitude, s.latitude, s.longitude))
    dist_km = round(haversine_km(req.latitude, req.longitude, nearest.latitude, nearest.longitude), 2)
    is_serviceable = dist_km <= 25.0
    cfg = get_pricing_config(db)
    return {
        "is_serviceable": is_serviceable,
        "nearest_station_id": nearest.id,
        "nearest_station_name": nearest.name,
        "distance_km": dist_km,
        "estimated_delivery_fee_pkr": cfg.delivery_cost_pkr if is_serviceable else None,
        "message": (
            f"Address is within {dist_km} km of {nearest.name}."
            if is_serviceable
            else f"Location is {dist_km} km away (outside 25 km delivery radius)."
        ),
    }


@rider_router.get("/battery-catalogue")
def get_battery_catalogue(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cfg = get_pricing_config(db)
    lfp_count = db.scalar(
        select(func.count())
        .select_from(Battery)
        .where(Battery.chemistry == Chemistry.LFP, Battery.state == BatteryState.CHARGED)
    ) or 0
    nmc_count = db.scalar(
        select(func.count())
        .select_from(Battery)
        .where(Battery.chemistry == Chemistry.NMC, Battery.state == BatteryState.CHARGED)
    ) or 0
    return {
        "batteries": [
            {
                "id": "VOLTORA-LFP-48V",
                "name": "Voltora LFP 48V 20Ah Pack",
                "chemistry": Chemistry.LFP.value,
                "nominal_voltage": 48.0,
                "rated_capacity_ah": 20.0,
                "wear_rate_pkr_per_kwh": cfg.wear_pkr_per_kwh_lfp,
                "charged_available": int(lfp_count),
            },
            {
                "id": "VOLTORA-NMC-48V",
                "name": "Voltora NMC 48V 20Ah High-Density Pack",
                "chemistry": Chemistry.NMC.value,
                "nominal_voltage": 48.0,
                "rated_capacity_ah": 20.0,
                "wear_rate_pkr_per_kwh": cfg.wear_pkr_per_kwh_nmc,
                "charged_available": int(nmc_count),
            },
        ]
    }


@rider_router.post("/orders/create")
def create_battery_delivery_order(
    order: BatteryOrderRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pay_method = PaymentMethod.WALLET
    if order.payment_method.upper() in PaymentMethod.__members__:
        pay_method = PaymentMethod[order.payment_method.upper()]
    created = swap.create_order(
        db,
        user=user,
        req=OrderCreate(
            latitude=order.latitude,
            longitude=order.longitude,
            mode=OrderMode.HOME,
            use_subscription=order.is_locker_customer,
            payment_method=pay_method,
            payment_reference=f"APP-{secrets.token_hex(6).upper()}",
            delivery_address=order.delivery_address,
            delivery_lat=order.latitude,
            delivery_lon=order.longitude,
        ),
    )
    return {
        "order_id": created.order_id,
        "id": created.id,
        "status": created.status.value,
        "delivery_otp": created.delivery_otp,
        "total_pkr": created.total_pkr,
        "charged_pkr": created.charged_pkr,
        "station_id": created.station_id,
        "message": "Battery delivery order scheduled and dispatched to nearest van.",
    }


# ====================================================================================================
# ROUTER 03: GUEST & PUBLIC ENDPOINTS (/api/guest/*)
# ====================================================================================================
guest_router = APIRouter(prefix="/api/guest", tags=["guest"])


@guest_router.get("/stations")
def public_stations(db: Session = Depends(get_db)):
    expire_stale_bookings(db)
    db.commit()
    return [
        station_view(db, st)
        for st in db.scalars(
            select(Station).where(Station.is_active.is_(True), Station.is_depot.is_(False)).order_by(Station.id)
        )
    ]


@guest_router.get("/stations/{station_id}")
def public_station(station_id: str, db: Session = Depends(get_db)):
    expire_stale_bookings(db)
    db.commit()
    st = db.get(Station, station_id)
    if not st or not st.is_active or st.is_depot:
        raise HTTPException(404, "Station not found")
    view = station_view(db, st, include_lockers=True)
    view["lockers"] = [LockerOut.model_validate(l) for l in view["lockers"]]
    return view


@guest_router.get("/plans", response_model=list[PlanOut])
def public_plans(db: Session = Depends(get_db)):
    return list(db.scalars(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.id)))


@guest_router.post("/quote")
def guest_quote(body: GuestQuoteRequest, db: Session = Depends(get_db)):
    st = swap.resolve_station(db, body.site_id, body.latitude, body.longitude)
    pack = db.get(Battery, body.pack_id) if body.pack_id else None
    if body.pack_id and not pack:
        raise HTTPException(404, f"Pack {body.pack_id} not found")
    cfg = swap.get_pricing_config(db)
    price = calculate_price(
        voltage=body.voltage or (pack.nominal_voltage if pack else 48.0),
        capacity_used_ah=body.capacity_used_ah,
        chemistry=pack.chemistry if pack else body.chemistry,
        delivery=body.delivery,
        demand=demand_factor(now_utc(), swap.stock_ratio(db, st), cfg),
        cfg=cfg,
        returned_temp_c=body.temperature_c if body.temperature_c is not None else (pack.temperature_c if pack else None),
        is_subscribed=body.is_subscribed,
        plan_code=body.plan_code,
    )
    return {
        "station_id": st.id,
        "station_name": st.name,
        "charged_packs_available": station_stock(db, st.id)["charged"],
        "price": price,
    }


@guest_router.post("/pay-and-swap", response_model=OrderOut, status_code=201)
def guest_pay_and_swap(body: GuestSwapRequest, db: Session = Depends(get_db)):
    st = swap.resolve_station(db, body.site_id, body.latitude, body.longitude)
    cfg = swap.get_pricing_config(db)
    expected = calculate_price(
        voltage=body.voltage or 48.0,
        capacity_used_ah=body.capacity_used_ah,
        chemistry=body.chemistry,
        delivery=body.delivery or body.mode == OrderMode.HOME,
        demand=demand_factor(now_utc(), swap.stock_ratio(db, st), cfg),
        cfg=cfg,
    ).total_pkr
    if body.amount_paid_pkr is not None and body.amount_paid_pkr + 0.01 < expected:
        raise HTTPException(402, f"Pay first: PKR {expected:.2f} due, received PKR {body.amount_paid_pkr:.2f}")
    req = OrderCreate(
        station_id=st.id,
        mode=OrderMode.HOME if body.delivery else body.mode,
        use_subscription=False,
        capacity_used_ah=body.capacity_used_ah,
        payment_method=body.payment_method,
        payment_reference=body.payment_reference,
        delivery_address=body.delivery_address,
        delivery_lat=body.delivery_lat,
        delivery_lon=body.delivery_lon,
    )
    return swap.create_order(
        db,
        user=None,
        req=req,
        guest_name=body.guest_name,
        guest_phone=body.phone,
        capacity_used_ah=body.capacity_used_ah,
    )


# ====================================================================================================
# ROUTER 04: ADMIN OPERATIONS & ANALYTICS (/api/admin/*)
# ====================================================================================================
admin_router = APIRouter(
    prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)]
)


@admin_router.get("/sites")
def list_sites(
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    expire_stale_bookings(db)
    db.commit()
    target_station = scoped_station_id(actor)
    q = select(Station).order_by(Station.id)
    if target_station:
        q = q.where(Station.id == target_station)
    out = []
    for st in db.scalars(q):
        v = station_view(db, st, include_lockers=True)
        v["lockers"] = [LockerOut.model_validate(l) for l in v["lockers"]]
        out.append(v)
    return out


@admin_router.post("/sites", status_code=201)
def create_site(body: StationCreate, actor: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    sid = body.id or f"STATION-{secrets.token_hex(3).upper()}"
    if db.get(Station, sid):
        raise HTTPException(409, f"Site {sid} already exists")
    st = Station(
        id=sid,
        name=body.name,
        latitude=body.latitude,
        longitude=body.longitude,
        address=body.address,
        total_lockers=body.total_lockers,
        host_name=body.host_name,
        host_revenue_share_pct=body.host_revenue_share_pct,
        is_depot=body.is_depot,
    )
    db.add(st)
    audit(db, actor, "CREATE_SITE", "station", sid, {"name": body.name})
    db.commit()
    return station_view(db, st, include_lockers=True)


@admin_router.post("/stations", status_code=201)
def create_station_portal(
    req: StationCreateRequest,
    actor: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Create a station from the Admin portal and auto-provision its locker slots."""
    sid = f"STN-{secrets.token_hex(3).upper()}"
    st = Station(
        id=sid,
        name=req.name,
        latitude=req.latitude,
        longitude=req.longitude,
        address=f"{req.address}, {req.city}",
        total_lockers=req.total_lockers,
        is_active=(req.status.upper() == "OPERATIONAL"),
        target_stock=max(2, req.total_lockers),
    )
    db.add(st)
    db.flush()
    for d in range(1, req.total_lockers + 1):
        db.add(Locker(id=f"{sid}-L{d}", station_id=sid, door_number=f"Door {d:02d}"))
    audit(db, actor, "CREATE_STATION", "station", sid, {"name": req.name, "lockers": req.total_lockers})
    db.commit()
    return {
        "station_id": sid,
        "status": "CREATED",
        "details": station_view(db, st, include_lockers=True),
    }


@admin_router.patch("/sites/{station_id}")
def update_site(
    station_id: str,
    body: StationUpdate,
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    scoped_station_id(actor, station_id)
    st = db.get(Station, station_id)
    if not st:
        raise HTTPException(404, "Site not found")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(st, k, v)
    audit(db, actor, "UPDATE_SITE", "station", station_id, changes)
    db.commit()
    return station_view(db, st)


@admin_router.post("/lockers", response_model=LockerOut, status_code=201)
def create_locker(body: LockerCreate, actor: User = Depends(require_admin), db: Session = Depends(get_db)):
    scoped_station_id(actor, body.site_id)
    if not db.get(Station, body.site_id):
        raise HTTPException(404, f"Site {body.site_id} not found")
    if body.reserved_for_user_id and not db.get(User, body.reserved_for_user_id):
        raise HTTPException(404, "VIP user not found")
    lid = body.id or f"LKR-{secrets.token_hex(3).upper()}"
    if db.get(Locker, lid):
        raise HTTPException(409, f"Locker {lid} already exists")
    existing = db.scalar(select(func.count()).select_from(Locker).where(Locker.station_id == body.site_id))
    locker = Locker(
        id=lid,
        station_id=body.site_id,
        door_number=body.door_number or f"Door {existing + 1:02d}",
        max_battery_capacity=body.max_battery_capacity,
        current_occupied_batteries=body.current_occupied_batteries,
        status=body.status,
        door_lock_state=body.door_lock_state,
        reserved_for_user_id=body.reserved_for_user_id,
    )
    refresh_status(locker)
    db.add(locker)
    audit(db, actor, "CREATE_LOCKER", "locker", lid, {"site_id": body.site_id})
    db.commit()
    return locker


@admin_router.patch("/lockers/{locker_id}", response_model=LockerOut)
def update_locker(
    locker_id: str,
    body: LockerUpdate,
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    l = db.get(Locker, locker_id)
    if not l:
        raise HTTPException(404, "Locker not found")
    scoped_station_id(actor, l.station_id)
    if body.max_battery_capacity is not None:
        if body.max_battery_capacity < l.current_occupied_batteries:
            raise HTTPException(409, "New capacity is less than current occupancy")
        l.max_battery_capacity = body.max_battery_capacity
    if body.door_lock_state is not None:
        l.door_lock_state = body.door_lock_state
    if body.status == LockerStatus.MAINTENANCE:
        l.status = LockerStatus.MAINTENANCE
    elif body.status == LockerStatus.AVAILABLE and l.status == LockerStatus.MAINTENANCE:
        l.status = LockerStatus.AVAILABLE
        refresh_status(l)
    else:
        refresh_status(l)
    audit(db, actor, "UPDATE_LOCKER", "locker", locker_id, body.model_dump(exclude_unset=True, mode="json"))
    db.commit()
    return l


@admin_router.get("/batteries", response_model=list[BatteryOut])
def list_batteries(
    station_id: Optional[str] = None,
    state: Optional[BatteryState] = None,
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    station_id = scoped_station_id(actor, station_id)
    q = select(Battery).order_by(Battery.id)
    if station_id:
        q = q.where(Battery.station_id == station_id)
    if state:
        q = q.where(Battery.state == state)
    return list(db.scalars(q))

@admin_router.post("/batteries", response_model=BatteryOut, status_code=201)
def create_battery(body: BatteryCreate, actor: User = Depends(require_admin), db: Session = Depends(get_db)):
    target_station = scoped_station_id(actor, body.station_id)
    if target_station:
        body.station_id = target_station
    if db.get(Battery, body.id):
        raise HTTPException(409, f"Pack {body.id} already exists")
    if body.station_id and not db.get(Station, body.station_id):
        raise HTTPException(404, "Station not found")
    b = Battery(
        id=body.id,
        chemistry=body.chemistry,
        rated_capacity_ah=body.rated_capacity_ah,
        nominal_voltage=body.nominal_voltage,
        full_charge_capacity_ah=body.rated_capacity_ah,
        remaining_ah=body.rated_capacity_ah,
        voltage=body.nominal_voltage,
        station_id=body.station_id,
        locker_id=body.locker_id,
        state=body.state,
    )
    db.add(b)
    audit(db, actor, "REGISTER_PACK", "battery", body.id)
    db.commit()
    return b


@admin_router.patch("/batteries/{pack_id}", response_model=BatteryOut)
def update_battery(
    pack_id: str,
    body: BatteryStateUpdate,
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    b = db.get(Battery, pack_id)
    if not b:
        raise HTTPException(404, "Pack not found")
    scoped_station_id(actor, b.station_id)
    if body.state == BatteryState.CHARGED and b.temperature_c > get_settings().lockout_c:
        raise HTTPException(409, f"Pack is at {b.temperature_c} C, cool down before unlocking")
    b.state = body.state
    if body.station_id is not None:
        target_station = scoped_station_id(actor, body.station_id)
        b.station_id = target_station or (body.station_id or None)
    if body.state == BatteryState.CHARGED:
        resolve_alerts(db, AlertType.LOCKOUT, pack_id=b.id)
    audit(db, actor, "SET_PACK_STATE", "battery", pack_id, {"state": body.state.value})
    db.commit()
    return b


@admin_router.get("/batteries/second-life", response_model=list[BatteryOut])
def get_second_life_batteries(actor: User = Depends(require_admin), db: Session = Depends(get_db)):
    packs = second_life_packs(db)
    target_station = scoped_station_id(actor)
    if target_station:
        return [p for p in packs if p.station_id == target_station]
    return packs


@admin_router.get("/pricing", response_model=PricingConfig)
def get_pricing(db: Session = Depends(get_db)):
    return swap.get_pricing_config(db)


@admin_router.put("/pricing", response_model=PricingConfig)
def set_pricing(body: PricingConfig, actor: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    row = db.get(AppSetting, "pricing")
    data = body.model_dump(mode="json")
    if row:
        row.value = data
    else:
        db.add(AppSetting(key="pricing", value=data))
    audit(db, actor, "UPDATE_PRICING", "app_setting", "pricing", data)
    db.commit()
    return body


@admin_router.post("/pricing/update-locker-rates")
def update_locker_rates(
    req: LockerRateUpdateRequest,
    actor: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Dynamically recalculate Weekly, Monthly, and Yearly locker pass rates from a base daily rate."""
    tiers = ["weekly", "monthly", "yearly"]
    updated_plans = []

    for duration in tiers:
        new_price = calculate_locker_plan_price(
            duration_type=duration,
            base_daily_rate=req.base_daily_rate,
            discount_factor=req.discount_factor,
        )
        plan = db.scalar(
            select(Plan).where(Plan.kind == PlanKind.LOCKER, Plan.duration_type == duration)
        )
        if plan:
            plan.price_pkr = new_price
            updated_plans.append(
                {
                    "code": plan.code,
                    "name": plan.name,
                    "duration": duration,
                    "new_price_pkr": new_price,
                }
            )

    audit(
        db,
        actor,
        "UPDATE_LOCKER_RATES",
        "plan",
        "LOCKER",
        {"base_daily_rate": req.base_daily_rate, "discount_factor": req.discount_factor},
    )
    db.commit()
    return {
        "status": "SUCCESS",
        "message": f"Locker plans updated using base rate {req.base_daily_rate} PKR/day",
        "updated_plans": updated_plans,
    }


@admin_router.get("/plans", response_model=list[PlanOut])
def admin_plans(db: Session = Depends(get_db)):
    return list(db.scalars(select(Plan).order_by(Plan.id)))


@admin_router.post("/plans", response_model=PlanOut, status_code=201)
def create_plan(body: PlanCreate, actor: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    if db.scalar(select(Plan).where(Plan.code == body.code)):
        raise HTTPException(409, "Plan code already exists")
    p = Plan(**body.model_dump())
    db.add(p)
    audit(db, actor, "CREATE_PLAN", "plan", body.code)
    db.commit()
    return p


@admin_router.patch("/plans/{plan_id}", response_model=PlanOut)
def update_plan(
    plan_id: int,
    body: PlanUpdate,
    actor: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    p = db.get(Plan, plan_id)
    if not p:
        raise HTTPException(404, "Plan not found")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(p, k, v)
    audit(db, actor, "UPDATE_PLAN", "plan", plan_id, changes)
    db.commit()
    return p


@admin_router.post("/payments/{payment_id}/refund", response_model=PaymentOut)
def refund_payment(
    payment_id: int,
    body: RefundRequest,
    actor: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    pay = db.get(Payment, payment_id)
    if not pay:
        raise HTTPException(404, "Payment not found")
    payments.refund(db, pay, body.amount_pkr, body.reason)
    audit(db, actor, "REFUND", "payment", payment_id, {"amount_pkr": body.amount_pkr, "reason": body.reason})
    db.commit()
    return pay

@admin_router.get("/alerts", response_model=list[AlertOut])
def list_alerts(open_only: bool = True, actor: User = Depends(require_admin), db: Session = Depends(get_db)):
    q = select(Alert).order_by(Alert.id.desc())
    target_station = scoped_station_id(actor)
    if target_station:
        q = q.where(Alert.station_id == target_station)
    if open_only:
        q = q.where(Alert.resolved.is_(False))
    return list(db.scalars(q.limit(200)))


@admin_router.post("/alerts/{alert_id}/ack", response_model=AlertOut)
def ack_alert(alert_id: int, actor: User = Depends(require_admin), db: Session = Depends(get_db)):
    a = db.get(Alert, alert_id)
    if not a:
        raise HTTPException(404, "Alert not found")
    scoped_station_id(actor, a.station_id)
    a.acknowledged = True
    audit(db, actor, "ACK_ALERT", "alert", alert_id)
    db.commit()
    return a


@admin_router.post("/alerts/{alert_id}/resolve", response_model=AlertOut)
def resolve_alert(alert_id: int, actor: User = Depends(require_admin), db: Session = Depends(get_db)):
    a = db.get(Alert, alert_id)
    if not a:
        raise HTTPException(404, "Alert not found")
    scoped_station_id(actor, a.station_id)
    a.resolved = a.acknowledged = True
    audit(db, actor, "RESOLVE_ALERT", "alert", alert_id)
    db.commit()
    return a


@admin_router.get("/vans", response_model=list[VanOut])
def list_vans(db: Session = Depends(get_db)):
    return list(db.scalars(select(Van).order_by(Van.id)))


@admin_router.post("/vans", response_model=VanOut, status_code=201)
def create_van(body: VanCreate, actor: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    if db.scalar(select(Van).where(Van.name == body.name)):
        raise HTTPException(409, "Van name already exists")
    v = Van(**body.model_dump())
    db.add(v)
    audit(db, actor, "CREATE_VAN", "van", body.name)
    db.commit()
    return v


@admin_router.patch("/vans/{van_id}", response_model=VanOut)
def update_van(
    van_id: int,
    body: VanUpdate,
    actor: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    v = db.get(Van, van_id)
    if not v:
        raise HTTPException(404, "Van not found")
    changes = body.model_dump(exclude_unset=True)
    for k, val in changes.items():
        setattr(v, k, val)
    audit(db, actor, "UPDATE_VAN", "van", van_id, changes)
    db.commit()
    return v


@admin_router.post("/rebalancing/plan", response_model=list[VanTaskOut])
def plan_rebalancing(
    body: PlanRouteRequest,
    actor: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    van = db.get(Van, body.van_id)
    if not van:
        raise HTTPException(404, "Van not found")
    tasks = fleet.generate_plan(db, van)
    audit(db, actor, "PLAN_ROUTE", "van", van.id, {"stops": len(tasks)})
    db.commit()
    return tasks


@admin_router.get("/rebalancing/tasks", response_model=list[VanTaskOut])
def list_tasks(status_: Optional[TaskStatus] = None, db: Session = Depends(get_db)):
    q = select(VanTask).order_by(VanTask.id.desc())
    if status_:
        q = q.where(VanTask.status == status_)
    return list(db.scalars(q.limit(200)))


@admin_router.get("/charging/plan")
def get_charging_plan(db: Session = Depends(get_db)):
    return fleet.charging_plan(db)


@admin_router.post("/weather")
def add_weather(body: WeatherIn, actor: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    w = WeatherObservation(ts=ensure_aware(body.ts) if body.ts else now_utc(), temp_c=body.temp_c, rain_mm=body.rain_mm)
    db.add(w)
    db.commit()
    return {"id": w.id, "ts": w.ts}


@admin_router.post("/forecast/train")
def train_forecast(
    body: TrainRequest = TrainRequest(),
    actor: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    try:
        mv = forecasting.train_and_register(db, prefer_xgb=body.prefer_xgboost)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    audit(db, actor, "TRAIN_FORECAST", "forecast_model", mv.id, mv.metrics)
    db.commit()
    return {
        "model_id": mv.id,
        "algorithm": mv.algorithm,
        "metrics": mv.metrics,
        "n_rows": mv.n_rows,
    }


@admin_router.post("/forecast/refresh")
def refresh_forecast(actor: User = Depends(require_super_admin), db: Session = Depends(get_db)):
    recs = forecasting.refresh_targets(db)
    audit(db, actor, "REFRESH_TARGETS", "station", "*", {"count": len(recs)})
    db.commit()
    return [
        {
            "station_id": r.station_id,
            "predicted_demand_12h": r.predicted_demand,
            "target_stock": r.target_stock,
        }
        for r in recs
    ]


@admin_router.get("/forecast/drift")
def get_drift(db: Session = Depends(get_db)):
    return forecasting.drift_check(db)


@admin_router.get("/forecast/latest")
def latest_forecast(actor: User = Depends(require_admin), db: Session = Depends(get_db)):
    latest: dict[str, ForecastRecord] = {}
    q = select(ForecastRecord).order_by(ForecastRecord.id.desc())
    target_station = scoped_station_id(actor)
    if target_station:
        q = q.where(ForecastRecord.station_id == target_station)
    for r in db.scalars(q.limit(100)):
        latest.setdefault(r.station_id, r)
    return [
        {
            "station_id": r.station_id,
            "horizon_hours": r.horizon_hours,
            "predicted_demand": r.predicted_demand,
            "target_stock": r.target_stock,
            "generated_at": r.generated_at,
        }
        for r in latest.values()
    ]


@admin_router.post("/pilot/simulate")
def simulate_pilot(body: PilotRequest = PilotRequest(), actor: User = Depends(require_super_admin)):
    return run_pilot(
        n_stations=body.n_stations,
        days=body.days,
        seed=body.seed,
        van_capacity=body.van_capacity,
    )


@admin_router.get("/audit", response_model=list[AuditOut])
def list_audit(
    limit: int = Query(100, ge=1, le=500),
    actor: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    return list(db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)))


@admin_router.get("/reports/summary")
def report_summary(actor: User = Depends(require_admin), db: Session = Depends(get_db)):
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


@admin_router.get("/reports/revenue")
def report_revenue(
    days: int = Query(30, ge=1, le=365),
    format: str = Query("json", pattern="^(json|csv)$"),
    actor: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
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


@admin_router.get("/reports/station-kpi")
def report_station_kpi(
    days: int = Query(7, ge=1, le=90),
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
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


@admin_router.get("/analytics/overview")
def get_analytics_overview(actor: User = Depends(require_admin), db: Session = Depends(get_db)):
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


@admin_router.patch("/users/{rider_id}/status")
def update_rider_status(
    rider_id: int,
    req: RiderStatusUpdate,
    actor: User = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, rider_id)
    if not user:
        raise HTTPException(404, "User not found")
    normalized = req.status.upper()
    if normalized in ("ACTIVE", "ENABLED", "TRUE"):
        user.is_active = True
    elif normalized in ("SUSPENDED", "DISABLED", "BANNED", "FALSE"):
        user.is_active = False
    else:
        raise HTTPException(422, "status must be ACTIVE or SUSPENDED")
    audit(db, actor, "SET_USER_STATUS", "user", rider_id, {"status": normalized, "reason": req.reason})
    db.commit()
    return {
        "rider_id": rider_id,
        "is_active": user.is_active,
        "new_status": "ACTIVE" if user.is_active else "SUSPENDED",
        "reason": req.reason,
    }


@admin_router.post("/orders/{order_id}/review")
def admin_review_order(
    order_id: int,
    req: OrderReviewRequest,
    actor: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    scoped_station_id(actor, order.station_id)
    action = req.action.upper()
    if action == "CANCEL" and order.status == OrderStatus.PAID:
        swap.release_order(db, order, OrderStatus.CANCELLED, refund=True, reason=req.reason or "admin review")
    elif action == "APPROVE" and order.status == OrderStatus.PENDING_PAYMENT:
        order.status = OrderStatus.PAID
        order.paid_at = now_utc()
    audit(db, actor, "REVIEW_ORDER", "order", order_id, {"action": action, "reason": req.reason})
    db.commit()
    return {"order_id": order.order_id, "action": action, "status": order.status.value, "reason": req.reason}


@admin_router.get("/finance/summary")
def get_finance_summary(actor: User = Depends(require_super_admin), db: Session = Depends(get_db)):
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


@admin_router.get("/delivery-zones")
def get_delivery_zones(actor: User = Depends(require_admin), db: Session = Depends(get_db)):
    cfg = get_pricing_config(db)
    target_station = scoped_station_id(actor)
    st_q = select(Station).where(Station.is_active.is_(True), Station.is_depot.is_(False))
    if target_station:
        st_q = st_q.where(Station.id == target_station)

    zones = []
    for st in db.scalars(st_q):
        zones.append(
            {
                "zone_id": f"ZONE-{st.id}",
                "station_id": st.id,
                "name": st.name,
                "latitude": st.latitude,
                "longitude": st.longitude,
                "radius_km": 25.0,
                "delivery_fee_pkr": cfg.delivery_cost_pkr,
                "is_active": st.is_active,
            }
        )
    return {"zones": zones}
# ####################################################################################################
# SECTION 16: API ROUTERS (DRIVER, TELEMETRY, SWAPS, SYSTEM & WEBSOCKETS)
# ####################################################################################################


# ====================================================================================================
# ROUTER 05: VAN DRIVER OPERATIONS (/api/driver/*)
# ====================================================================================================
driver_router = APIRouter(
    prefix="/api/driver", tags=["driver"], dependencies=[Depends(require_driver)]
)


def _my_van(db: Session, user: User) -> Van:
    van = db.scalar(select(Van).where(Van.driver_id == user.id, Van.is_active.is_(True)))
    if not van:
        raise HTTPException(404, "No active van is assigned to your account")
    return van


@driver_router.get("/me", response_model=VanOut)
def driver_van(user: User = Depends(require_driver), db: Session = Depends(get_db)):
    return _my_van(db, user)


@driver_router.get("/tasks", response_model=list[VanTaskOut])
def driver_tasks(user: User = Depends(require_driver), db: Session = Depends(get_db)):
    if user.is_admin:
        return list(
            db.scalars(
                select(VanTask)
                .where(VanTask.status == TaskStatus.PENDING)
                .order_by(VanTask.seq, VanTask.id)
            )
        )
    van = _my_van(db, user)
    return list(
        db.scalars(
            select(VanTask)
            .where(VanTask.van_id == van.id, VanTask.status == TaskStatus.PENDING)
            .order_by(VanTask.seq, VanTask.id)
        )
    )


@driver_router.post("/location", response_model=VanOut)
def update_location(
    body: LocationUpdate, user: User = Depends(require_driver), db: Session = Depends(get_db)
):
    van = _my_van(db, user)
    van.latitude, van.longitude = body.latitude, body.longitude
    db.commit()
    db.refresh(van)
    return van


@driver_router.post("/tasks/{task_id}/confirm", response_model=VanTaskOut)
def confirm_driver_task(
    task_id: int,
    body: TaskConfirm = TaskConfirm(),
    user: User = Depends(require_driver),
    db: Session = Depends(get_db),
):
    task = db.get(VanTask, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return fleet.confirm_task(
        db,
        task,
        user,
        delivery_otp=body.delivery_otp,
        confirmed_drop=body.confirmed_drop,
        confirmed_pickup=body.confirmed_pickup,
        returned_pack_id=body.returned_pack_id,
    )


@driver_router.post("/return-to-depot")
def driver_return_to_depot(user: User = Depends(require_driver), db: Session = Depends(get_db)):
    van = _my_van(db, user)
    unloaded = fleet.return_to_depot(db, van)
    return {"van_id": van.id, "unloaded_packs": unloaded, "depot_station_id": van.depot_station_id}


@driver_router.post("/orders/handover")
def complete_delivery_handover(
    handover: DeliveryHandoverOTP,
    user: User = Depends(require_driver),
    db: Session = Depends(get_db),
):
    """Verify rider delivery OTP at doorstep, record any COD collection, and finalize the swap order."""
    raw_id = handover.order_id.strip().upper()
    numeric_id = int(raw_id.replace("ORD-", "")) if raw_id.startswith("ORD-") else int(raw_id)

    order = db.get(Order, numeric_id)
    if not order:
        raise HTTPException(404, f"Order {handover.order_id} not found")
    if order.mode != OrderMode.HOME:
        raise HTTPException(400, "Handover OTP applies only to HOME delivery orders")
    if order.status not in OPEN_ORDER:
        raise HTTPException(409, f"Order is already {order.status.value}")
    if not order.delivery_otp or not secrets.compare_digest(
        handover.otp_code.strip(), order.delivery_otp
    ):
        raise HTTPException(403, "Invalid rider delivery OTP")

    task = db.scalar(
        select(VanTask).where(
            VanTask.order_id == order.id,
            VanTask.kind == TaskKind.DELIVERY,
            VanTask.status == TaskStatus.PENDING,
        )
    )
    if task:
        fleet.confirm_task(
            db,
            task,
            user,
            delivery_otp=handover.otp_code.strip(),
            returned_pack_id=handover.returned_pack_id,
        )
    else:
        swap.complete_order(db, order, handover.returned_pack_id)

    audit(
        db,
        user,
        "DELIVERY_HANDOVER",
        "order",
        order.id,
        {"cash_collected_pkr": handover.cash_collected_amount, "returned_pack_id": handover.returned_pack_id},
    )
    db.commit()
    return {
        "order_id": order.order_id,
        "status": "DELIVERED",
        "cash_collected_pkr": handover.cash_collected_amount,
        "receipt_no": order.receipt_no,
        "message": "Battery handover verified and swap order completed.",
    }


# ====================================================================================================
# ROUTER 06: BMS TELEMETRY INGESTION & LIVE SNAPSHOTS (/api/telemetry/*)
# ====================================================================================================
telemetry_router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])


@telemetry_router.post("/ingest")
def ingest_telemetry(
    body: TelemetryIn,
    _: str = Depends(require_device_or_admin),
    db: Session = Depends(get_db),
):
    try:
        res = ingest_reading(db, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return {
        "pack_id": res.pack_id,
        "duplicate": res.duplicate,
        "trip_relay": res.trip_relay,
        "lockout": res.lockout,
        "health_score": res.health_score,
    }


@telemetry_router.get("/latest")
def get_all_latest_telemetry(db: Session = Depends(get_db)):
    """Return latest telemetry snapshot for all registered packs (used by live dashboard cards)."""
    packs = list(db.scalars(select(Battery).order_by(Battery.id).limit(200)))
    return {
        "count": len(packs),
        "packs": [
            {
                "pack_id": b.id,
                "station_id": b.station_id,
                "slot_id": b.locker_id,
                "state": b.state.value,
                "voltage": b.voltage,
                "current_a": b.current_a,
                "temperature_c": b.temperature_c,
                "soc": b.soc,
                "soh": b.soh,
                "cycle_count": b.cycle_count,
                "health_score": b.health_score,
                "health_status": b.health_status.value,
                "last_seen_at": b.last_seen_at.isoformat() if b.last_seen_at else None,
            }
            for b in packs
        ],
    }


@telemetry_router.get("/{pack_id}/latest", response_model=TelemetryOut)
def latest_telemetry(pack_id: str, db: Session = Depends(get_db)):
    row = db.scalar(
        select(TelemetryReading)
        .where(TelemetryReading.pack_id == pack_id)
        .order_by(TelemetryReading.ts.desc())
    )
    if not row:
        raise HTTPException(404, "No telemetry recorded for this pack")
    return row


# ====================================================================================================
# ROUTER 07: LEGACY / DIRECT SWAP COMPATIBILITY ROUTES (/api/swaps/*)
# ====================================================================================================
swap_router = APIRouter(prefix="/api/swaps", tags=["swaps"])


@swap_router.post("/request")
def request_battery_swap(
    req: SwapRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    order = swap.create_order(
        db,
        user=user,
        req=OrderCreate(
            station_id=req.station_id,
            mode=OrderMode.STATION,
            use_subscription=True,
        ),
    )
    return {
        "swap_id": order.order_id,
        "id": order.id,
        "station_id": order.station_id,
        "assigned_battery_id": order.pack_out_id,
        "slot_id": order.slot_id,
        "pickup_code": order.pickup_code,
        "status": order.status.value,
    }


@swap_router.post("/complete")
def complete_physical_swap(
    data: PhysicalSwapReturn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    open_order = db.scalar(
        select(Order)
        .where(
            Order.user_id == user.id,
            Order.station_id == data.station_id,
            Order.status.in_(OPEN_ORDER),
        )
        .order_by(Order.id.desc())
    )
    if not open_order:
        raise HTTPException(404, "No active swap order found for this rider at this station")

    returned_pack = db.get(Battery, data.returned_battery_id)
    if returned_pack:
        returned_pack.soc = data.returned_soc
        returned_pack.soh = data.returned_soh
        returned_pack.remaining_ah = data.returned_soc / 100.0 * returned_pack.full_charge_capacity_ah
        returned_pack.full_charge_capacity_ah = data.returned_soh / 100.0 * returned_pack.rated_capacity_ah

    completed = swap.complete_order(db, open_order, data.returned_battery_id)
    return {
        "status": "COMPLETED",
        "order_id": completed.order_id,
        "receipt_no": completed.receipt_no,
        "returned_pack": {
            "id": data.returned_battery_id,
            "soc": data.returned_soc,
            "soh": data.returned_soh,
        },
        "points_balance": user.points,
    }


@swap_router.get("/history/{rider_id}")
def get_rider_swap_history(
    rider_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target_id = int(rider_id) if rider_id.isdigit() else user.id
    if target_id != user.id and not user.is_admin:
        raise HTTPException(403, "Cannot inspect another rider's swap history")
    orders = list(
        db.scalars(
            select(Order)
            .where(Order.user_id == target_id)
            .order_by(Order.id.desc())
            .limit(100)
        )
    )
    return {
        "rider_id": target_id,
        "history": [
            {
                "order_id": o.order_id,
                "station_id": o.station_id,
                "mode": o.mode.value,
                "status": o.status.value,
                "pack_out_id": o.pack_out_id,
                "pack_in_id": o.pack_in_id,
                "total_pkr": o.total_pkr,
                "receipt_no": o.receipt_no,
                "created_at": o.created_at.isoformat(),
                "completed_at": o.completed_at.isoformat() if o.completed_at else None,
            }
            for o in orders
        ],
    }


# ====================================================================================================
# ROUTER 08: SYSTEM HEALTH, PROMETHEUS METRICS & PAYMENT WEBHOOK
# ====================================================================================================
system_router = APIRouter(tags=["system"])


@system_router.get("/health/live")
def health_live():
    return {"status": "ok", "time": now_utc().isoformat()}


@system_router.get("/health/ready")
def health_ready(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(503, f"Database unavailable: {exc.__class__.__name__}")
    return {"status": "ready"}


@system_router.get("/metrics")
def prometheus_metrics():
    return Response(generate_latest(METRICS_REGISTRY), media_type=CONTENT_TYPE_LATEST)


@system_router.post("/api/payments/webhook")
async def payment_webhook(
    request: Request,
    x_signature: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    raw_body = await request.body()
    if not payments.verify_webhook(raw_body, x_signature):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook signature")

    payload = PaymentWebhook.model_validate_json(raw_body)
    pay = db.scalar(select(Payment).where(Payment.reference == payload.reference))
    if not pay:
        raise HTTPException(404, "Payment reference not found")

    if payload.provider_ref:
        pay.provider_ref = payload.provider_ref
    outcome = payload.status.upper()
    if outcome == "SUCCEEDED" and pay.status == PaymentStatus.PENDING:
        pay.status = PaymentStatus.SUCCEEDED
        if pay.order_id:
            order = db.get(Order, pay.order_id)
            if order and order.status == OrderStatus.PENDING_PAYMENT:
                order.status = OrderStatus.PAID
                order.paid_at = now_utc()
    elif outcome == "FAILED" and pay.status == PaymentStatus.PENDING:
        pay.status = PaymentStatus.FAILED
        if pay.order_id:
            order = db.get(Order, pay.order_id)
            if order and order.status == OrderStatus.PENDING_PAYMENT:
                swap.release_order(db, order, OrderStatus.FAILED, refund=False)

    db.commit()
    return {"received": True, "reference": pay.reference, "status": pay.status.value}


# ====================================================================================================
# ROUTER 09: REAL-TIME WEBSOCKET STREAMS (/ws/telemetry/{pack_id}, /ws/alerts)
# ====================================================================================================
ws_router = APIRouter(tags=["websockets"])


def _ws_device_authorized(ws: WebSocket) -> bool:
    s = get_settings()
    key = ws.headers.get("x-device-key") or ws.query_params.get("device_key")
    if key and secrets.compare_digest(key, s.device_api_key):
        return True
    auth_header = ws.headers.get("authorization", "")
    token = auth_header[7:] if auth_header.lower().startswith("bearer ") else ws.query_params.get("token")
    if token:
        with SessionLocal() as db:
            try:
                user = user_from_token(db, token)
                return user.is_admin
            except HTTPException:
                return False
    return False


@ws_router.websocket("/ws/telemetry/{pack_id}")
async def ws_telemetry(ws: WebSocket, pack_id: str):
    await ws.accept()
    topic = f"pack:{pack_id}"
    queue = hub.subscribe(topic)
    can_publish = _ws_device_authorized(ws)
    heartbeat_s = get_settings().ws_heartbeat_s

    try:
        # Send immediate initial state frame if pack already exists
        with SessionLocal() as db:
            pack = db.get(Battery, pack_id)
            if pack:
                await ws.send_json(to_wire(telemetry_frame(pack)))

        async def _sender():
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
                    await ws.send_json(msg)
                except asyncio.TimeoutError:
                    await ws.send_json({"type": "PING", "pack_id": pack_id, "timestamp": now_utc().isoformat()})

        async def _receiver():
            while True:
                raw_msg = await ws.receive_text()
                if not can_publish:
                    await ws.send_json({"type": "ERROR", "detail": "Read-only stream; provide device_key to ingest"})
                    continue
                try:
                    data = json.loads(raw_msg)
                    if data.get("type") == "PING":
                        await ws.send_json({"type": "PONG", "timestamp": now_utc().isoformat()})
                        continue
                    with SessionLocal() as db:
                        reading = merge_partial(db, pack_id, data)
                        res = ingest_reading(db, reading)
                    await ws.send_json(
                        to_wire(
                            {
                                "type": "ACK",
                                "pack_id": res.pack_id,
                                "duplicate": res.duplicate,
                                "trip_relay": res.trip_relay,
                                "lockout": res.lockout,
                            }
                        )
                    )
                except Exception as exc:
                    await ws.send_json({"type": "ERROR", "detail": str(exc)[:200]})

        sender_task = asyncio.create_task(_sender())
        receiver_task = asyncio.create_task(_receiver())
        done, pending = await asyncio.wait(
            {sender_task, receiver_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(topic, queue)
        if ws.client_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await ws.close()


@ws_router.websocket("/ws/alerts")
async def ws_alerts(ws: WebSocket):
    auth_header = ws.headers.get("authorization", "")
    token = auth_header[7:] if auth_header.lower().startswith("bearer ") else ws.query_params.get("token")
    if not token:
        await ws.close(code=1008, reason="Admin token required")
        return

    with SessionLocal() as db:
        try:
            user = user_from_token(db, token)
            if not user.is_admin:
                await ws.close(code=1008, reason="Admin role required")
                return
        except HTTPException:
            await ws.close(code=1008, reason="Invalid token")
            return

    await ws.accept()
    queue = hub.subscribe("alerts")
    heartbeat_s = get_settings().ws_heartbeat_s
    try:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
                await ws.send_json(msg)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "PING", "timestamp": now_utc().isoformat()})
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe("alerts", queue)
        if ws.client_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await ws.close()


# ####################################################################################################
# SECTION 17: BACKGROUND SWEEPER, LIFESPAN, MIDDLEWARE & FASTAPI APP INITIALIZATION
# ####################################################################################################
log_app = logging.getLogger("voltora.app")


async def _sweeper() -> None:
    interval = max(10, get_settings().sweeper_interval_s)
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(sweep)
        except Exception:
            log_app.exception("Background sweep failed")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    hub.bind_loop(asyncio.get_running_loop())
    init_db()

    with SessionLocal() as db:
        seed_defaults(db)
        bootstrap_admin(db)
        if s.seed_demo_data:
            seed_demo(db)

    mqtt_ingestor: Optional[MqttIngestor] = None
    if s.mqtt_enabled:
        try:
            mqtt_ingestor = MqttIngestor()
            mqtt_ingestor.start()
        except Exception:
            log_app.exception("Failed to start MQTT ingestor")

    sweeper_task = asyncio.create_task(_sweeper())
    log_app.info("Voltora Backend ready (env=%s)", s.env)
    try:
        yield
    finally:
        sweeper_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweeper_task
        if mqtt_ingestor:
            mqtt_ingestor.stop()


app = FastAPI(
    title="Voltora EV Battery Swapping Network API",
    version="2.0.0",
    default_response_class=DualKeyJSONResponse,
    lifespan=lifespan,
)

_origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    path_label = route.path if route else request.url.path
    if path_label != "/metrics":
        elapsed = time.perf_counter() - start
        HTTP_REQUESTS.labels(request.method, path_label, str(response.status_code)).inc()
        HTTP_LATENCY.labels(request.method, path_label).observe(elapsed)
    return response


# Register all routers cleanly without shadowing
app.include_router(auth_router)
app.include_router(rider_router)
app.include_router(guest_router)
app.include_router(admin_router)
app.include_router(driver_router)
app.include_router(telemetry_router)
app.include_router(swap_router)
app.include_router(system_router)
app.include_router(ws_router)
if subscription_router is not None:
    app.include_router(subscription_router)


# ####################################################################################################
# SECTION 18: CLI & SERVER ENTRYPOINT
# ####################################################################################################
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if len(sys.argv) >= 2 and sys.argv[1] == "job":
        if len(sys.argv) < 3 or sys.argv[2] not in JOBS:
            print(f"Usage: python main.py job <{'|'.join(JOBS)}>")
            sys.exit(1)
        init_db()
        print(json.dumps(JOBS[sys.argv[2]](), default=str, indent=2))
    elif len(sys.argv) >= 2 and sys.argv[1] == "mqtt":
        init_db()
        ingestor = MqttIngestor()
        ingestor.start()
        print("MQTT ingestor listening (Ctrl+C to stop)...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            ingestor.stop()
    else:
        import uvicorn

        uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=not get_settings().is_prod)