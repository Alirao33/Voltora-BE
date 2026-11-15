"""Route definitions: plans."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers.admin import plans as controller
from guards.auth import require_admin, require_super_admin
from schemas.plan import PlanOut

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

router.add_api_route("/plans", controller.admin_plans, methods=["GET"], response_model=list[PlanOut])
router.add_api_route("/plans", controller.create_plan, methods=["POST"], response_model=PlanOut, status_code=201, dependencies=[Depends(require_super_admin)])
router.add_api_route("/plans/{plan_id}", controller.update_plan, methods=["PATCH"], response_model=PlanOut, dependencies=[Depends(require_super_admin)])
