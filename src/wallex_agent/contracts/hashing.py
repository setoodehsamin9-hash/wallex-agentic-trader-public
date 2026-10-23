"""Canonical JSON and SHA-256: the basis of snapshot ids, intent hashes, config hashes and payload refs."""

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel


def _default(value: object) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)  # type: ignore[arg-type]
    if isinstance(value, tuple):
        return list(value)  # type: ignore[arg-type]
    raise TypeError(f"not canonically serialisable: {type(value).__name__}")


def canonical_json(obj: BaseModel | Mapping[str, Any] | list[Any]) -> bytes:
    """Sorted keys, no whitespace, Decimals as strings, UTC 'Z' timestamps."""
    data: Any = obj.model_dump(mode="python") if isinstance(obj, BaseModel) else obj
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=_default, ensure_ascii=False).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_id(obj: BaseModel | Mapping[str, Any] | list[Any]) -> str:
    return sha256_hex(canonical_json(obj))
