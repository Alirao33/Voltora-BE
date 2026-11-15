"""Constants: enums."""

from __future__ import annotations

from enum import Enum

class Role(str, Enum):
    ADMIN = "ADMIN"
    STATION_ADMIN = "STATION_ADMIN"
    DRIVER = "DRIVER"
    RIDER = "RIDER"


class LockerStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    ONE_LEFT = "ONE_LEFT"
    FULL = "FULL"
    RESERVED_VIP = "RESERVED_VIP"
    MAINTENANCE = "MAINTENANCE"


class DoorLockState(str, Enum):
    LOCKED = "LOCKED"
    UNLOCKED = "UNLOCKED"


class ActionType(str, Enum):
    SWAP_DROP_AND_PICK = "SWAP_DROP_AND_PICK"
    RESERVE_VIP_SLOT = "RESERVE_VIP_SLOT"
    GUEST_PAY_SWAP = "GUEST_PAY_SWAP"


class BookingStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class RiderTier(str, Enum):
    VIP = "VIP"
    SUBSCRIBED = "SUBSCRIBED"
    GUEST = "GUEST"


class SiteAvailability(str, Enum):
    AVAILABLE = "AVAILABLE"
    ONE_LEFT = "ONE_LEFT"
    FULL = "FULL"


class Chemistry(str, Enum):
    LFP = "LFP"
    NMC = "NMC"


class PaymentMethod(str, Enum):
    RAAST = "RAAST"
    WALLET = "WALLET"
    CARD = "CARD"


class PaymentStatus(str, Enum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    REFUNDED = "REFUNDED"


class BatteryState(str, Enum):
    CHARGED = "CHARGED"        # in a station slot, ready to issue
    CHARGING = "CHARGING"
    DEPLETED = "DEPLETED"      # returned, waiting for a charger
    RESERVED = "RESERVED"      # held for a paid order
    IN_USE = "IN_USE"          # with a rider
    ON_VAN = "ON_VAN"
    LOCKED = "LOCKED"          # over-temperature lockout (> 55 C)
    RETIRED = "RETIRED"        # health < 70 -> second-life stationary storage


class HealthStatus(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    RETIRE = "RETIRE"


class OrderMode(str, Enum):
    STATION = "STATION"
    LOCKER = "LOCKER"
    HOME = "HOME"


class OrderStatus(str, Enum):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"  # home delivery: paid/reserved, waiting for admin to approve
    PAID = "PAID"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class PlanKind(str, Enum):
    PER_SWAP = "PER_SWAP"
    MONTHLY = "MONTHLY"
    LOCKER = "LOCKER"


class SubscriptionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class AlertType(str, Enum):
    WEAK_PACK = "WEAK_PACK"
    PACK_RETIRED = "PACK_RETIRED"
    STOCK_OUT = "STOCK_OUT"
    OVER_TEMP = "OVER_TEMP"
    LOCKOUT = "LOCKOUT"
    LOW_SUPPLY = "LOW_SUPPLY"


class AlertSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class TaskKind(str, Enum):
    REBALANCE = "REBALANCE"
    DELIVERY = "DELIVERY"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
