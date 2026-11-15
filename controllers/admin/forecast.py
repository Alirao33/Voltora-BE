"""Thin controllers (HTTP in, module call, HTTP out): forecast."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from config.database import get_db
from guards.auth import get_current_user
from models import User
from modules import forecasting as forecasting_service
from schemas.forecast import PilotRequest, TrainRequest, WeatherIn

def add_weather(body: WeatherIn, actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return forecasting_service.add_weather(body=body, actor=actor, db=db)


def train_forecast(body: TrainRequest = TrainRequest(), actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return forecasting_service.train_forecast(body=body, actor=actor, db=db)


def refresh_forecast(actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return forecasting_service.refresh_forecast(actor=actor, db=db)


def get_drift(db: Session = Depends(get_db)):
    return forecasting_service.get_drift(db=db)


def latest_forecast(actor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return forecasting_service.latest_forecast(actor=actor, db=db)


def simulate_pilot(body: PilotRequest = PilotRequest(), actor: User = Depends(get_current_user)):
    return forecasting_service.simulate_pilot(body=body, actor=actor)
