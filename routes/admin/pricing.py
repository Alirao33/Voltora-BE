"""Route definitions: pricing."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers.admin import pricing as controller
from guards.auth import require_admin, require_super_admin
from modules.pricing import PricingConfig

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

router.add_api_route("/pricing", controller.get_pricing, methods=["GET"], response_model=PricingConfig)
router.add_api_route("/pricing", controller.set_pricing, methods=["PUT"], response_model=PricingConfig, dependencies=[Depends(require_super_admin)])
router.add_api_route("/pricing/update-locker-rates", controller.update_locker_rates, methods=["POST"], dependencies=[Depends(require_super_admin)])
