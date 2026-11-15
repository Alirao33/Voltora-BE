"""Route definitions: audit."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers.admin import audit as controller
from guards.auth import require_admin, require_super_admin
from schemas.audit import AuditOut

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

router.add_api_route("/audit", controller.list_audit, methods=["GET"], response_model=list[AuditOut], dependencies=[Depends(require_super_admin)])
