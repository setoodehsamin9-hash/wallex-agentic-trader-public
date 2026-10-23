"""Shared base for all contracts: strict, immutable, versioned."""

from datetime import datetime, timedelta
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict

SCHEMA_VERSION = "1.0.0"
"""Bump the minor version for additive fields, the major version for anything a replay of an
old journal could misread. Every persisted record carries the version it was written with."""


def _require_utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if offset is None or offset != timedelta(0):
        raise ValueError("timestamps must be timezone-aware UTC")
    return value


UtcDatetime = Annotated[datetime, AfterValidator(_require_utc)]
"""All stored and compared times are UTC. Asia/Tehran is for human-facing display only."""


class Contract(BaseModel):
    """Frozen, extra-forbidding base. Unknown fields are an error, never silently dropped."""

    model_config = ConfigDict(frozen=True, extra="forbid")
