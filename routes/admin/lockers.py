"""Route definitions: lockers."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers.admin import lockers as controller
from guards.auth import require_admin
from schemas.locker import LockerOut

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

router.add_api_route("/lockers", controller.create_locker, methods=["POST"], response_model=LockerOut, status_code=201)
router.add_api_route("/lockers/{locker_id}", controller.update_locker, methods=["PATCH"], response_model=LockerOut)
