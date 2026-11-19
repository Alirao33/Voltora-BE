"""Route definitions: driver."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers import driver as controller
from guards.auth import require_driver
from schemas.van import VanOut, VanTaskOut

router = APIRouter(prefix="/api/driver", tags=["driver"], dependencies=[Depends(require_driver)])

router.add_api_route("/me", controller.driver_van, methods=["GET"], response_model=VanOut)
router.add_api_route("/tasks", controller.driver_tasks, methods=["GET"], response_model=list[VanTaskOut])
router.add_api_route("/location", controller.update_location, methods=["POST"], response_model=VanOut)
router.add_api_route("/tasks/{task_id}/stage", controller.set_task_stage, methods=["POST"], response_model=VanTaskOut)
router.add_api_route("/tasks/{task_id}/confirm", controller.confirm_driver_task, methods=["POST"], response_model=VanTaskOut)
router.add_api_route("/return-to-depot", controller.driver_return_to_depot, methods=["POST"])
router.add_api_route("/orders/handover", controller.complete_delivery_handover, methods=["POST"])
