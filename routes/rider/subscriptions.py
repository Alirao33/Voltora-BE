"""Route definitions: subscriptions."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from typing import Optional

from controllers.rider import subscriptions as controller
from guards.auth import get_current_user
from schemas.plan import SubscriptionOut

router = APIRouter(prefix="/api/rider", tags=["rider"])

router.add_api_route("/subscription", controller.my_subscription, methods=["GET"], response_model=Optional[SubscriptionOut], dependencies=[Depends(get_current_user)])
router.add_api_route("/subscription/cancel", controller.cancel_subscription, methods=["POST"], response_model=SubscriptionOut, dependencies=[Depends(get_current_user)])
router.add_api_route("/plans/{plan_id}/subscribe", controller.subscribe_plan, methods=["POST"], response_model=SubscriptionOut, status_code=201, dependencies=[Depends(get_current_user)])
