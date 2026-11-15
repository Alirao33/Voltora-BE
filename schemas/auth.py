"""Pydantic schemas: auth."""

from __future__ import annotations

import re
from pydantic import BaseModel, Field, field_validator
from typing import Optional

from constants.enums import Role
from schemas.base import clean_phone, ORM

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)
    phone: Optional[str] = None
    full_name: Optional[str] = Field(None, max_length=120)

    _p = field_validator("phone")(clean_phone)

    @field_validator("password")
    @classmethod
    def validate_password_complexity(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if not re.search(r"[A-Z]", value):
            raise ValueError("Password must contain at least one uppercase letter (A-Z)")
        if not re.search(r"[a-z]", value):
            raise ValueError("Password must contain at least one lowercase letter (a-z)")
        if not re.search(r"\d", value):
            raise ValueError("Password must contain at least one number (0-9)")
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', value):
            raise ValueError("Password must contain at least one special character (!@#$%^&*)")
        return value


class UserOut(BaseModel):
    model_config = ORM
    id: int
    username: Optional[str] = None
    phone: Optional[str] = None
    full_name: Optional[str] = None
    role: Role
    station_id: Optional[str] = None
    is_active: bool
    is_admin: bool
    is_vip: bool
    points: int


class Token(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"


class OtpRequest(BaseModel):
    phone: str
    _p = field_validator("phone")(clean_phone)


class OtpVerify(BaseModel):
    phone: str
    code: str = Field(..., min_length=4, max_length=8)
    full_name: Optional[str] = Field(None, max_length=120)
    _p = field_validator("phone")(clean_phone)


class AdminLogin(BaseModel):
    username: str
    password: str


class AdminVerify(BaseModel):
    challenge_id: int
    code: str = Field(..., min_length=4, max_length=8)


class RefreshRequest(BaseModel):
    refresh_token: str


class UserAdminUpdate(BaseModel):
    role: Optional[Role] = None
    station_id: Optional[str] = None
    is_vip: Optional[bool] = None
    full_name: Optional[str] = None
    phone: Optional[str] = None
    _p = field_validator("phone")(clean_phone)


class RiderStatusUpdate(BaseModel):
    status: str
    reason: Optional[str] = None
