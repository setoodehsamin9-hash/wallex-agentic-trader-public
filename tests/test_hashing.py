from datetime import UTC, datetime
from decimal import Decimal

from wallex_agent.contracts.hashing import canonical_json, sha256_hex


def test_canonical_json_sorts_keys_and_stringifies_decimal() -> None:
    assert canonical_json({"b": Decimal("1.10"), "a": 1}) == b'{"a":1,"b":"1.10"}'


def test_canonical_json_datetime_is_iso_utc() -> None:
    assert canonical_json({"t": datetime(2026, 10, 22, 9, 0, tzinfo=UTC)}) == b'{"t":"2026-10-22T09:00:00Z"}'


def test_hash_independent_of_key_order() -> None:
    assert sha256_hex(canonical_json({"x": 1, "y": [1, 2]})) == sha256_hex(canonical_json({"y": [1, 2], "x": 1}))
    assert len(sha256_hex(b"")) == 64
