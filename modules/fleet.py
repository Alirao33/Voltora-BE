"""Business logic: fleet."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from datetime import timedelta
from typing import Any, Optional

from constants.enums import BatteryState, OrderStatus, TaskKind, TaskStatus
from models import Battery, Order, Station, User, Van, VanTask
from modules.audit import audit
from modules.orders import complete_order
from modules.routing import plan_route, priority, StationState
from modules.stations import check_stock_alert, station_stock
from schemas.van import LocationUpdate, PlanRouteRequest, TaskConfirm, TaskStage, VanCreate, VanUpdate
from utils.access import scoped_station_id
from utils.dates import now_utc

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
        if (order.delivery_stage or "") != "EN_ROUTE":
            raise HTTPException(409, "Mark the order Picked Up and En Route before handing over")
        if not delivery_otp or delivery_otp.strip() != (order.delivery_otp or ""):
            raise HTTPException(403, "Invalid delivery OTP")
        order.status = OrderStatus.IN_PROGRESS
        task.status, task.confirmed_at, task.confirmed_drop = TaskStatus.DONE, now_utc(), 1
        db.flush()
        complete_order(db, order, returned_pack_id)
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


CHARGER_KW = 3.0  # planning assumption: one slot charger delivers 3 kW; windows are estimates, not measurements


def charging_plan(db: Session, actor: Optional[User] = None) -> list[dict[str, Any]]:
    """Charging windows for depleted/charging packs: high-shortage stations first, then lowest SoC.
    Each pack sits in its own slot charger, so every window starts now and ends when the missing energy is in."""
    pri = {s.id: priority(s.target, s.current) for s in station_states(db)}
    rows = []
    now = now_utc()
    q = select(Battery).where(Battery.state.in_((BatteryState.DEPLETED, BatteryState.CHARGING)))
    sid = scoped_station_id(actor) if actor else None
    if sid:
        q = q.where(Battery.station_id == sid)
    for b in db.scalars(q):
        need_kwh = max(0.0, (100.0 - b.soc) / 100.0 * b.full_charge_capacity_ah * b.nominal_voltage / 1000.0)
        hours = need_kwh / CHARGER_KW
        rows.append(
            {
                "pack_id": b.id,
                "station_id": b.station_id,
                "state": b.state,
                "soc": b.soc,
                "health_score": b.health_score,
                "station_priority": round(pri.get(b.station_id, 0.0), 4),
                "supply_voltage_v": b.supply_voltage_v,
                "energy_needed_kwh": round(need_kwh, 2),
                "window_start": now,
                "window_end": now + timedelta(hours=hours),
                "charger_kw_assumed": CHARGER_KW,
            }
        )
    rows.sort(key=lambda r: (-r["station_priority"], r["soc"], r["pack_id"]))
    return rows


def list_vans(db: Session):
    return list(db.scalars(select(Van).order_by(Van.id)))


def create_van(body: VanCreate, actor: User, db: Session):
    if db.scalar(select(Van).where(Van.name == body.name)):
        raise HTTPException(409, "Van name already exists")
    v = Van(**body.model_dump())
    db.add(v)
    audit(db, actor, "CREATE_VAN", "van", body.name)
    db.commit()
    return v


def update_van(van_id: int, body: VanUpdate, actor: User, db: Session):
    v = db.get(Van, van_id)
    if not v:
        raise HTTPException(404, "Van not found")
    changes = body.model_dump(exclude_unset=True)
    for k, val in changes.items():
        setattr(v, k, val)
    audit(db, actor, "UPDATE_VAN", "van", van_id, changes)
    db.commit()
    return v


def plan_rebalancing(body: PlanRouteRequest, actor: User, db: Session):
    van = db.get(Van, body.van_id)
    if not van:
        raise HTTPException(404, "Van not found")
    tasks = generate_plan(db, van)
    audit(db, actor, "PLAN_ROUTE", "van", van.id, {"stops": len(tasks)})
    db.commit()
    return tasks


def list_tasks(status_: Optional[TaskStatus], db: Session, van_id: Optional[int] = None):
    q = select(VanTask).order_by(VanTask.id.desc())
    if status_:
        q = q.where(VanTask.status == status_)
    if van_id is not None:
        q = q.where(VanTask.van_id == van_id)
    return list(db.scalars(q.limit(200)))


def get_charging_plan(actor: User, db: Session):
    return charging_plan(db, actor)


def recompute_charging_plan(actor: User, db: Session):
    """Rebuild the windows from the packs' current SoC and log who asked."""
    rows = charging_plan(db, actor)
    audit(db, actor, "RECOMPUTE_CHARGING_PLAN", "station", scoped_station_id(actor) or "*", {"packs": len(rows)})
    db.commit()
    return rows


def _my_van(db: Session, user: User) -> Van:
    van = db.scalar(select(Van).where(Van.driver_id == user.id, Van.is_active.is_(True)))
    if not van:
        raise HTTPException(404, "No active van is assigned to your account")
    return van


def driver_van(user: User, db: Session):
    return _my_van(db, user)


def driver_tasks(user: User, db: Session, include_done: bool = False):
    if include_done and not user.is_admin:
        # history: every task of this driver's van, newest first
        van = _my_van(db, user)
        return list(db.scalars(select(VanTask).where(VanTask.van_id == van.id).order_by(VanTask.id.desc()).limit(200)))
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


def update_location(body: LocationUpdate, user: User, db: Session):
    van = _my_van(db, user)
    van.latitude, van.longitude = body.latitude, body.longitude
    db.commit()
    db.refresh(van)
    return van


def confirm_driver_task(task_id: int, body: TaskConfirm, user: User, db: Session):
    task = db.get(VanTask, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return confirm_task(
        db,
        task,
        user,
        delivery_otp=body.delivery_otp,
        confirmed_drop=body.confirmed_drop,
        confirmed_pickup=body.confirmed_pickup,
        returned_pack_id=body.returned_pack_id,
    )


def set_task_stage(task_id: int, body: TaskStage, user: User, db: Session):
    """Driver moves a home delivery on: Accepted -> Picked Up from Locker -> En Route (Delivered is the OTP hand-over)."""
    task = db.get(VanTask, task_id)
    if not task or task.kind != TaskKind.DELIVERY or task.status != TaskStatus.PENDING:
        raise HTTPException(404, "Delivery task not found")
    _check_van(task, user, db)
    order = db.get(Order, task.order_id) if task.order_id else None
    if not order or order.status not in (OrderStatus.PAID, OrderStatus.IN_PROGRESS):
        raise HTTPException(409, "Order is not open")
    if body.stage == "PICKED_UP":
        if order.delivery_stage:
            raise HTTPException(409, f"Order is already {order.delivery_stage}")
        if not order.locker_code_shared:
            raise HTTPException(409, "Get the locker code from the rider before picking up the pack")
    elif order.delivery_stage != "PICKED_UP":
        raise HTTPException(409, "Mark the pack Picked Up first")
    order.delivery_stage = body.stage
    audit(db, user, "DELIVERY_" + body.stage, "order", order.id, {"station_id": order.station_id}, station_id=order.station_id)
    db.commit()
    db.refresh(task)
    return task


def driver_return_to_depot(user: User, db: Session):
    van = _my_van(db, user)
    unloaded = return_to_depot(db, van)
    return {"van_id": van.id, "unloaded_packs": unloaded, "depot_station_id": van.depot_station_id}


def assign_task(task_id: int, van_id: int, actor: User, db: Session):
    """Move a pending task to another active van."""
    task = db.get(VanTask, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.status != TaskStatus.PENDING:
        raise HTTPException(409, f"Task is {task.status.value}")
    van = db.get(Van, van_id)
    if not van or not van.is_active:
        raise HTTPException(404, "Van not found or inactive")
    task.van_id = van.id
    audit(db, actor, "ASSIGN_VAN_TASK", "van_task", task_id, {"van_id": van.id})
    db.commit()
    return task
