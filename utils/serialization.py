"""Shared helpers: serialization."""

from __future__ import annotations

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from typing import Any

def _camel(key: str) -> str:
    head, *tail = key.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in tail)


def dual_keys(obj: Any) -> Any:
    """Emit both snake_case and camelCase keys so Python and Next.js/TypeScript clients work seamlessly."""
    if isinstance(obj, dict):
        out = {k: dual_keys(v) for k, v in obj.items()}
        for key in list(out):
            if isinstance(key, str) and "_" in key.strip("_"):
                out.setdefault(_camel(key), out[key])
        return out
    if isinstance(obj, (list, tuple)):
        return [dual_keys(i) for i in obj]
    return obj


class DualKeyJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return super().render(dual_keys(jsonable_encoder(content)))


def to_wire(payload: Any) -> Any:
    return dual_keys(jsonable_encoder(payload))
