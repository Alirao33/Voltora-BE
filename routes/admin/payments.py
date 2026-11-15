"""Route definitions: payments."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers.admin import payments as controller
from guards.auth import require_admin, require_super_admin
from schemas.payment import PaymentOut

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

router.add_api_route("/payments/{payment_id}/refund", controller.refund_payment, methods=["POST"], response_model=PaymentOut, dependencies=[Depends(require_super_admin)])
