"""Business logic: audit."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session
from typing import Any, Optional

from models import AuditLog, User

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


def list_audit(limit: int, actor: User, db: Session):
    return list(db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)))
