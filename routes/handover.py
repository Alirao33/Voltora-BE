"""Route definitions: rider <-> delivery man handover (chat and locker-code consent)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers import handover as controller
from guards.auth import get_current_user, require_driver

router = APIRouter(prefix="/api", tags=["handover"])

rider = [Depends(get_current_user)]
driver = [Depends(require_driver)]

router.add_api_route("/rider/orders/{order_id}/messages", controller.list_messages, methods=["GET"], dependencies=rider)
router.add_api_route("/rider/orders/{order_id}/messages", controller.send_message, methods=["POST"], dependencies=rider)
router.add_api_route("/rider/orders/{order_id}/locker-code", controller.respond_locker_code, methods=["POST"], dependencies=rider)
router.add_api_route("/driver/orders/{order_id}/messages", controller.list_messages, methods=["GET"], dependencies=driver)
router.add_api_route("/driver/orders/{order_id}/messages", controller.send_message, methods=["POST"], dependencies=driver)
router.add_api_route("/driver/orders/{order_id}/locker-code/request", controller.request_locker_code, methods=["POST"], dependencies=driver)
