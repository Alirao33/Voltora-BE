"""Business logic: plans."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Plan, User
from modules.audit import audit
from schemas.plan import PlanCreate, PlanUpdate

def admin_plans(db: Session):
    return list(db.scalars(select(Plan).order_by(Plan.id)))


def create_plan(body: PlanCreate, actor: User, db: Session):
    if db.scalar(select(Plan).where(Plan.code == body.code)):
        raise HTTPException(409, "Plan code already exists")
    p = Plan(**body.model_dump())
    db.add(p)
    audit(db, actor, "CREATE_PLAN", "plan", body.code)
    db.commit()
    return p


def update_plan(plan_id: int, body: PlanUpdate, actor: User, db: Session):
    p = db.get(Plan, plan_id)
    if not p:
        raise HTTPException(404, "Plan not found")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(p, k, v)
    audit(db, actor, "UPDATE_PLAN", "plan", plan_id, changes)
    db.commit()
    return p
