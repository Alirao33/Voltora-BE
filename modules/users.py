"""Business logic: users."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from constants.enums import Role
from models import Station, User
from modules.audit import audit
from schemas.auth import RiderStatusUpdate, UserAdminUpdate

def me(user: User):
    return user


def list_users(db: Session):
    return list(db.scalars(select(User).order_by(User.id)))


def update_user(user_id: int, body: UserAdminUpdate, actor: User, db: Session):
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


def update_rider_status(rider_id: int, req: RiderStatusUpdate, actor: User, db: Session):
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
