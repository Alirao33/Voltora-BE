"""Route definitions: auth."""

from __future__ import annotations

from fastapi import APIRouter

from controllers import auth as controller
from schemas.auth import Token, UserOut

router = APIRouter(tags=["auth"])

router.add_api_route("/register", controller.register, methods=["POST"], response_model=UserOut, status_code=201)
router.add_api_route("/token", controller.login, methods=["POST"], response_model=Token)
router.add_api_route("/auth/refresh", controller.refresh, methods=["POST"], response_model=Token)
router.add_api_route("/auth/otp/request", controller.otp_request, methods=["POST"])
router.add_api_route("/auth/otp/verify", controller.otp_verify, methods=["POST"], response_model=Token)
router.add_api_route("/auth/admin/login", controller.admin_login, methods=["POST"])
router.add_api_route("/auth/admin/verify", controller.admin_verify, methods=["POST"], response_model=Token)
