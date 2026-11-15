"""Constants: orders."""

from __future__ import annotations

from constants.enums import OrderStatus

OPEN_ORDER = (OrderStatus.PAID, OrderStatus.IN_PROGRESS)


DEFAULT_CAPACITY_FRACTION = 0.8

# Subscribed riders may pay a swap bill later: due PAY_LATER_GRACE_DAYS after the pass ends,
# then a LATE_PENALTY_RATE surcharge on the actual amount applies.
PAY_LATER_GRACE_DAYS = 2
LATE_PENALTY_RATE = 0.05
