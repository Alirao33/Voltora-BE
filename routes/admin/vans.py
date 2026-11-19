"""Route definitions: vans."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers.admin import vans as controller
from guards.auth import require_admin, require_super_admin
from schemas.van import VanOut, VanTaskOut

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

router.add_api_route("/vans", controller.list_vans, methods=["GET"], response_model=list[VanOut])
router.add_api_route("/vans", controller.create_van, methods=["POST"], response_model=VanOut, status_code=201, dependencies=[Depends(require_super_admin)])
router.add_api_route("/vans/{van_id}", controller.update_van, methods=["PATCH"], response_model=VanOut, dependencies=[Depends(require_super_admin)])
router.add_api_route("/rebalancing/plan", controller.plan_rebalancing, methods=["POST"], response_model=list[VanTaskOut], dependencies=[Depends(require_super_admin)])
router.add_api_route("/rebalancing/tasks", controller.list_tasks, methods=["GET"], response_model=list[VanTaskOut])
router.add_api_route("/charging/plan", controller.get_charging_plan, methods=["GET"])
router.add_api_route("/charging/plan/recompute", controller.recompute_charging_plan, methods=["POST"])
