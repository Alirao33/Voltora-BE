"""Route definitions: riders."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers.admin import riders as controller
from guards.auth import require_admin, require_super_admin

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

router.add_api_route("/users/{rider_id}/status", controller.update_rider_status, methods=["PATCH"], dependencies=[Depends(require_super_admin)])
