"""Route definitions: bookings."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from controllers.rider import bookings as controller
from guards.auth import get_current_user
from schemas.booking import BookingOut

router = APIRouter(prefix="/api/rider", tags=["rider"])

router.add_api_route("/book-locker", controller.book_locker, methods=["POST"], response_model=BookingOut, status_code=201, dependencies=[Depends(get_current_user)])
router.add_api_route("/smart-swap", controller.smart_swap, methods=["POST"], dependencies=[Depends(get_current_user)])
router.add_api_route("/bookings", controller.my_bookings, methods=["GET"], response_model=list[BookingOut], dependencies=[Depends(get_current_user)])
router.add_api_route("/bookings/{booking_id}/confirm", controller.confirm_booking, methods=["POST"], response_model=BookingOut, dependencies=[Depends(get_current_user)])
router.add_api_route("/bookings/{booking_id}/cancel", controller.cancel_booking, methods=["POST"], response_model=BookingOut, dependencies=[Depends(get_current_user)])
