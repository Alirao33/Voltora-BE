"""Route definitions: users."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers import users as controller
from guards.auth import get_current_user, require_super_admin
from schemas.auth import UserOut

router = APIRouter(tags=["auth"])

router.add_api_route("/users/me", controller.me, methods=["GET"], response_model=UserOut, dependencies=[Depends(get_current_user)])
router.add_api_route("/users", controller.list_users, methods=["GET"], response_model=list[UserOut], dependencies=[Depends(require_super_admin)])
router.add_api_route("/users/{user_id}", controller.update_user, methods=["PATCH"], response_model=UserOut, dependencies=[Depends(require_super_admin)])
