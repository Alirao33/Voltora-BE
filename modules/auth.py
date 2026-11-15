"""Business logic: auth."""

from __future__ import annotations

import hashlib
import hmac
import jwt
import logging
import secrets
from datetime import timedelta
from fastapi import HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typing import Any, Optional

from config.settings import get_settings
from constants.enums import Role
from constants.roles import ADMIN_ROLES
from constants.security import ALGORITHM
from models import OtpChallenge, User
from modules.audit import audit
from schemas.auth import AdminLogin, AdminVerify, OtpRequest, OtpVerify, RefreshRequest, UserCreate
from utils.dates import now_utc
from utils.security import hash_password, verify_password

log_security = logging.getLogger("voltora.security")


def _encode(user: User, kind: str, delta: timedelta) -> str:
    payload = {"sub": str(user.id), "role": user.role.value, "type": kind, "exp": now_utc() + delta}
    return jwt.encode(payload, get_settings().secret_key, algorithm=ALGORITHM)


def create_access_token(user: User) -> str:
    return _encode(user, "access", timedelta(minutes=get_settings().access_token_minutes))


def create_refresh_token(user: User) -> str:
    return _encode(user, "refresh", timedelta(days=get_settings().refresh_token_days))


def token_pair(user: User) -> dict[str, str]:
    return {
        "access_token": create_access_token(user),
        "refresh_token": create_refresh_token(user),
        "token_type": "bearer",
    }


def decode_token(token: str, kind: str = "access") -> dict[str, Any]:
    payload = jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])
    if payload.get("type") != kind:
        raise jwt.InvalidTokenError("wrong token type")
    return payload


def user_from_token(db: Session, token: str, kind: str = "access") -> User:
    err = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Could not validate credentials",
        {"WWW-Authenticate": "Bearer"},
    )
    try:
        uid = int(decode_token(token, kind)["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise err
    user = db.get(User, uid)
    if user is None:
        raise err
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User is disabled")
    return user


def _otp_hash(code: str, challenge_id: int) -> str:
    return hmac.new(
        get_settings().secret_key.encode(),
        f"{challenge_id}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


def send_sms(phone: Optional[str], message: str) -> None:
    """SMS gateway integration point; logs OTP in non-production environments."""
    log_security.info("SMS to %s: %s", phone, message if not get_settings().is_prod else "[redacted]")


def issue_otp(
    db: Session, *, purpose: str, phone: Optional[str], user: Optional[User] = None
) -> tuple[OtpChallenge, str]:
    s = get_settings()
    recent = (
        db.scalar(
            select(func.count())
            .select_from(OtpChallenge)
            .where(OtpChallenge.phone == phone, OtpChallenge.created_at > now_utc() - timedelta(minutes=10))
        )
        if phone
        else 0
    )
    if recent >= s.otp_max_requests_per_10min:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many OTP requests, try again later")

    code = "".join(secrets.choice("0123456789") for _ in range(6))
    ch = OtpChallenge(
        purpose=purpose,
        phone=phone,
        user_id=user.id if user else None,
        expires_at=now_utc() + timedelta(seconds=s.otp_ttl_seconds),
    )
    db.add(ch)
    db.flush()
    ch.code_hash = _otp_hash(code, ch.id)
    send_sms(phone, f"Your Voltora verification code is {code}")
    return ch, code


def check_otp(db: Session, ch: Optional[OtpChallenge], code: str) -> bool:
    """Constant-time OTP verification with attempt counter persistence."""
    s = get_settings()
    if ch is None or ch.consumed or ch.expires_at < now_utc() or ch.attempts >= s.otp_max_attempts:
        return False
    ch.attempts += 1
    ok = hmac.compare_digest(ch.code_hash, _otp_hash(code, ch.id))
    if ok:
        ch.consumed = True
    db.commit()
    return ok


def register(body: UserCreate, db: Session):
    if db.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(409, "Username is already registered")
    if body.phone and db.scalar(select(User).where(User.phone == body.phone)):
        raise HTTPException(409, "Phone number is already registered")

    user = User(
        username=body.username,
        phone=body.phone,
        full_name=body.full_name,
        hashed_password=hash_password(body.password),
        role=Role.RIDER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def login(form: OAuth2PasswordRequestForm, db: Session):
    user = db.scalar(select(User).where(User.username == form.username))
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect username or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User is disabled")
    if user.role in ADMIN_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin accounts must sign in with 2FA via /auth/admin/login")
    user.last_login_at = now_utc()
    db.commit()
    return token_pair(user)


def refresh(body: RefreshRequest, db: Session):
    user = user_from_token(db, body.refresh_token, kind="refresh")
    return token_pair(user)


def otp_request(body: OtpRequest, db: Session):
    ch, code = issue_otp(db, purpose="LOGIN", phone=body.phone)
    db.commit()
    s = get_settings()
    out: dict[str, Any] = {
        "challenge_id": ch.id,
        "expires_in": s.otp_ttl_seconds,
        "message": "OTP sent",
    }
    if s.otp_dev_echo or not s.is_prod:
        out["dev_code"] = code
    return out


def otp_verify(body: OtpVerify, db: Session):
    ch = db.scalar(
        select(OtpChallenge)
        .where(
            OtpChallenge.phone == body.phone,
            OtpChallenge.purpose == "LOGIN",
            OtpChallenge.consumed.is_(False),
        )
        .order_by(OtpChallenge.id.desc())
    )
    if not check_otp(db, ch, body.code):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired OTP")

    user = db.scalar(select(User).where(User.phone == body.phone))
    if user is None:
        user = User(phone=body.phone, full_name=body.full_name, role=Role.RIDER)
        db.add(user)
        db.flush()
    elif body.full_name and not user.full_name:
        user.full_name = body.full_name

    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User is disabled")
    user.last_login_at = now_utc()
    db.commit()
    return token_pair(user)


def admin_login(body: AdminLogin, db: Session):
    user = db.scalar(select(User).where(User.username == body.username))
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    if user.role not in ADMIN_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not an admin account")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User is disabled")

    ch, code = issue_otp(db, purpose="ADMIN_2FA", phone=user.phone, user=user)
    db.commit()
    s = get_settings()
    out: dict[str, Any] = {
        "challenge_id": ch.id,
        "expires_in": s.otp_ttl_seconds,
        "message": "2FA code sent",
    }
    if s.otp_dev_echo or not s.is_prod:
        out["dev_code"] = code
    return out


def admin_verify(body: AdminVerify, db: Session):
    ch = db.get(OtpChallenge, body.challenge_id)
    if not ch or ch.purpose != "ADMIN_2FA" or not check_otp(db, ch, body.code):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired 2FA code")
    user = db.get(User, ch.user_id) if ch.user_id else None
    if not user or user.role not in ADMIN_ROLES or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid admin session")
    user.last_login_at = now_utc()
    audit(db, user, "ADMIN_LOGIN", "user", user.id)
    db.commit()
    return token_pair(user)
