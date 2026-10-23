"""C2 — trend-hold (time-series momentum with a fixed holding horizon), Phase 04.

Falsifiable hypothesis (reports/phase-04/STRATEGY_DESIGN_FA.md): at multi-hour horizons BTC/ETH TMN returns are not
mean-reverting (4h variance ratios ~1.0-1.16 vs 0.7 at 15m) and an established up-trend preceded larger 12h forward
returns than random entry, so holding through a fixed horizon while the trend is up can clear round-trip costs with
low turnover. Not assumed profitable; the experiment measures it against a DRIFT CONTROL (same exits, no trend gate).

Entry: close >= entry_trend_atr x ATR above SMA(trend_bars) AND trend_bars return > 0 (both existing features).
Exit: ATR stop fixed at entry, or max holding; no take-profit. Cost-awareness: ATR >= min_atr_cost_ratio x round-trip
cost. trend_gate = false disables the trend gate (drift control only). Long-only spot; all numbers from config.
"""

from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal

from wallex_agent.contracts import FeatureSet, MarketSnapshot, SignalCandidate
from wallex_agent.contracts.hashing import content_id
from wallex_agent.features.engine import feature_map
from wallex_agent.runtime.config import C2Params, CostModel, ExecutionSettings

STRATEGY_ID = "C2_trend_hold"
BPS = Decimal(10000)


class C2TrendHold:
    strategy_id = STRATEGY_ID

    def __init__(self, params: C2Params, costs: CostModel, execution: ExecutionSettings, *, trend_bars: int) -> None:
        self.p = params
        self.costs = costs
        self.execution = execution
        self.trend_bars = trend_bars
        self.version = params.version

    def estimated_round_trip_cost_bps(self, spread_bps: Decimal | None) -> Decimal:
        return self.costs.fee_rate * 2 * BPS + self.costs.backtest_slippage_bps * 2 + (spread_bps or Decimal(0))

    def candidates(self, snapshot: MarketSnapshot, features: FeatureSet) -> list[SignalCandidate]:
        sym = snapshot.symbol
        f = feature_map(features)
        atr_id = next((k for k in f if k.startswith(f"{sym}.atr.")), None)
        trend_id, ret_id = f"{sym}.trend_dist_atr.{self.trend_bars}", f"{sym}.return.{self.trend_bars}"
        spread_id = f"{sym}.spread_bps.0"
        close, atr = f.get(f"{sym}.close.1"), f.get(atr_id or "")
        trend, ret = f.get(trend_id), f.get(ret_id)
        if close is None or atr is None or atr <= 0 or trend is None or ret is None:
            return []
        if self.p.trend_gate and (self.p.entry_trend_atr is None or trend < self.p.entry_trend_atr or ret <= 0):
            return []
        ref = snapshot.book.ask if snapshot.book is not None else close
        cost = self.estimated_round_trip_cost_bps(f.get(spread_id))
        if atr / ref * BPS < self.p.min_atr_cost_ratio * cost:
            return []
        q = Decimal(1).scaleb(-snapshot.meta.price_precision)
        stop = (ref - self.p.stop_atr * atr).quantize(q, rounding=ROUND_FLOOR)
        if stop <= 0 or stop >= ref:
            return []
        cid = "c2-" + content_id({"snapshot": snapshot.snapshot_id, "strategy": STRATEGY_ID, "version": self.version})[:16]
        evidence = tuple(k for k in (trend_id, ret_id, atr_id, spread_id) if k is not None and f.get(k) is not None)
        return [SignalCandidate(
            candidate_id=cid, snapshot_id=snapshot.snapshot_id, strategy_id=STRATEGY_ID, strategy_version=self.version,
            symbol=sym, reference_price=ref, stop_price=stop, take_profit_price=None,
            max_holding_seconds=self.p.max_holding_s, exit_policy_id="stop-time-v1",
            estimated_round_trip_cost_bps=cost.quantize(Decimal("0.01")), evidence_feature_ids=evidence,
            created_at=snapshot.as_of, expires_at=snapshot.as_of + timedelta(seconds=self.p.candidate_ttl_s))]
