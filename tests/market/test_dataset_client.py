import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from wallex_agent.market.bars import Bar, WallexPayloadError
from wallex_agent.market.dataset import DatasetIntegrityError, load_dataset, write_dataset
from wallex_agent.market.wallex_public import WallexPublicClient


def _bars(n: int) -> list[Bar]:
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    return [Bar(t0 + timedelta(minutes=i), Decimal("100.5"), Decimal(101), Decimal(100), Decimal("100.25"), Decimal("0.001"))
            for i in range(n)]


def test_dataset_roundtrip_and_deterministic_hash(tmp_path: Path) -> None:
    m1 = write_dataset(tmp_path / "a", "ds", {"BTCTMN": _bars(30)}, extra={"note": "x"})
    m2 = write_dataset(tmp_path / "b", "ds", {"BTCTMN": _bars(30)}, extra={"note": "x"})
    assert m1["symbols"]["BTCTMN"]["sha256"] == m2["symbols"]["BTCTMN"]["sha256"]
    ds = load_dataset(tmp_path / "a")
    assert ds.bars["BTCTMN"] == _bars(30)
    assert ds.refs["BTCTMN"].content_sha256 == m1["symbols"]["BTCTMN"]["sha256"]


def test_dataset_tamper_detected(tmp_path: Path) -> None:
    write_dataset(tmp_path, "ds", {"BTCTMN": _bars(5)}, extra={})
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["symbols"]["BTCTMN"]["sha256"] = "0" * 64
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(DatasetIntegrityError):
        load_dataset(tmp_path)


def test_client_parses_decimal_and_rejects_success_false() -> None:
    calls: list[str] = []

    def fake_get(url: str, timeout: float) -> bytes:
        calls.append(url)
        if "depth" in url:
            return b'{"result":{"ask":[{"price":22123973858,"quantity":0.3529008}],"bid":[]},"success":true}'
        return b'{"success":false,"message":"bad symbol","result":{}}'

    client = WallexPublicClient(get=fake_get, min_interval_s=0)
    depth = client.depth("BTCTMN")
    assert isinstance(depth["result"]["ask"][0]["quantity"], Decimal)
    with pytest.raises(WallexPayloadError, match="bad symbol"):
        client.markets_v1()
    assert calls[0] == "https://api.wallex.ir/v1/depth?symbol=BTCTMN"
