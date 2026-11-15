"""Route definitions: orders."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers.rider import orders as controller
from guards.auth import get_current_user
from schemas.order import OrderOut

router = APIRouter(prefix="/api/rider", tags=["rider"])

router.add_api_route("/orders", controller.create_rider_order, methods=["POST"], response_model=OrderOut, status_code=201, dependencies=[Depends(get_current_user)])
router.add_api_route("/orders", controller.list_orders, methods=["GET"], response_model=list[OrderOut], dependencies=[Depends(get_current_user)])
router.add_api_route("/orders/{order_id}", controller.get_order, methods=["GET"], response_model=OrderOut, dependencies=[Depends(get_current_user)])
router.add_api_route("/orders/{order_id}/unlock", controller.unlock_rider_order, methods=["POST"], response_model=OrderOut, dependencies=[Depends(get_current_user)])
router.add_api_route("/orders/{order_id}/complete", controller.complete_rider_order, methods=["POST"], response_model=OrderOut, dependencies=[Depends(get_current_user)])
router.add_api_route("/orders/{order_id}/cancel", controller.cancel_rider_order, methods=["POST"], response_model=OrderOut, dependencies=[Depends(get_current_user)])
router.add_api_route("/orders/{order_id}/rate", controller.rate_order, methods=["POST"], response_model=OrderOut, dependencies=[Depends(get_current_user)])
router.add_api_route("/orders/{order_id}/pay-bill", controller.pay_order_bill, methods=["POST"], response_model=OrderOut, dependencies=[Depends(get_current_user)])
router.add_api_route("/orders/{order_id}/receipt", controller.get_receipt, methods=["GET"], dependencies=[Depends(get_current_user)])
