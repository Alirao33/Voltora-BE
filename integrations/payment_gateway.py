"""Infrastructure integration: payment gateway."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from fastapi import HTTPException
from typing import Optional, Protocol

from config.settings import get_settings
from constants.enums import PaymentMethod

log_payments = logging.getLogger("voltora.payments")


@dataclass
class GatewayResult:
    ok: bool
    provider_ref: Optional[str] = None
    message: str = ""


class PaymentGateway(Protocol):
    def charge(self, reference: str, method: PaymentMethod, amount_pkr: float) -> GatewayResult: ...
    def refund(self, reference: str, amount_pkr: float) -> GatewayResult: ...


class SandboxGateway:
    """Accepts every reference except those starting with FAIL (for demos and automated tests)."""

    def charge(self, reference: str, method: PaymentMethod, amount_pkr: float) -> GatewayResult:
        if reference.upper().startswith("FAIL"):
            return GatewayResult(False, None, "Declined by sandbox")
        return GatewayResult(True, f"SBX-{reference}")

    def refund(self, reference: str, amount_pkr: float) -> GatewayResult:
        return GatewayResult(True, f"SBX-RF-{reference}")


def get_gateway() -> PaymentGateway:
    name = get_settings().payment_gateway
    if name == "sandbox":
        return SandboxGateway()
    raise HTTPException(501, f"Payment gateway '{name}' is not configured")
