"""Business logic: pricing changes need the super admin's approval (the price calculator never does)."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import PricingChange, User
from modules.audit import audit
from modules.pricing import PricingConfig, get_pricing_config, set_pricing
from utils.dates import now_utc


def _diff(cur: dict, new: dict) -> list[dict]:
    return [{"field": k, "from": cur.get(k), "to": v} for k, v in new.items() if cur.get(k) != v]


def _out(c: PricingChange) -> dict:
    return {
        "id": c.id,
        "status": c.status,
        "requested_by": c.requested_by,
        "requested_by_name": c.requested_by_name,
        "station_id": c.station_id,
        "created_at": c.created_at,
        "decided_by_name": c.decided_by_name,
        "decided_at": c.decided_at,
        "decision_note": c.decision_note,
        "diff": _diff(c.current, c.proposed),
    }


def submit_pricing(body: PricingConfig, actor: User, db: Session):
    """Super admin: applied at once. Station admin: parked as a request until the super admin decides."""
    cur = get_pricing_config(db).model_dump(mode="json")
    new = body.model_dump(mode="json")
    if cur == new:
        raise HTTPException(400, "Nothing has changed")
    if actor.is_super_admin:
        set_pricing(body, actor, db)
        return {"status": "APPLIED"}
    for old in db.scalars(select(PricingChange).where(PricingChange.requested_by == actor.id, PricingChange.status == "PENDING")):
        old.status = "SUPERSEDED"  # a new request replaces the one still waiting
    c = PricingChange(
        requested_by=actor.id,
        requested_by_name=actor.full_name or actor.username or f"user {actor.id}",
        station_id=actor.station_id,
        proposed=new,
        current=cur,
    )
    db.add(c)
    db.flush()
    audit(db, actor, "REQUEST_PRICING_CHANGE", "pricing_change", c.id, {"changes": len(_diff(cur, new))})
    db.commit()
    return {"status": "PENDING_APPROVAL", "request": _out(c)}


def list_changes(actor: User, db: Session):
    q = select(PricingChange).order_by(PricingChange.id.desc()).limit(50)
    if not actor.is_super_admin:
        q = q.where(PricingChange.requested_by == actor.id)
    return [_out(c) for c in db.scalars(q)]


def _pending(db: Session, change_id: int) -> PricingChange:
    c = db.get(PricingChange, change_id)
    if not c:
        raise HTTPException(404, "Request not found")
    if c.status != "PENDING":
        raise HTTPException(409, f"This request is already {c.status.lower()}")
    return c


def decide_change(change_id: int, approve: bool, note: str | None, actor: User, db: Session):
    """Super admin only (enforced on the route)."""
    c = _pending(db, change_id)
    if approve:
        set_pricing(PricingConfig(**c.proposed), actor, db)
    c.status = "APPROVED" if approve else "REJECTED"
    c.decided_by_name = actor.full_name or actor.username
    c.decided_at = now_utc()
    c.decision_note = (note or "").strip()[:200] or None
    audit(db, actor, "APPROVE_PRICING_CHANGE" if approve else "REJECT_PRICING_CHANGE", "pricing_change", c.id, {"requested_by": c.requested_by_name})
    db.commit()
    return _out(c)


def cancel_change(change_id: int, actor: User, db: Session):
    c = _pending(db, change_id)
    if c.requested_by != actor.id and not actor.is_super_admin:
        raise HTTPException(404, "Request not found")
    c.status = "CANCELLED"
    audit(db, actor, "CANCEL_PRICING_CHANGE", "pricing_change", c.id, None)
    db.commit()
    return _out(c)
