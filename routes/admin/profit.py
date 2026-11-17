"""Route definitions: profit log, platform settings and registration fees."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers.admin import profit as controller
from guards.auth import require_admin, require_super_admin
from modules.profit import PlatformSettings

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

router.add_api_route("/profit/log", controller.profit_log, methods=["GET"])
router.add_api_route("/platform-settings", controller.get_platform_settings, methods=["GET"], response_model=PlatformSettings, dependencies=[Depends(require_super_admin)])
router.add_api_route("/platform-settings", controller.set_platform_settings, methods=["PUT"], response_model=PlatformSettings, dependencies=[Depends(require_super_admin)])
router.add_api_route("/fees", controller.list_fees, methods=["GET"])
router.add_api_route("/fees/{fee_id}/received", controller.mark_fee_received, methods=["POST"], dependencies=[Depends(require_super_admin)])
