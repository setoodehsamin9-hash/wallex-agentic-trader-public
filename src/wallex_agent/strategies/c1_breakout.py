"""C1 — short-term breakout above the PRIOR window's high, in an up-trend, only when the target clears costs.

Falsifiable hypothesis (Phase 01): part of confirmed breakouts continue far enough to exit profitably after all
costs. Not assumed profitable; the backtest study measures it. All parameters come from config (C1Params).
Exits are fixed here (stop, take-profit, max holding) and cannot be loosened downstream.

exit_mode TIME (Phase 04, c1-0.2.x): no take-profit; exit by ATR stop or max holding. The 15m/3-ATR-target version
lost because round-trip cost was ~2x the bar ATR and the median favourable excursion (~1.4 ATR) rarely reached the
target (reports/phase-04/diagnosis). Cost-awareness then means: the bar ATR must be at least
min_atr_cost_ratio x estimated round-trip cost, otherwise no candidate.
"""

from datetime import timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from wallex_agent.contracts import FeatureSet, MarketSnapshot, SignalCandidate
from wallex_agent.contracts.hashing import content_id
from wallex_agent.features.engine import feature_map
from wallex_agent.runtime.config import C1Params, CostModel, ExecutionSettings

STRATEGY_ID = "C1_breakout"
BPS = Decimal(10000)


class C1Breakout:
    strategy_id = STRATEGY_ID

    def __init__(self, params: C1Params, costs: CostModel, execution: ExecutionSettings) -> None:
        self.p = params
        self.costs = costs
        self.execution = execution
        self.version = params.version

    def estimated_round_trip_cost_bps(self, spread_bps: Decimal | None) -> Decimal:
        fees = self.costs.fee_rate * 2 * BPS
        slip = self.costs.backtest_slippage_bps * 2
        return fees + slip + (spread_bps or Decimal(0))

    def candidates(self, snapshot: MarketSnapshot, features: FeatureSet) -> list[SignalCandidate]:
        sym = snapshot.symbol
        f = feature_map(features)
        lb = self.p.lookback_bars
        names = {n: next((k for k in f if k.startswith(f"{sym}.{n}.")), None)
                 for n in ("close", "atr", "prior_high", "breakout_margin_atr", "trend_dist_atr", "volume_ratio", "spread_bps")}
        margin_id = f"{sym}.breakout_margin_atr.{lb}"
        close, atr = f.get(f"{sym}.close.1"), f.get(names["atr"] or "")
        margin, trend = f.get(margin_id), f.get(names["trend_dist_atr"] or "")
        if close is None or atr is None or margin is None or trend is None or atr <= 0:
            return []
        if margin <= self.p.buffer_atr or trend <= 0:
            return []
        vol = f.get(names["volume_ratio"] or "")
        if self.p.min_volume_ratio is not None and (vol is None or vol < self.p.min_volume_ratio):
            return []
        ref = snapshot.book.ask if snapshot.book is not None else close
        q = Decimal(1).scaleb(-snapshot.meta.price_precision)
        stop = (ref - self.p.stop_atr * atr).quantize(q, rounding=ROUND_FLOOR)
        if stop <= 0 or stop >= ref:
            return []
        spread = f.get(names["spread_bps"] or "")
        cost = self.estimated_round_trip_cost_bps(spread)
        tp: Decimal | None = None
        if self.p.exit_mode == "TIME":
            assert self.p.min_atr_cost_ratio is not None
            if atr / ref * BPS < self.p.min_atr_cost_ratio * cost:
                return []
        else:
            if self.p.tp_atr is None:
                return []  # TARGET mode requires a fixed target so the cost check is meaningful
            tp = (ref + self.p.tp_atr * atr).quantize(q, rounding=ROUND_CEILING)
            edge = (tp - ref) / ref * BPS
            if edge < cost + self.p.min_edge_over_cost_bps:
                return []

        cid = "c1-" + content_id({"snapshot": snapshot.snapshot_id, "strategy": STRATEGY_ID, "version": self.version})[:16]
        evidence = tuple(k for k in (margin_id, names["trend_dist_atr"], names["atr"], names["volume_ratio"], names["spread_bps"])
                         if k is not None and f.get(k) is not None)
        return [SignalCandidate(
            candidate_id=cid,
            snapshot_id=snapshot.snapshot_id,
            strategy_id=STRATEGY_ID,
            strategy_version=self.version,
            symbol=sym,
            reference_price=ref,
            stop_price=stop,
            take_profit_price=tp,
            max_holding_seconds=self.p.max_holding_s,
            exit_policy_id="triple-barrier-v1" if tp is not None else "stop-time-v1",
            estimated_round_trip_cost_bps=cost.quantize(Decimal("0.01")),
            evidence_feature_ids=evidence,
            created_at=snapshot.as_of,
            expires_at=snapshot.as_of + timedelta(seconds=self.p.candidate_ttl_s),
        )]
