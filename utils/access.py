"""Shared helpers: access."""

from __future__ import annotations

from fastapi import HTTPException, status
from typing import Optional

from constants.enums import Role
from models import User

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
