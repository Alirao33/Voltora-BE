"""Route guards: device."""

from __future__ import annotations

import secrets
from fastapi import Depends, Header, HTTPException, status, WebSocket
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from typing import Optional

from config.database import get_db, SessionLocal
from config.settings import get_settings
from constants.roles import ADMIN_ROLES
from guards.auth import bearer_scheme, oauth2_optional, pick_token
from modules.auth import user_from_token

def require_device_or_admin(
    x_device_key: Optional[str] = Header(None),
    oauth: Optional[str] = Depends(oauth2_optional),
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> str:
    """Devices/ingestion service authenticate with X-Device-Key; human operators require an Admin JWT."""
    if x_device_key and secrets.compare_digest(x_device_key, get_settings().device_api_key):
        return "device"
    token = pick_token(oauth, bearer)
    if token:
        user = user_from_token(db, token)
        if user.role in ADMIN_ROLES:
            return f"admin:{user.id}"
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Device key or admin token required")


def ws_device_authorized(ws: WebSocket) -> bool:
    s = get_settings()
    key = ws.headers.get("x-device-key") or ws.query_params.get("device_key")
    if key and secrets.compare_digest(key, s.device_api_key):
        return True
    auth_header = ws.headers.get("authorization", "")
    token = auth_header[7:] if auth_header.lower().startswith("bearer ") else ws.query_params.get("token")
    if token:
        with SessionLocal() as db:
            try:
                user = user_from_token(db, token)
                return user.is_admin
            except HTTPException:
                return False
    return False
