"""Route definitions: system."""

from __future__ import annotations

from fastapi import APIRouter

from controllers import system as controller

router = APIRouter(tags=["system"])

router.add_api_route("/health/live", controller.health_live, methods=["GET"])
router.add_api_route("/health/ready", controller.health_ready, methods=["GET"])
router.add_api_route("/metrics", controller.prometheus_metrics, methods=["GET"])
router.add_api_route("/api/payments/webhook", controller.payment_webhook, methods=["POST"])
