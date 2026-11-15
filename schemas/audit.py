"""Pydantic schemas: audit."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel
from typing import Optional

from schemas.base import ORM

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
