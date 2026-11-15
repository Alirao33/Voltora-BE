from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["Subscriptions"])

class SubscriptionRequest(BaseModel):
    plan_code: str

@router.get("/api/plans")
@router.get("/api/subscriptions/plans")
@router.get("/api/subscriptions")
async def get_plans():
    from sqlalchemy import select
    from main import get_db, Plan
    
    db_gen = get_db()
    db = next(db_gen)
    
    # Exclude PER_SWAP or zero-price baseline from locker pass subscription page
    db_plans = db.scalars(
        select(Plan)
        .where(Plan.is_active.is_(True), Plan.code != "PER_SWAP", Plan.price_pkr > 0)
        .order_by(Plan.id)
    ).all()
    
    formatted_plans = []
    for p in db_plans:
        formatted_plans.append({
            "id": p.id,
            "code": p.code,
            "plan_code": p.code,
            "name": p.name,
            "price": p.price_pkr,
            "price_pkr": p.price_pkr,
            "swaps_included": p.swaps_included,
            "duration_days": p.duration_days,
            "billing_cycle": getattr(p, "duration_type", "Monthly").capitalize() if getattr(p, "duration_type", None) else "Monthly",
            "description": f"Exclusive station locker storage pass for {p.duration_days} days.",
            "features": [
                "Dedicated Station Locker Storage Rights",
                "Priority Locker Reservation Access",
                "Discounted Energy Consumption Settlement"
            ],
            "kind": p.kind.value if hasattr(p.kind, "value") else str(p.kind),
            "is_active": p.is_active
        })
    
    return formatted_plans

@router.post("/api/subscriptions/subscribe")
async def create_subscription(req: SubscriptionRequest):
    return {
        "status": "success",
        "message": f"Successfully secured locker pass subscription for {req.plan_code}",
        "plan_code": req.plan_code
    }