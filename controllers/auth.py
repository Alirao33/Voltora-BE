"""Thin controllers (HTTP in, module call, HTTP out): auth."""

from __future__ import annotations

from fastapi import Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from config.database import get_db
from modules import auth as auth_service
from schemas.auth import AdminLogin, AdminVerify, OtpRequest, OtpVerify, RefreshRequest, UserCreate

def register(body: UserCreate, db: Session = Depends(get_db)):
    return auth_service.register(body=body, db=db)


def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    return auth_service.login(form=form, db=db)


def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    return auth_service.refresh(body=body, db=db)


def otp_request(body: OtpRequest, db: Session = Depends(get_db)):
    return auth_service.otp_request(body=body, db=db)


def otp_verify(body: OtpVerify, db: Session = Depends(get_db)):
    return auth_service.otp_verify(body=body, db=db)


def admin_login(body: AdminLogin, db: Session = Depends(get_db)):
    return auth_service.admin_login(body=body, db=db)


def admin_verify(body: AdminVerify, db: Session = Depends(get_db)):
    return auth_service.admin_verify(body=body, db=db)
