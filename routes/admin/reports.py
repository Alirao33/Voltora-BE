"""Route definitions: reports."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers.admin import reports as controller
from guards.auth import require_admin, require_super_admin

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

router.add_api_route("/reports/summary", controller.report_summary, methods=["GET"])
router.add_api_route("/reports/log", controller.report_log, methods=["GET"])
router.add_api_route("/reports/revenue.csv", controller.report_csv, methods=["GET"])
router.add_api_route("/reports/revenue", controller.report_revenue, methods=["GET"], dependencies=[Depends(require_super_admin)])
router.add_api_route("/reports/station-kpi", controller.report_station_kpi, methods=["GET"])
router.add_api_route("/analytics/overview", controller.get_analytics_overview, methods=["GET"])
router.add_api_route("/finance/summary", controller.get_finance_summary, methods=["GET"], dependencies=[Depends(require_super_admin)])
