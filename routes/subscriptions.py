"""Route definitions: subscriptions."""

from __future__ import annotations

from fastapi import APIRouter

from controllers import subscriptions as controller

router = APIRouter(tags=["Subscriptions"])

router.add_api_route("/api/plans", controller.get_plans, methods=["GET"])
router.add_api_route("/api/subscriptions/plans", controller.get_plans, methods=["GET"])
router.add_api_route("/api/subscriptions", controller.get_plans, methods=["GET"])
router.add_api_route("/api/subscriptions/subscribe", controller.create_subscription, methods=["POST"])

