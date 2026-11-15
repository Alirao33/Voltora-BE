"""Route definitions: orders."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers.admin import orders as controller
from guards.auth import require_admin
from schemas.order import OrderOut

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

router.add_api_route("/orders", controller.admin_list_orders, methods=["GET"], response_model=list[OrderOut])
router.add_api_route("/orders/{order_id}/review", controller.admin_review_order, methods=["POST"])
router.add_api_route("/delivery-zones", controller.get_delivery_zones, methods=["GET"])
