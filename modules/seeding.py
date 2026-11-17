"""Business logic: seeding."""

from __future__ import annotations

import logging
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import get_settings
from constants.enums import BatteryState, PlanKind, Role
from constants.seed import DEFAULT_PLANS, DEMO_STATIONS, RETIRED_PLAN_CODES
from models import AppSetting, Battery, Locker, Plan, Station, User, Van
from modules.lockers import refresh_status
from modules.sites import ensure_lockers
from modules.pricing import calculate_locker_plan_price, PricingConfig
from utils.security import hash_password, normalize_phone

log_bootstrap = logging.getLogger("voltora.bootstrap")


def seed_defaults(db: Session) -> None:
    if not db.get(AppSetting, "pricing"):
        db.add(AppSetting(key="pricing", value=PricingConfig().model_dump(mode="json")))
    for code, name, kind, swaps, price in DEFAULT_PLANS:
        if not db.scalar(select(Plan).where(Plan.code == code)):
            db.add(Plan(code=code, name=name, kind=kind, swaps_included=swaps, price_pkr=price))
    for p in db.scalars(select(Plan).where(Plan.code.in_(RETIRED_PLAN_CODES))):
        p.is_active = False  # keep rows: existing subscriptions reference them
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
    monthly_rate = 1000.0 / 30  # Rs 1,000 a month, so 6 months = 6,000 and a year = 12,000
    locker_plans = [
        {
            "code": "LOCKER_MO",
            "name": "Monthly Locker Pass",
            "kind": PlanKind.LOCKER,
            "duration_type": "monthly",
            "duration_days": 30,
            "swaps_included": 0,
            "price_pkr": calculate_locker_plan_price("monthly", base_daily_rate=monthly_rate),
            "is_active": True,
        },
        {
            "code": "LOCKER_6M",
            "name": "6-Month Locker Pass",
            "kind": PlanKind.LOCKER,
            "duration_type": "half_yearly",
            "duration_days": 180,
            "swaps_included": 0,
            "price_pkr": calculate_locker_plan_price("half_yearly", base_daily_rate=monthly_rate),
            "is_active": True,
        },
        {
            "code": "LOCKER_YR",
            "name": "Yearly Locker Pass",
            "kind": PlanKind.LOCKER,
            "duration_type": "yearly",
            "duration_days": 365,
            "swaps_included": 0,
            "price_pkr": calculate_locker_plan_price("yearly", base_daily_rate=monthly_rate),
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

    db.flush()
    for st in db.scalars(select(Station)):
        ensure_lockers(db, st)
    van1 = db.scalar(select(Van).where(Van.name == "Van-1"))
    driver = db.scalar(select(User).where(User.username == "driver1"))
    if van1 and van1.driver_id is None and driver:
        van1.driver_id = driver.id  # older databases created the van before the driver account existed
    if not van1:
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
