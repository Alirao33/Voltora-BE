"""Route definitions: sites."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers.admin import sites as controller
from guards.auth import require_admin, require_super_admin

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

router.add_api_route("/sites", controller.list_sites, methods=["GET"])
router.add_api_route("/sites", controller.create_site, methods=["POST"], status_code=201, dependencies=[Depends(require_super_admin)])
router.add_api_route("/stations", controller.create_station_portal, methods=["POST"], status_code=201, dependencies=[Depends(require_super_admin)])
router.add_api_route("/sites/{station_id}", controller.update_site, methods=["PATCH"])
