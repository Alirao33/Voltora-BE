"""Route definitions: swaps."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers import swaps as controller
from guards.auth import get_current_user

router = APIRouter(prefix="/api/swaps", tags=["swaps"])

router.add_api_route("/request", controller.request_battery_swap, methods=["POST"], dependencies=[Depends(get_current_user)])
router.add_api_route("/complete", controller.complete_physical_swap, methods=["POST"], dependencies=[Depends(get_current_user)])
router.add_api_route("/history/{rider_id}", controller.get_rider_swap_history, methods=["GET"], dependencies=[Depends(get_current_user)])
