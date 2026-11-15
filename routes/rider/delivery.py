"""Route definitions: delivery."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers.rider import delivery as controller
from guards.auth import get_current_user

router = APIRouter(prefix="/api/rider", tags=["rider"])

router.add_api_route("/delivery/validate-address", controller.validate_delivery_address, methods=["POST"], dependencies=[Depends(get_current_user)])
router.add_api_route("/battery-catalogue", controller.get_battery_catalogue, methods=["GET"], dependencies=[Depends(get_current_user)])
router.add_api_route("/orders/create", controller.create_battery_delivery_order, methods=["POST"], dependencies=[Depends(get_current_user)])
