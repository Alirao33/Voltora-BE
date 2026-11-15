"""Pydantic schemas: base."""

from __future__ import annotations

from pydantic import ConfigDict
from typing import Optional

from utils.security import normalize_phone

ORM = ConfigDict(from_attributes=True)


def clean_phone(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    return normalize_phone(v)
