from datetime import UTC, datetime, timedelta
from decimal import Decimal

from wallex_agent.agent.context import DecisionContext
from wallex_agent.contracts import DataRef, FeatureSet, FeatureValue, MarketMeta, MarketSnapshot, RegimeLabel, SignalCandidate


def decision_context() -> DecisionContext:
    now = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)
    meta = MarketMeta(symbol="BTCTMN", quote_asset="TMN", price_precision=0, amount_precision=6,
                      min_qty=Decimal("0.000001"), min_notional=Decimal(50000), market_order_enabled=True, metadata_version="fixture")
    snapshot = MarketSnapshot(snapshot_id="qa-snapshot", symbol="BTCTMN", as_of=now, bar_interval_seconds=900,
                              last_closed_bar_open=now - timedelta(seconds=900),
                              candles=DataRef(dataset_id="qa-fixture", content_sha256="0" * 64, source="FIXTURE", symbol="BTCTMN",
                                              first_ts=now - timedelta(seconds=900), last_ts=now - timedelta(seconds=900), resolution_seconds=900),
                              book=None, meta=meta, universe_version="qa")
    features = FeatureSet(snapshot_id=snapshot.snapshot_id, feature_engine_version="qa", deterministic_regime=RegimeLabel.TRENDING_UP,
                          features=(FeatureValue(feature_id="BTCTMN.margin.16", name="margin", value=Decimal(1), unit="ATR", window_bars=16),))
    candidate = SignalCandidate(candidate_id="qa-candidate", snapshot_id=snapshot.snapshot_id, strategy_id="C1_breakout",
                                strategy_version="qa", symbol="BTCTMN", reference_price=Decimal(100), stop_price=Decimal(95),
                                take_profit_price=Decimal(110), max_holding_seconds=3600, exit_policy_id="qa",
                                estimated_round_trip_cost_bps=Decimal(60), evidence_feature_ids=("BTCTMN.margin.16",),
                                created_at=now, expires_at=now + timedelta(minutes=10))
    return DecisionContext(snapshot, features, (candidate,), (), {"equity_tmn": "5000000", "source": "virtual QA"}, (),
                           now, now + timedelta(seconds=60), "qa-run", "qa-case")
