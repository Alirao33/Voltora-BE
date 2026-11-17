"""Route definitions: one file per resource, all mounted on api_router."""
from fastapi import APIRouter

from routes import auth
from routes import driver
from routes import guest
from routes import handover
from routes import subscriptions
from routes import swaps
from routes import system
from routes import telemetry
from routes import users
from routes import websockets
from routes.admin import alerts as admin_alerts
from routes.admin import audit as admin_audit
from routes.admin import batteries as admin_batteries
from routes.admin import forecast as admin_forecast
from routes.admin import lockers as admin_lockers
from routes.admin import orders as admin_orders
from routes.admin import payments as admin_payments
from routes.admin import plans as admin_plans
from routes.admin import profit as admin_profit
from routes.admin import pricing as admin_pricing
from routes.admin import reports as admin_reports
from routes.admin import riders as admin_riders
from routes.admin import sites as admin_sites
from routes.admin import vans as admin_vans
from routes.rider import bookings as rider_bookings
from routes.rider import delivery as rider_delivery
from routes.rider import orders as rider_orders
from routes.rider import subscriptions as rider_subscriptions

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(rider_subscriptions.router)
api_router.include_router(rider_orders.router)
api_router.include_router(rider_bookings.router)
api_router.include_router(rider_delivery.router)
api_router.include_router(guest.router)
api_router.include_router(handover.router)
api_router.include_router(admin_sites.router)
api_router.include_router(admin_lockers.router)
api_router.include_router(admin_batteries.router)
api_router.include_router(admin_profit.router)
api_router.include_router(admin_pricing.router)
api_router.include_router(admin_plans.router)
api_router.include_router(admin_payments.router)
api_router.include_router(admin_alerts.router)
api_router.include_router(admin_vans.router)
api_router.include_router(admin_forecast.router)
api_router.include_router(admin_audit.router)
api_router.include_router(admin_reports.router)
api_router.include_router(admin_riders.router)
api_router.include_router(admin_orders.router)
api_router.include_router(driver.router)
api_router.include_router(telemetry.router)
api_router.include_router(swaps.router)
api_router.include_router(system.router)
api_router.include_router(websockets.router)
api_router.include_router(subscriptions.router)
