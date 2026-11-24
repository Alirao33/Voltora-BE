"""Thin controllers (HTTP in, module call, HTTP out): vans."""

from __future__ import annotations

from fastapi import Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from config.database import get_db
from constants.enums import TaskStatus
from guards.auth import get_current_user
from models import User
from modules import fleet as fleet_service
from schemas.van import PlanRouteRequest, VanCreate, VanUpdate

def list_vans(db: Session = Depends(get_db)):
    return fleet_service.list_vans(db=db)


def create_van(body: VanCreate, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return fleet_service.create_van(body=body, actor=actor, db=db)


def update_van(van_id: int, body: VanUpdate, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return fleet_service.update_van(van_id=van_id, body=body, actor=actor, db=db)


def plan_rebalancing(body: PlanRouteRequest, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return fleet_service.plan_rebalancing(body=body, actor=actor, db=db)


def list_tasks(status_: Optional[TaskStatus] = Query(None, alias="status"), van_id: Optional[int] = None, db: Session = Depends(get_db)):
    return fleet_service.list_tasks(status_=status_, db=db, van_id=van_id)


def assign_task(task_id: int, van_id: int, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return fleet_service.assign_task(task_id=task_id, van_id=van_id, actor=actor, db=db)


def get_charging_plan(actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return fleet_service.get_charging_plan(actor=actor, db=db)


def recompute_charging_plan(actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return fleet_service.recompute_charging_plan(actor=actor, db=db)
