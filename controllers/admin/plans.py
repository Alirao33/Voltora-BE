"""Thin controllers (HTTP in, module call, HTTP out): plans."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import plans as plans_service
from schemas.plan import PlanCreate, PlanUpdate

def admin_plans(db: Session = Depends(get_db)):
    return plans_service.admin_plans(db=db)


def create_plan(body: PlanCreate, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return plans_service.create_plan(body=body, actor=actor, db=db)


def update_plan(plan_id: int, body: PlanUpdate, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return plans_service.update_plan(plan_id=plan_id, body=body, actor=actor, db=db)
