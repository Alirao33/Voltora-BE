"""ORM models: one file per table. Importing this package registers every table on Base.metadata."""
from models.alert import Alert
from models.app_setting import AppSetting
from models.audit_log import AuditLog
from models.battery import Battery
from models.booking import Booking
from models.forecast_model_version import ForecastModelVersion
from models.forecast_record import ForecastRecord
from models.locker import Locker
from models.order import Order
from models.order_message import OrderMessage
from models.otp_challenge import OtpChallenge
from models.payment import Payment
from models.plan import Plan
from models.profit import PlatformFee, ProfitEntry
from models.pricing_change import PricingChange
from models.station import Station
from models.subscription import Subscription
from models.telemetry_reading import TelemetryReading
from models.user import User
from models.van import Van
from models.van_task import VanTask
from models.weather_observation import WeatherObservation

__all__ = [
    "Alert",
    "AppSetting",
    "AuditLog",
    "Battery",
    "Booking",
    "ForecastModelVersion",
    "ForecastRecord",
    "Locker",
    "Order",
    "OrderMessage",
    "OtpChallenge",
    "Payment",
    "Plan",
    "PlatformFee",
    "ProfitEntry",
    "PricingChange",
    "Station",
    "Subscription",
    "TelemetryReading",
    "User",
    "Van",
    "VanTask",
    "WeatherObservation",
]
