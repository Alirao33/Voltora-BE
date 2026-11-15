"""Route definitions: guest."""

from __future__ import annotations

from fastapi import APIRouter

from controllers import guest as controller
from schemas.order import OrderOut
from schemas.plan import PlanOut

router = APIRouter(prefix="/api/guest", tags=["guest"])

router.add_api_route("/stations", controller.public_stations, methods=["GET"])
router.add_api_route("/stations/{station_id}", controller.public_station, methods=["GET"])
router.add_api_route("/plans", controller.public_plans, methods=["GET"], response_model=list[PlanOut])
router.add_api_route("/quote", controller.guest_quote, methods=["POST"])
router.add_api_route("/pay-and-swap", controller.guest_pay_and_swap, methods=["POST"], response_model=OrderOut, status_code=201)
