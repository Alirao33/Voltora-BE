"""Shared helpers: security."""

from __future__ import annotations

import bcrypt
import re
from typing import Optional

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: Optional[str]) -> bool:
    return bool(hashed) and bcrypt.checkpw(password.encode(), hashed.encode())


def normalize_phone(raw: str) -> str:
    v = re.sub(r"[\s\-()]", "", raw)
    if not re.fullmatch(r"(\+92|0)3\d{9}", v):
        raise ValueError("Enter a valid Pakistani mobile number, e.g. 0300 1234567")
    return "+92" + v[-10:]
