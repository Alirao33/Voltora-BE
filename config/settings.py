"""Configuration: settings."""

from __future__ import annotations

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"                                   # dev | test | prod
    database_url: str = "sqlite:///./voltora.db"       # prod: postgresql+psycopg2://user:pass@host/db
    secret_key: str = "change-me-in-production"
    device_api_key: str = "change-me-device-key"       # ESP32 / simulator / ingestion service
    cors_origins: str = "*"

    access_token_minutes: int = 30
    refresh_token_days: int = 7
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    otp_max_requests_per_10min: int = 5
    otp_dev_echo: bool = False                         # return OTP in API response (dev only)
    admin_2fa_enabled: bool = True                     # False = admin password logs in directly (local demo only)

    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "Admin@2026!"
    bootstrap_admin_phone: str = "+923001234567"
    seed_demo_data: bool = True

    auto_register_packs: bool = True                   # unknown pack_id in telemetry -> create it (dev/simulator)
    mqtt_enabled: bool = False
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_telemetry_topic: str = "voltora/+/+/telemetry"
    mqtt_command_topic: str = "voltora/{station_id}/{slot_id}/cmd"

    payment_gateway: str = "sandbox"
    payment_webhook_secret: str = "change-me-webhook"

    booking_grace_minutes: int = 15
    max_booking_advance_hours: int = 24
    sweeper_interval_s: int = 60

    overheat_trip_c: float = 45.0                      # WebSocket TRIP_RELAY command threshold
    lockout_c: float = 55.0                            # slot locked, pack cannot be issued
    low_supply_v: float = 180.0                        # mains voltage alert threshold
    min_issue_soc: float = 20.0                        # swaps allowed in 20-100 % window
    warning_health: float = 80.0
    retire_health: float = 70.0
    rider_points_per_swap: int = 10

    ws_heartbeat_s: float = 15.0
    ws_queue_size: int = 100
    model_dir: str = "models_store"

    @property
    def is_prod(self) -> bool:
        return self.env.lower() == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
