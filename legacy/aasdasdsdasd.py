# """
# Voltora – Smart EV battery-swap network (Karachi) – FastAPI backend.

# Scope of this file
# ------------------
# Pydantic schemas, business-logic functions, REST routes and the telemetry
# WebSocket. Database engine/ORM/migrations and authentication/JWT live elsewhere.

# Integration seams (search for "SEAM")
# -------------------------------------
# * `Store` / `get_store()`  – a thread-safe repository holding sites, lockers,
#   bookings and the latest telemetry. Swap its methods for your ORM session
#   without touching routes or business rules.
# * Auth – add your dependency to the routers, e.g.
#   `admin_router = APIRouter(..., dependencies=[Depends(require_admin)])`.
#   Until then, `user_id` and `rider_tier` are read from the request body.
# * `verify_payment()` – plug in Raast / wallet / card gateway.
# * `hub.publish_reading()` – call from your MQTT ingestion service (or use
#   POST /api/telemetry/ingest).

# Pricing constants come from the Voltora CEP proposal, section D. "Thermal surge"
# and the peak-hour demand factor are our own assumptions to calibrate in the pilot.
# """
# import asyncio
# import contextlib
# import logging
# import math
# import os
# import re
# import secrets
# import threading
# from collections import defaultdict
# from contextlib import asynccontextmanager
# from datetime import datetime, timedelta, timezone
# from decimal import ROUND_HALF_UP, Decimal
# from enum import Enum
# from typing import Any, Optional

# from fastapi import (
#     APIRouter,
#     Depends,
#     FastAPI,
#     HTTPException,
#     Query,
#     WebSocket,
# )
# from fastapi.encoders import jsonable_encoder
# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.responses import JSONResponse
# from pydantic import (
#     BaseModel,
#     Field,
#     computed_field,
#     field_validator,
#     model_validator,
# )
# import uvicorn

# logger = logging.getLogger("voltora")

# # ═══════════════════════════════════════════════════════════════════════════
# # Configuration
# # ═══════════════════════════════════════════════════════════════════════════
# PKT = timezone(timedelta(hours=5))  # Karachi, no DST

# HARD_MAX_BATTERIES_PER_LOCKER = 2   # configurable ceiling (spec: max 2)
# DEFAULT_LOCKER_CAPACITY = 2
# BOOKING_GRACE = timedelta(minutes=15)       # slot is held until scheduled + grace
# MAX_BOOKING_ADVANCE = timedelta(hours=24)
# MAX_BOOKING_PAST_SKEW = timedelta(minutes=5)
# DEFAULT_SEARCH_RADIUS_KM = 25.0

# OVERHEAT_TRIP_C = 45.0              # relay trip threshold (WebSocket guardrail)
# WS_HEARTBEAT_S = 15.0
# WS_QUEUE_SIZE = 100

# # Pricing (proposal §D)
# TARIFF_GRID_PKR_PER_KWH = 65.0      # verify against current K-Electric/NEPRA tariff
# OPERATING_COST_PKR = 30.0
# MARGIN = 0.20
# PEAK_DEMAND_FACTOR = 1.2            # assumption
# PEAK_HOURS_PKT = ((8, 10), (17, 20))  # assumption: commute peaks
# # Thermal penalty (proposal §B) reused for the surge
# THERMAL_FREE_BELOW_C = 35.0
# THERMAL_SLOPE_PER_C = 2.5
# THERMAL_LOCKOUT_C = 55.0
# THERMAL_SURGE_CAP = 0.30            # assumption: surge multiplier never exceeds +30%


# # ═══════════════════════════════════════════════════════════════════════════
# # Enums
# # ═══════════════════════════════════════════════════════════════════════════
# class LockerStatus(str, Enum):
#     AVAILABLE = "AVAILABLE"
#     ONE_LEFT = "ONE_LEFT"
#     FULL = "FULL"
#     RESERVED_VIP = "RESERVED_VIP"
#     MAINTENANCE = "MAINTENANCE"


# class DoorLockState(str, Enum):
#     LOCKED = "LOCKED"
#     UNLOCKED = "UNLOCKED"


# class ActionType(str, Enum):
#     SWAP_DROP_AND_PICK = "SWAP_DROP_AND_PICK"
#     RESERVE_VIP_SLOT = "RESERVE_VIP_SLOT"
#     GUEST_PAY_SWAP = "GUEST_PAY_SWAP"


# class BookingStatus(str, Enum):
#     PENDING = "PENDING"
#     CONFIRMED = "CONFIRMED"
#     COMPLETED = "COMPLETED"
#     CANCELLED = "CANCELLED"
#     EXPIRED = "EXPIRED"


# class RiderTier(str, Enum):
#     VIP = "VIP"
#     SUBSCRIBED = "SUBSCRIBED"
#     GUEST = "GUEST"


# class SiteAvailability(str, Enum):
#     AVAILABLE = "AVAILABLE"
#     ONE_LEFT = "ONE_LEFT"
#     FULL = "FULL"


# class Chemistry(str, Enum):
#     LFP = "LFP"
#     NMC = "NMC"


# class PaymentMethod(str, Enum):
#     RAAST = "RAAST"
#     WALLET = "WALLET"
#     CARD = "CARD"


# WEAR_PKR_PER_KWH = {Chemistry.LFP: 6.0, Chemistry.NMC: 12.0}
# ACTIVE_BOOKING = {BookingStatus.PENDING, BookingStatus.CONFIRMED}
# BOOKABLE_LOCKER = {LockerStatus.AVAILABLE, LockerStatus.ONE_LEFT}


# # ═══════════════════════════════════════════════════════════════════════════
# # Dual-key (snake_case + camelCase) serialization
# # ═══════════════════════════════════════════════════════════════════════════
# def _camel(key: str) -> str:
#     head, *tail = key.split("_")
#     return head + "".join(p[:1].upper() + p[1:] for p in tail)


# def dual_keys(obj: Any) -> Any:
#     """Recursively add a camelCase twin for every snake_case dict key."""
#     if isinstance(obj, dict):
#         out: dict = {}
#         for key, value in obj.items():
#             out[key] = dual_keys(value)
#         for key in list(out):
#             if isinstance(key, str) and "_" in key.strip("_"):
#                 out.setdefault(_camel(key), out[key])
#         return out
#     if isinstance(obj, (list, tuple)):
#         return [dual_keys(i) for i in obj]
#     return obj


# class DualKeyJSONResponse(JSONResponse):
#     def render(self, content: Any) -> bytes:
#         return super().render(dual_keys(jsonable_encoder(content)))


# def to_wire(payload: Any) -> Any:
#     """For WebSocket frames: JSON-safe + dual keys."""
#     return dual_keys(jsonable_encoder(payload))


# # ═══════════════════════════════════════════════════════════════════════════
# # Time helpers
# # ═══════════════════════════════════════════════════════════════════════════
# def now_pkt() -> datetime:
#     return datetime.now(PKT)


# def ensure_pkt(dt: datetime) -> datetime:
#     """Naive datetimes are interpreted as Karachi local time."""
#     return dt.replace(tzinfo=PKT) if dt.tzinfo is None else dt.astimezone(PKT)


# # ═══════════════════════════════════════════════════════════════════════════
# # Pydantic schemas
# # ═══════════════════════════════════════════════════════════════════════════
# class StationSchema(BaseModel):
#     id: str = Field(..., min_length=1, max_length=40, examples=["STATION-01"])
#     name: str = Field(..., min_length=1, max_length=120, examples=["Saddar Empress Hub"])
#     latitude: float = Field(..., ge=-90, le=90, examples=[24.8607])
#     longitude: float = Field(..., ge=-180, le=180, examples=[67.0211])
#     address: str = Field(..., min_length=1, max_length=300)
#     total_lockers: int = Field(..., ge=0, le=500)


# class StationCreate(StationSchema):
#     id: Optional[str] = Field(None, min_length=1, max_length=40)  # auto STATION-NN


# class LockerSchema(BaseModel):
#     id: str = Field(..., examples=["LOCKER-101"])
#     site_id: str
#     door_number: str = Field(..., examples=["Door 01"])
#     max_battery_capacity: int = Field(
#         DEFAULT_LOCKER_CAPACITY, ge=1, le=HARD_MAX_BATTERIES_PER_LOCKER
#     )
#     current_occupied_batteries: int = Field(0, ge=0)
#     status: LockerStatus = LockerStatus.AVAILABLE
#     door_lock_state: DoorLockState = DoorLockState.LOCKED

#     @computed_field  # type: ignore[misc]
#     @property
#     def is_occupancy_full(self) -> bool:
#         return self.current_occupied_batteries >= self.max_battery_capacity

#     @model_validator(mode="after")
#     def _occupancy_within_capacity(self):
#         if self.current_occupied_batteries > self.max_battery_capacity:
#             raise ValueError("current_occupied_batteries exceeds max_battery_capacity")
#         return self


# class LockerCreate(BaseModel):
#     id: Optional[str] = Field(None, min_length=1, max_length=40)       # auto LOCKER-NNN
#     site_id: str
#     door_number: Optional[str] = Field(None, min_length=1, max_length=40)  # auto "Door 01"
#     max_battery_capacity: int = Field(
#         DEFAULT_LOCKER_CAPACITY, ge=1, le=HARD_MAX_BATTERIES_PER_LOCKER
#     )
#     current_occupied_batteries: int = Field(0, ge=0)
#     status: LockerStatus = LockerStatus.AVAILABLE
#     door_lock_state: DoorLockState = DoorLockState.LOCKED
#     reserved_for_user_id: Optional[str] = None  # required when status=RESERVED_VIP

#     @model_validator(mode="after")
#     def _checks(self):
#         if self.current_occupied_batteries > self.max_battery_capacity:
#             raise ValueError("current_occupied_batteries exceeds max_battery_capacity")
#         if self.status == LockerStatus.RESERVED_VIP and not self.reserved_for_user_id:
#             raise ValueError("reserved_for_user_id is required for RESERVED_VIP lockers")
#         return self


# class LockerUpdate(BaseModel):
#     status: Optional[LockerStatus] = Field(
#         None, description="Only AVAILABLE (leave maintenance) or MAINTENANCE can be set."
#     )
#     max_battery_capacity: Optional[int] = Field(None, ge=1, le=HARD_MAX_BATTERIES_PER_LOCKER)

#     @field_validator("status")
#     @classmethod
#     def _admin_statuses(cls, v):
#         if v is not None and v not in (LockerStatus.AVAILABLE, LockerStatus.MAINTENANCE):
#             raise ValueError("status can only be set to AVAILABLE or MAINTENANCE; other states are automatic")
#         return v


# class BatterySchema(BaseModel):
#     pack_id: str = Field(..., examples=["VOLT-0199"])
#     voltage: float = Field(..., ge=0, le=120, examples=[51.2])
#     soc: float = Field(..., ge=0, le=100, examples=[85.5])
#     soh: float = Field(..., ge=0, le=100, examples=[94.2])
#     temperature_c: float = Field(..., ge=-40, le=150, examples=[38.5])
#     assigned_user_id: Optional[str] = None


# class BookingSlotSchema(BaseModel):
#     booking_id: str = Field(..., examples=["BK-1001"])
#     user_id: str
#     site_id: str
#     locker_id: str
#     action_type: ActionType
#     scheduled_time: datetime
#     unlock_code: str = Field(..., examples=["#8492"])
#     status: BookingStatus = BookingStatus.PENDING


# # ── Request / response models ──────────────────────────────────────────────
# class BookLockerRequest(BaseModel):
#     user_id: str = Field(..., min_length=1)
#     site_id: str
#     locker_id: Optional[str] = Field(None, description="Omit to auto-assign the best locker.")
#     action_type: ActionType = ActionType.SWAP_DROP_AND_PICK
#     scheduled_time: Optional[datetime] = Field(None, description="Defaults to now (Karachi time).")
#     rider_tier: RiderTier = RiderTier.SUBSCRIBED  # SEAM: derive from auth claims

#     @model_validator(mode="after")
#     def _no_guest_here(self):
#         if self.action_type == ActionType.GUEST_PAY_SWAP:
#             raise ValueError("GUEST_PAY_SWAP bookings are created via /api/guest/pay-and-swap")
#         return self


# class SmartSwapRequest(BaseModel):
#     user_id: str = Field(..., min_length=1)
#     latitude: float = Field(..., ge=-90, le=90)
#     longitude: float = Field(..., ge=-180, le=180)
#     rider_tier: RiderTier = RiderTier.SUBSCRIBED  # SEAM: derive from auth claims
#     max_distance_km: float = Field(DEFAULT_SEARCH_RADIUS_KM, gt=0, le=100)
#     auto_book: bool = True
#     scheduled_time: Optional[datetime] = None


# class BookingActionRequest(BaseModel):
#     user_id: str = Field(..., min_length=1)  # SEAM: use authenticated user instead
#     unlock_code: Optional[str] = None


# class GuestQuoteRequest(BaseModel):
#     site_id: Optional[str] = None
#     latitude: Optional[float] = Field(None, ge=-90, le=90)
#     longitude: Optional[float] = Field(None, ge=-180, le=180)
#     pack_id: Optional[str] = Field(None, description="Returned (depleted) pack; used to look up voltage/temperature.")
#     voltage: Optional[float] = Field(None, gt=0, le=120)
#     capacity_used_ah: float = Field(..., gt=0, le=200)
#     chemistry: Chemistry = Chemistry.LFP
#     temperature_c: Optional[float] = Field(None, ge=-40, le=150)

#     @model_validator(mode="after")
#     def _need_location(self):
#         if not self.site_id and (self.latitude is None or self.longitude is None):
#             raise ValueError("Provide site_id, or both latitude and longitude")
#         return self


# class GuestSwapRequest(GuestQuoteRequest):
#     guest_name: str = Field(..., min_length=2, max_length=80)
#     phone: str
#     payment_method: PaymentMethod
#     payment_reference: str = Field(..., min_length=4, max_length=100)
#     amount_paid_pkr: float = Field(..., ge=0)

#     @field_validator("phone")
#     @classmethod
#     def _pk_mobile(cls, v: str) -> str:
#         v = re.sub(r"[\s\-()]", "", v)
#         if not re.fullmatch(r"(\+92|0)3\d{9}", v):
#             raise ValueError("Enter a valid Pakistani mobile number, e.g. 0300 1234567")
#         return v


# class PriceBreakdown(BaseModel):
#     energy_kwh: float
#     grid_cost_pkr: float
#     wear_cost_pkr: float
#     operating_cost_pkr: float
#     base_cost_pkr: float
#     margin_pkr: float
#     demand_factor: float
#     demand_surcharge_pkr: float
#     thermal_penalty: float
#     thermal_surge_factor: float
#     thermal_surge_pkr: float
#     price_final_pkr: float
#     currency: str = "PKR"


# # ═══════════════════════════════════════════════════════════════════════════
# # In-memory repository  (SEAM: replace with your ORM-backed implementation)
# # ═══════════════════════════════════════════════════════════════════════════
# class Store:
#     def __init__(self) -> None:
#         self.lock = threading.RLock()
#         self.sites: dict[str, StationSchema] = {}
#         self.lockers: dict[str, LockerSchema] = {}
#         self.bookings: dict[str, BookingSlotSchema] = {}
#         # locker_id -> (user_id, booking_id | None). booking_id None = admin-provisioned.
#         self.vip_reservations: dict[str, tuple[str, Optional[str]]] = {}
#         self.payment_refs: dict[str, str] = {}      # payment_reference -> booking_id
#         self.latest_battery: dict[str, BatterySchema] = {}
#         self._site_seq = 0
#         self._locker_seq = 100
#         self._booking_seq = 1000

#     def lockers_at(self, site_id: str) -> list[LockerSchema]:
#         return sorted(
#             (l for l in self.lockers.values() if l.site_id == site_id), key=lambda l: l.id
#         )

#     def next_site_id(self) -> str:
#         while True:
#             self._site_seq += 1
#             sid = f"STATION-{self._site_seq:02d}"
#             if sid not in self.sites:
#                 return sid

#     def next_locker_id(self) -> str:
#         while True:
#             self._locker_seq += 1
#             lid = f"LOCKER-{self._locker_seq}"
#             if lid not in self.lockers:
#                 return lid

#     def next_booking_id(self) -> str:
#         self._booking_seq += 1
#         return f"BK-{self._booking_seq}"


# STORE = Store()


# def get_store() -> Store:
#     return STORE


# # ═══════════════════════════════════════════════════════════════════════════
# # Business logic – lockers & bookings
# # ═══════════════════════════════════════════════════════════════════════════
# def derive_status(locker: LockerSchema, store: Store) -> LockerStatus:
#     """Single source of truth for automatic locker status."""
#     if locker.status == LockerStatus.MAINTENANCE:
#         return LockerStatus.MAINTENANCE          # sticky until an admin clears it
#     if locker.id in store.vip_reservations:
#         return LockerStatus.RESERVED_VIP
#     free = locker.max_battery_capacity - locker.current_occupied_batteries
#     if free <= 0:
#         return LockerStatus.FULL
#     if free == 1 and locker.max_battery_capacity > 1:
#         return LockerStatus.ONE_LEFT
#     return LockerStatus.AVAILABLE


# def refresh_status(locker: LockerSchema, store: Store) -> None:
#     locker.status = derive_status(locker, store)


# def release_booking(store: Store, booking: BookingSlotSchema, new_status: BookingStatus) -> None:
#     """Move an active booking to a terminal state and free what it held. Caller holds store.lock."""
#     if booking.status not in ACTIVE_BOOKING:
#         return
#     booking.status = new_status
#     locker = store.lockers.get(booking.locker_id)
#     if not locker:
#         return
#     if booking.action_type == ActionType.RESERVE_VIP_SLOT:
#         res = store.vip_reservations.get(locker.id)
#         if res and res[1] == booking.booking_id:
#             del store.vip_reservations[locker.id]
#     else:
#         locker.current_occupied_batteries = max(0, locker.current_occupied_batteries - 1)
#     refresh_status(locker, store)


# def expire_stale_bookings(store: Store, now: Optional[datetime] = None) -> int:
#     now = now or now_pkt()
#     count = 0
#     with store.lock:
#         for b in list(store.bookings.values()):
#             if b.status in ACTIVE_BOOKING and now > ensure_pkt(b.scheduled_time) + BOOKING_GRACE:
#                 release_booking(store, b, BookingStatus.EXPIRED)
#                 count += 1
#     return count


# def _new_unlock_code(store: Store, locker_id: str) -> str:
#     in_use = {
#         b.unlock_code
#         for b in store.bookings.values()
#         if b.locker_id == locker_id and b.status in ACTIVE_BOOKING
#     }
#     while True:
#         code = f"#{secrets.randbelow(10000):04d}"
#         if code not in in_use:
#             return code


# def _pick_locker(store: Store, site_id: str, user_id: str, action: ActionType) -> LockerSchema:
#     lockers = store.lockers_at(site_id)
#     if action == ActionType.SWAP_DROP_AND_PICK:  # VIPs use their own reserved locker first
#         mine = [
#             l for l in lockers
#             if store.vip_reservations.get(l.id, (None, None))[0] == user_id
#             and l.status != LockerStatus.MAINTENANCE
#             and not l.is_occupancy_full
#         ]
#         if mine:
#             return min(mine, key=lambda l: (l.current_occupied_batteries, l.id))
#     public = [l for l in lockers if l.status in BOOKABLE_LOCKER and not l.is_occupancy_full]
#     if not public:
#         raise HTTPException(409, "No available locker at this site (full, reserved or under maintenance)")
#     return min(public, key=lambda l: (l.current_occupied_batteries, l.id))


# def place_booking(
#     store: Store,
#     *,
#     user_id: str,
#     tier: RiderTier,
#     site_id: str,
#     locker_id: Optional[str],
#     action: ActionType,
#     scheduled_time: Optional[datetime],
#     initial_status: BookingStatus = BookingStatus.CONFIRMED,
# ) -> tuple[BookingSlotSchema, LockerSchema]:
#     """Validate, hold a locker slot, update locker status. Returns snapshots."""
#     now = now_pkt()
#     when = ensure_pkt(scheduled_time) if scheduled_time else now
#     if when < now - MAX_BOOKING_PAST_SKEW:
#         raise HTTPException(422, "scheduled_time is in the past")
#     if when > now + MAX_BOOKING_ADVANCE:
#         raise HTTPException(422, "Bookings can be made at most 24 hours ahead")

#     with store.lock:
#         expire_stale_bookings(store, now)
#         if site_id not in store.sites:
#             raise HTTPException(404, f"Site {site_id} not found")

#         if action == ActionType.RESERVE_VIP_SLOT and tier != RiderTier.VIP:
#             raise HTTPException(403, "Only VIP riders can reserve a VIP locker")
#         if action in (ActionType.SWAP_DROP_AND_PICK, ActionType.RESERVE_VIP_SLOT):
#             if any(
#                 b.user_id == user_id and b.action_type == action and b.status in ACTIVE_BOOKING
#                 for b in store.bookings.values()
#             ):
#                 raise HTTPException(409, f"You already have an active {action.value} booking")

#         if locker_id:
#             locker = store.lockers.get(locker_id)
#             if not locker:
#                 raise HTTPException(404, f"Locker {locker_id} not found")
#             if locker.site_id != site_id:
#                 raise HTTPException(400, "Locker does not belong to this site")
#         else:
#             locker = _pick_locker(store, site_id, user_id, action)

#         if locker.status == LockerStatus.MAINTENANCE:
#             raise HTTPException(409, "Locker is under maintenance")
#         if locker.is_occupancy_full:
#             raise HTTPException(409, "Locker is full")
#         reservation = store.vip_reservations.get(locker.id)
#         if reservation and reservation[0] != user_id:
#             raise HTTPException(409, "Locker is reserved for a VIP rider")
#         if action == ActionType.RESERVE_VIP_SLOT and reservation:
#             raise HTTPException(409, "Locker is already reserved")
#         if action != ActionType.RESERVE_VIP_SLOT and reservation is None and locker.status not in BOOKABLE_LOCKER:
#             raise HTTPException(409, f"Locker is not bookable (status {locker.status.value})")

#         booking = BookingSlotSchema(
#             booking_id=store.next_booking_id(),
#             user_id=user_id,
#             site_id=site_id,
#             locker_id=locker.id,
#             action_type=action,
#             scheduled_time=when,
#             unlock_code=_new_unlock_code(store, locker.id),
#             status=initial_status,
#         )
#         if action == ActionType.RESERVE_VIP_SLOT:
#             store.vip_reservations[locker.id] = (user_id, booking.booking_id)
#         else:
#             locker.current_occupied_batteries += 1
#         refresh_status(locker, store)
#         store.bookings[booking.booking_id] = booking
#         return booking.model_copy(), locker.model_copy()


# # ═══════════════════════════════════════════════════════════════════════════
# # Business logic – site occupancy & routing
# # ═══════════════════════════════════════════════════════════════════════════
# def site_snapshot(store: Store, site: StationSchema, include_lockers: bool) -> dict:
#     lockers = store.lockers_at(site.id)
#     bookable = [l for l in lockers if l.status in BOOKABLE_LOCKER]
#     free_slots = sum(l.max_battery_capacity - l.current_occupied_batteries for l in bookable)
#     availability = (
#         SiteAvailability.FULL if free_slots == 0
#         else SiteAvailability.ONE_LEFT if free_slots == 1
#         else SiteAvailability.AVAILABLE
#     )
#     counts = {s.value.lower(): 0 for s in LockerStatus}
#     for l in lockers:
#         counts[l.status.value.lower()] += 1
#     snap: dict[str, Any] = {
#         **site.model_dump(),
#         "availability": availability,
#         "free_slots": free_slots,
#         "lockers_registered": len(lockers),
#         "locker_status_counts": counts,
#     }
#     if include_lockers:
#         snap["lockers"] = lockers
#     return snap


# def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
#     r = 6371.0088
#     p1, p2 = math.radians(lat1), math.radians(lat2)
#     dphi, dlmb = p2 - p1, math.radians(lon2 - lon1)
#     a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
#     return 2 * r * math.asin(math.sqrt(a))


# def rank_public_sites(store: Store, lat: float, lon: float, max_km: float) -> list[tuple[float, StationSchema, int]]:
#     """Nearest-first sites with at least one free public slot: (distance_km, site, free_slots)."""
#     ranked = []
#     for site in store.sites.values():
#         free = sum(
#             l.max_battery_capacity - l.current_occupied_batteries
#             for l in store.lockers_at(site.id)
#             if l.status in BOOKABLE_LOCKER
#         )
#         if free <= 0:
#             continue
#         d = haversine_km(lat, lon, site.latitude, site.longitude)
#         if d <= max_km:
#             ranked.append((d, site, free))
#     ranked.sort(key=lambda t: (t[0], t[1].id))
#     return ranked


# def nearest_vip_locker(store: Store, user_id: str, lat: float, lon: float, max_km: float):
#     best = None
#     for locker_id, (owner, _) in store.vip_reservations.items():
#         if owner != user_id:
#             continue
#         locker = store.lockers.get(locker_id)
#         if not locker or locker.status == LockerStatus.MAINTENANCE or locker.is_occupancy_full:
#             continue
#         site = store.sites.get(locker.site_id)
#         if not site:
#             continue
#         d = haversine_km(lat, lon, site.latitude, site.longitude)
#         if d <= max_km and (best is None or d < best[0]):
#             best = (d, site, locker.id)
#     return best


# # ═══════════════════════════════════════════════════════════════════════════
# # Business logic – pricing (proposal §B and §D)
# # ═══════════════════════════════════════════════════════════════════════════
# def _money(x: float) -> float:
#     return float(Decimal(repr(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# def penalty_temp(t_c: float) -> float:
#     if t_c <= THERMAL_FREE_BELOW_C:
#         return 0.0
#     if t_c <= THERMAL_LOCKOUT_C:
#         return THERMAL_SLOPE_PER_C * (t_c - THERMAL_FREE_BELOW_C)
#     return 100.0


# def thermal_surge_factor(t_c: float) -> float:
#     return 1.0 + min(THERMAL_SURGE_CAP, penalty_temp(t_c) / 100.0)


# def demand_factor(now: datetime) -> float:
#     hour = ensure_pkt(now).hour
#     return PEAK_DEMAND_FACTOR if any(a <= hour < b for a, b in PEAK_HOURS_PKT) else 1.0


# def calculate_swap_price(
#     *, voltage: float, capacity_used_ah: float, chemistry: Chemistry, temperature_c: float, now: datetime
# ) -> PriceBreakdown:
#     """
#     E_kWh      = V × Ah / 1000
#     Cost_base  = E×Tariff_grid + E×Wear_rate + Cost_operating
#     Price      = Cost_base × (1 + Margin) × DemandFactor × ThermalSurge
#     """
#     energy = voltage * capacity_used_ah / 1000.0
#     grid = energy * TARIFF_GRID_PKR_PER_KWH
#     wear = energy * WEAR_PKR_PER_KWH[chemistry]
#     base = grid + wear + OPERATING_COST_PKR
#     margin = base * MARGIN
#     dem = demand_factor(now)
#     after_demand = (base + margin) * dem
#     surge = thermal_surge_factor(temperature_c)
#     final = after_demand * surge
#     return PriceBreakdown(
#         energy_kwh=round(energy, 4),
#         grid_cost_pkr=_money(grid),
#         wear_cost_pkr=_money(wear),
#         operating_cost_pkr=_money(OPERATING_COST_PKR),
#         base_cost_pkr=_money(base),
#         margin_pkr=_money(margin),
#         demand_factor=dem,
#         demand_surcharge_pkr=_money(after_demand - (base + margin)),
#         thermal_penalty=round(penalty_temp(temperature_c), 2),
#         thermal_surge_factor=round(surge, 4),
#         thermal_surge_pkr=_money(final - after_demand),
#         price_final_pkr=_money(final),
#     )


# def quote_for(req: GuestQuoteRequest, store: Store) -> PriceBreakdown:
#     with store.lock:
#         battery = store.latest_battery.get(req.pack_id) if req.pack_id else None
#     voltage = req.voltage if req.voltage is not None else (battery.voltage if battery else None)
#     if voltage is None:
#         raise HTTPException(422, "voltage is required (or send a pack_id that has recent telemetry)")
#     if req.temperature_c is not None:
#         temp = req.temperature_c
#     else:
#         temp = battery.temperature_c if battery else THERMAL_FREE_BELOW_C  # no data -> no surge
#     return calculate_swap_price(
#         voltage=voltage,
#         capacity_used_ah=req.capacity_used_ah,
#         chemistry=req.chemistry,
#         temperature_c=temp,
#         now=now_pkt(),
#     )


# def verify_payment(reference: str, method: PaymentMethod, amount_pkr: float) -> bool:
#     """SEAM: call Raast / wallet / card gateway. Sandbox mode accepts every reference."""
#     if os.getenv("VOLTORA_PAYMENT_SANDBOX", "1") == "1":
#         logger.info("Sandbox payment accepted: %s %s PKR %.2f", method.value, reference, amount_pkr)
#         return True
#     raise HTTPException(501, "Payment gateway is not configured")


# # ═══════════════════════════════════════════════════════════════════════════
# # Telemetry hub (WebSocket fan-out + thermal guardrail)
# # ═══════════════════════════════════════════════════════════════════════════
# def telemetry_frame(b: BatterySchema) -> dict:
#     return {
#         "type": "TELEMETRY",
#         "pack_id": b.pack_id,
#         "voltage": b.voltage,
#         "soc": b.soc,
#         "soh": b.soh,
#         "temperature_c": b.temperature_c,
#         "timestamp": now_pkt().isoformat(),
#     }


# def overheat_event(b: BatterySchema) -> dict:
#     return {
#         "type": "EMERGENCY",
#         "command": "TRIP_RELAY",
#         "alert": "OVERHEAT_CRITICAL",
#         "pack_id": b.pack_id,
#         "temperature_c": b.temperature_c,
#         "threshold_c": OVERHEAT_TRIP_C,
#         "timestamp": now_pkt().isoformat(),
#     }


# def frames_for(b: BatterySchema) -> list[dict]:
#     frames = [telemetry_frame(b)]
#     if b.temperature_c > OVERHEAT_TRIP_C:
#         frames.append(overheat_event(b))
#     return frames


# class TelemetryHub:
#     def __init__(self) -> None:
#         self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

#     def subscribe(self, pack_id: str) -> asyncio.Queue:
#         q: asyncio.Queue = asyncio.Queue(maxsize=WS_QUEUE_SIZE)
#         self._subs[pack_id].add(q)
#         return q

#     def unsubscribe(self, pack_id: str, q: asyncio.Queue) -> None:
#         self._subs[pack_id].discard(q)
#         if not self._subs[pack_id]:
#             self._subs.pop(pack_id, None)

#     def publish_reading(self, battery: BatterySchema) -> None:
#         """Must be called from the event-loop thread. Broadcasts the frame and, if hot, the trip command."""
#         for msg in frames_for(battery):
#             for q in list(self._subs.get(battery.pack_id, ())):
#                 if q.full():                      # slow consumer: drop oldest, never block
#                     with contextlib.suppress(asyncio.QueueEmpty):
#                         q.get_nowait()
#                 q.put_nowait(msg)


# hub = TelemetryHub()


# # ═══════════════════════════════════════════════════════════════════════════
# # Routers
# # ═══════════════════════════════════════════════════════════════════════════
# admin_router = APIRouter(prefix="/api/admin", tags=["admin"], default_response_class=DualKeyJSONResponse)
# rider_router = APIRouter(prefix="/api/rider", tags=["rider"], default_response_class=DualKeyJSONResponse)
# guest_router = APIRouter(prefix="/api/guest", tags=["guest"], default_response_class=DualKeyJSONResponse)
# telemetry_router = APIRouter(prefix="/api/telemetry", tags=["telemetry"], default_response_class=DualKeyJSONResponse)


# # ── Admin ───────────────────────────────────────────────────────────────────
# @admin_router.post("/sites", status_code=201)
# def create_site(body: StationCreate, store: Store = Depends(get_store)):
#     with store.lock:
#         site_id = body.id or store.next_site_id()
#         if site_id in store.sites:
#             raise HTTPException(409, f"Site {site_id} already exists")
#         site = StationSchema(**body.model_dump(exclude={"id"}), id=site_id)
#         store.sites[site_id] = site
#         return {"site": site, "lockers_registered": 0}


# @admin_router.post("/lockers", status_code=201)
# def create_locker(body: LockerCreate, store: Store = Depends(get_store)):
#     with store.lock:
#         site = store.sites.get(body.site_id)
#         if not site:
#             raise HTTPException(404, f"Site {body.site_id} not found")
#         existing = store.lockers_at(site.id)
#         if len(existing) >= site.total_lockers:
#             raise HTTPException(409, f"Site {site.id} is allocated {site.total_lockers} lockers and all are registered")

#         locker_id = body.id or store.next_locker_id()
#         if locker_id in store.lockers:
#             raise HTTPException(409, f"Locker {locker_id} already exists")
#         door = body.door_number or f"Door {len(existing) + 1:02d}"
#         if any(l.door_number == door for l in existing):
#             raise HTTPException(409, f"{door} already exists at {site.id}")

#         locker = LockerSchema(
#             id=locker_id,
#             site_id=site.id,
#             door_number=door,
#             max_battery_capacity=body.max_battery_capacity,
#             current_occupied_batteries=body.current_occupied_batteries,
#             status=body.status,
#             door_lock_state=body.door_lock_state,
#         )
#         if body.status == LockerStatus.RESERVED_VIP:
#             store.vip_reservations[locker_id] = (body.reserved_for_user_id, None)  # type: ignore[assignment]
#         store.lockers[locker_id] = locker
#         refresh_status(locker, store)  # AVAILABLE/ONE_LEFT/FULL are always derived from occupancy
#         return {"locker": locker.model_copy(), "site_id": site.id,
#                 "lockers_registered": len(existing) + 1, "total_lockers": site.total_lockers}


# @admin_router.patch("/lockers/{locker_id}")
# def update_locker(locker_id: str, body: LockerUpdate, store: Store = Depends(get_store)):
#     with store.lock:
#         locker = store.lockers.get(locker_id)
#         if not locker:
#             raise HTTPException(404, f"Locker {locker_id} not found")
#         if body.max_battery_capacity is not None:
#             if body.max_battery_capacity < locker.current_occupied_batteries:
#                 raise HTTPException(409, "New capacity is below the current occupancy")
#             locker.max_battery_capacity = body.max_battery_capacity
#         if body.status is not None:
#             locker.status = body.status
#         refresh_status(locker, store)
#         return {"locker": locker.model_copy()}


# @admin_router.get("/sites/occupancy")
# def sites_occupancy(
#     availability: Optional[SiteAvailability] = Query(None, description="Filter: FULL, ONE_LEFT or AVAILABLE"),
#     include_lockers: bool = Query(False),
#     store: Store = Depends(get_store),
# ):
#     with store.lock:
#         expire_stale_bookings(store)
#         sites = [site_snapshot(store, s, include_lockers) for s in sorted(store.sites.values(), key=lambda s: s.id)]
#     summary = {k.value.lower(): sum(1 for s in sites if s["availability"] == k) for k in SiteAvailability}
#     summary["total_sites"] = len(sites)
#     if availability:
#         sites = [s for s in sites if s["availability"] == availability]
#     return {"generated_at": now_pkt(), "summary": summary, "sites": sites}


# # ── Rider ───────────────────────────────────────────────────────────────────
# @rider_router.post("/book-locker", status_code=201)
# def book_locker(body: BookLockerRequest, store: Store = Depends(get_store)):
#     booking, locker = place_booking(
#         store,
#         user_id=body.user_id,
#         tier=body.rider_tier,
#         site_id=body.site_id,
#         locker_id=body.locker_id,
#         action=body.action_type,
#         scheduled_time=body.scheduled_time,
#     )
#     return {
#         "booking": booking,
#         "locker": locker,
#         "site": store.sites[booking.site_id],
#         "message": f"Slot held until {(ensure_pkt(booking.scheduled_time) + BOOKING_GRACE).strftime('%H:%M')} PKT",
#     }


# @rider_router.post("/smart-swap")
# def smart_swap(body: SmartSwapRequest, store: Store = Depends(get_store)):
#     with store.lock:
#         expire_stale_bookings(store)
#         strategy, fallback, target = "NEAREST_PUBLIC_SITE", False, None
#         if body.rider_tier == RiderTier.VIP:
#             target = nearest_vip_locker(store, body.user_id, body.latitude, body.longitude, body.max_distance_km)
#             if target:
#                 strategy = "VIP_RESERVED_LOCKER"
#             else:
#                 fallback = True  # no usable reserved locker -> public network

#         ranked = rank_public_sites(store, body.latitude, body.longitude, body.max_distance_km)
#         if target is None:
#             if not ranked:
#                 raise HTTPException(404, f"No site with a free locker within {body.max_distance_km:g} km")
#             target = (ranked[0][0], ranked[0][1], None)

#         distance_km, site, locker_id = target
#         locker = (
#             store.lockers[locker_id] if locker_id
#             else _pick_locker(store, site.id, body.user_id, ActionType.SWAP_DROP_AND_PICK)
#         )
#         requires_payment = body.rider_tier == RiderTier.GUEST
#         booking = None
#         if body.auto_book and not requires_payment:
#             booking, locker = place_booking(
#                 store,
#                 user_id=body.user_id,
#                 tier=body.rider_tier,
#                 site_id=site.id,
#                 locker_id=locker.id,
#                 action=ActionType.SWAP_DROP_AND_PICK,
#                 scheduled_time=body.scheduled_time,
#             )
#         alternatives = [
#             {"site": s, "distance_km": round(d, 2), "free_slots": free}
#             for d, s, free in ranked if s.id != site.id
#         ][:3]
#         return {
#             "strategy": strategy,
#             "fallback_to_public": fallback,
#             "site": site,
#             "locker": locker.model_copy(),
#             "distance_km": round(distance_km, 2),
#             "booking": booking,
#             "requires_payment": requires_payment,
#             "next_step": "POST /api/guest/pay-and-swap" if requires_payment else None,
#             "alternatives": alternatives,
#         }


# @rider_router.get("/bookings/{booking_id}")
# def get_booking(booking_id: str, store: Store = Depends(get_store)):
#     with store.lock:
#         expire_stale_bookings(store)
#         booking = store.bookings.get(booking_id)
#         if not booking:
#             raise HTTPException(404, "Booking not found")
#         return {"booking": booking.model_copy(), "locker": store.lockers[booking.locker_id].model_copy()}


# def _owned_booking(store: Store, booking_id: str, user_id: str) -> BookingSlotSchema:
#     booking = store.bookings.get(booking_id)
#     if not booking:
#         raise HTTPException(404, "Booking not found")
#     if booking.user_id != user_id:
#         raise HTTPException(403, "This booking belongs to another user")
#     return booking


# @rider_router.post("/bookings/{booking_id}/unlock")
# def unlock_locker(booking_id: str, body: BookingActionRequest, store: Store = Depends(get_store)):
#     with store.lock:
#         expire_stale_bookings(store)
#         booking = _owned_booking(store, booking_id, body.user_id)
#         if booking.status != BookingStatus.CONFIRMED:
#             raise HTTPException(409, f"Booking is {booking.status.value}")
#         if not body.unlock_code or not secrets.compare_digest(body.unlock_code.encode(), booking.unlock_code.encode()):
#             raise HTTPException(403, "Invalid unlock code")
#         locker = store.lockers[booking.locker_id]
#         locker.door_lock_state = DoorLockState.UNLOCKED
#         return {"unlocked": True, "locker": locker.model_copy()}


# @rider_router.post("/bookings/{booking_id}/complete")
# def complete_booking(booking_id: str, body: BookingActionRequest, store: Store = Depends(get_store)):
#     with store.lock:
#         expire_stale_bookings(store)
#         booking = _owned_booking(store, booking_id, body.user_id)
#         if booking.status != BookingStatus.CONFIRMED:
#             raise HTTPException(409, f"Booking is {booking.status.value}")
#         release_booking(store, booking, BookingStatus.COMPLETED)
#         locker = store.lockers[booking.locker_id]
#         locker.door_lock_state = DoorLockState.LOCKED
#         return {"booking": booking.model_copy(), "locker": locker.model_copy()}


# @rider_router.post("/bookings/{booking_id}/cancel")
# def cancel_booking(booking_id: str, body: BookingActionRequest, store: Store = Depends(get_store)):
#     with store.lock:
#         expire_stale_bookings(store)
#         booking = _owned_booking(store, booking_id, body.user_id)
#         if booking.status not in ACTIVE_BOOKING:
#             raise HTTPException(409, f"Booking is already {booking.status.value}")
#         release_booking(store, booking, BookingStatus.CANCELLED)
#         return {"booking": booking.model_copy(), "locker": store.lockers[booking.locker_id].model_copy()}


# # ── Guest ───────────────────────────────────────────────────────────────────
# def _resolve_guest_site(store: Store, req: GuestQuoteRequest) -> StationSchema:
#     if req.site_id:
#         site = store.sites.get(req.site_id)
#         if not site:
#             raise HTTPException(404, f"Site {req.site_id} not found")
#         return site
#     ranked = rank_public_sites(store, req.latitude, req.longitude, DEFAULT_SEARCH_RADIUS_KM)  # type: ignore[arg-type]
#     if not ranked:
#         raise HTTPException(404, "No site with a free locker nearby")
#     return ranked[0][1]


# @guest_router.post("/quote")
# def guest_quote(body: GuestQuoteRequest, store: Store = Depends(get_store)):
#     with store.lock:
#         expire_stale_bookings(store)
#         site = _resolve_guest_site(store, body)
#     return {"site": site, "price": quote_for(body, store), "valid_for_minutes": 10}


# @guest_router.post("/pay-and-swap", status_code=201)
# def guest_pay_and_swap(body: GuestSwapRequest, store: Store = Depends(get_store)):
#     price = quote_for(body, store)
#     if body.amount_paid_pkr + 0.005 < price.price_final_pkr:
#         raise HTTPException(402, f"Amount paid is below the swap price of PKR {price.price_final_pkr:.2f}")

#     guest_id = f"GUEST-{secrets.token_hex(3).upper()}"
#     with store.lock:
#         expire_stale_bookings(store)
#         if body.payment_reference in store.payment_refs:
#             raise HTTPException(409, "This payment reference has already been used")
#         site = _resolve_guest_site(store, body)
#         # Hold the slot first (PENDING) so we never take money for a locker that is gone.
#         booking, _ = place_booking(
#             store,
#             user_id=guest_id,
#             tier=RiderTier.GUEST,
#             site_id=site.id,
#             locker_id=None,
#             action=ActionType.GUEST_PAY_SWAP,
#             scheduled_time=None,
#             initial_status=BookingStatus.PENDING,
#         )
#         store.payment_refs[body.payment_reference] = booking.booking_id

#     def abort() -> None:
#         with store.lock:
#             release_booking(store, store.bookings[booking.booking_id], BookingStatus.CANCELLED)
#             store.payment_refs.pop(body.payment_reference, None)

#     try:
#         paid = verify_payment(body.payment_reference, body.payment_method, body.amount_paid_pkr)
#     except HTTPException:
#         abort()
#         raise
#     if not paid:
#         abort()
#         raise HTTPException(402, "Payment could not be verified")

#     with store.lock:
#         live = store.bookings[booking.booking_id]
#         if live.status != BookingStatus.PENDING:
#             raise HTTPException(409, "Booking expired before payment completed; contact support for a refund")
#         live.status = BookingStatus.CONFIRMED
#         locker = store.lockers[live.locker_id]
#         return {
#             "order_id": f"ORD-{live.booking_id}",
#             "guest": {"user_id": guest_id, "name": body.guest_name, "phone": body.phone},
#             "site": site,
#             "locker": locker.model_copy(),
#             "booking": live.model_copy(),
#             "price": price,
#             "payment": {
#                 "method": body.payment_method,
#                 "reference": body.payment_reference,
#                 "amount_paid_pkr": body.amount_paid_pkr,
#                 "change_due_pkr": _money(body.amount_paid_pkr - price.price_final_pkr),
#                 "status": "PAID",
#             },
#         }


# # ── Telemetry ingest (feed for the WebSocket; MQTT service can call hub directly) ──
# @telemetry_router.post("/ingest", status_code=202)
# async def ingest_telemetry(reading: BatterySchema, store: Store = Depends(get_store)):
#     with store.lock:
#         store.latest_battery[reading.pack_id] = reading.model_copy()
#     hub.publish_reading(reading)
#     return {
#         "accepted": True,
#         "pack_id": reading.pack_id,
#         "trip_relay": reading.temperature_c > OVERHEAT_TRIP_C,
#     }


# # ═══════════════════════════════════════════════════════════════════════════
# # WebSocket – /ws/telemetry/{pack_id}
# # ═══════════════════════════════════════════════════════════════════════════
# async def _ws_sender(ws: WebSocket, queue: asyncio.Queue) -> None:
#     while True:
#         try:
#             msg = await asyncio.wait_for(queue.get(), timeout=WS_HEARTBEAT_S)
#         except asyncio.TimeoutError:
#             msg = {"type": "HEARTBEAT", "timestamp": now_pkt().isoformat()}
#         await ws.send_json(to_wire(msg))


# async def _ws_receiver(ws: WebSocket) -> None:
#     while True:           # drain client messages until the client disconnects
#         msg = await ws.receive()
#         if msg["type"] == "websocket.disconnect":
#             return


# @contextlib.asynccontextmanager
# async def _subscription(pack_id: str):
#     queue = hub.subscribe(pack_id)
#     try:
#         yield queue
#     finally:
#         hub.unsubscribe(pack_id, queue)


# async def telemetry_ws(websocket: WebSocket, pack_id: str):
#     await websocket.accept()
#     async with _subscription(pack_id) as queue:
#         with STORE.lock:
#             latest = STORE.latest_battery.get(pack_id)
#         if latest:
#             for frame in frames_for(latest):          # snapshot (+ trip command if already hot)
#                 await websocket.send_json(to_wire(frame))
#         else:
#             await websocket.send_json(to_wire({
#                 "type": "WAITING", "pack_id": pack_id,
#                 "message": "Connected. Waiting for first telemetry reading.",
#             }))

#         sender = asyncio.create_task(_ws_sender(websocket, queue))
#         receiver = asyncio.create_task(_ws_receiver(websocket))
#         done, pending = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
#         for task in pending:
#             task.cancel()
#         for task in done:                              # swallow disconnect errors, log anything else
#             exc = task.exception()
#             if exc and exc.__class__.__name__ not in ("WebSocketDisconnect", "ConnectionClosedOK", "ConnectionClosedError"):
#                 logger.warning("Telemetry socket for %s ended: %r", pack_id, exc)
#     with contextlib.suppress(Exception):
#         await websocket.close()


# # ═══════════════════════════════════════════════════════════════════════════
# # App
# # ═══════════════════════════════════════════════════════════════════════════
# @asynccontextmanager
# async def lifespan(_: FastAPI):
#     async def sweeper() -> None:
#         while True:
#             await asyncio.sleep(60)
#             try:
#                 released = await asyncio.to_thread(expire_stale_bookings, STORE)
#                 if released:
#                     logger.info("Expired %d stale booking(s)", released)
#             except Exception:
#                 logger.exception("Booking sweeper failed")

#     task = asyncio.create_task(sweeper())
#     yield
#     task.cancel()
#     with contextlib.suppress(asyncio.CancelledError):
#         await task


# app = FastAPI(
#     title="Voltora API",
#     version="1.0.0",
#     description="Smart EV battery-swap network – Karachi",
#     default_response_class=DualKeyJSONResponse,
#     lifespan=lifespan,
# )

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_credentials=False,   # required when allow_origins is "*"
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# app.include_router(admin_router)
# app.include_router(rider_router)
# app.include_router(guest_router)
# app.include_router(telemetry_router)
# app.add_api_websocket_route("/ws/telemetry/{pack_id}", telemetry_ws)


# if __name__ == "__main__":
#     uvicorn.run("main:app", reload=True)