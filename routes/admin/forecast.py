"""Route definitions: forecast."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers.admin import forecast as controller
from guards.auth import require_admin, require_super_admin

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

router.add_api_route("/weather", controller.add_weather, methods=["POST"], dependencies=[Depends(require_super_admin)])
router.add_api_route("/forecast/train", controller.train_forecast, methods=["POST"], dependencies=[Depends(require_super_admin)])
router.add_api_route("/forecast/refresh", controller.refresh_forecast, methods=["POST"], dependencies=[Depends(require_super_admin)])
router.add_api_route("/forecast/models", controller.list_models, methods=["GET"])
router.add_api_route("/forecast/drift", controller.get_drift, methods=["GET"])
router.add_api_route("/forecast/latest", controller.latest_forecast, methods=["GET"])
router.add_api_route("/pilot/simulate", controller.simulate_pilot, methods=["POST"], dependencies=[Depends(require_super_admin)])
