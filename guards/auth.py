"""Route guards: auth."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordBearer
from sqlalchemy.orm import Session
from typing import Optional

from config.database import get_db
from constants.enums import Role
from models import User
from modules.auth import user_from_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


oauth2_optional = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)


bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Paste the access_token here (admins log in via two-step 2FA, so use this header).",
)


def pick_token(oauth: Optional[str], bearer: Optional[HTTPAuthorizationCredentials]) -> Optional[str]:
    return oauth or (bearer.credentials if bearer else None)


def get_current_user(
    oauth: Optional[str] = Depends(oauth2_optional),
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    token = pick_token(oauth, bearer)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", {"WWW-Authenticate": "Bearer"})
    return user_from_token(db, token)


def require_roles(*roles: Role):
    def dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires role: {', '.join(r.value for r in roles)}",
            )
        return user
    return dep


require_admin = require_roles(Role.ADMIN, Role.STATION_ADMIN)


require_super_admin = require_roles(Role.ADMIN)


require_driver = require_roles(Role.DRIVER, Role.ADMIN)
