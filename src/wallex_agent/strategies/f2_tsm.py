"""F2 — time-series momentum with hysteresis, chandelier trail and optional regime-r2 filter (Phase 04 round 2).

Falsifiable hypothesis (reports/phase-04/round2/PREREGISTRATION_R2.md): on 4h bars, BTC/ETH TMN trends persist long
enough that holding until the trend is lost (close < EMA) or a volatility-scaled trail is hit clears a 60 bps round
trip, and beats random entries with identical exits. Selected on TRAIN 2023-02..2025-03 and validated on unseen
2025-03..2026-10-13 in the research simulator; the kernel re-expression is checked by the Experiment Harness (G10).

Entry: close >= EMA(n) + entry_band_atr x ATR and close > close n bars ago (hysteresis band vs the exit rule).
Exit: chandelier trail (stop = max(ref - stop_atr x ATR, highest high - trail_atr x ATR at entry)), the signal
close < EMA(n) at a decision close (EXIT_SIGNAL), or max holding. Regime filter: entries only in regime-r2 TREND_UP
or RANGE: 0.20 < ATR% rank < 0.95 (not QUIET / SHOCK) and (ER rank < 0.5 or close > close er_n bars ago).
Missing features fail closed (no candidate). Long-only spot; every number from config.
"""

from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal

from wallex_agent.contracts import FeatureSet, MarketSnapshot, SignalCandidate, StrategyBrief
from wallex_agent.contracts.hashing import content_id
from wallex_agent.features.engine import feature_map
from wallex_agent.runtime.config import CostModel, ExecutionSettings, F2Params, FeatureSettings

STRATEGY_ID = "F2_tsm"
BRIEF_VERSION = "f2-brief-1"
REGIME_VERSION = "regime-r2-0.1.0"
SHOCK_RANK, QUIET_RANK, TREND_RANK = Decimal("0.95"), Decimal("0.20"), Decimal("0.5")
BPS = Decimal(10000)


class F2Momentum:
    strategy_id = STRATEGY_ID

    def __init__(self, params: F2Params, costs: CostModel, execution: ExecutionSettings, features: FeatureSettings) -> None:
        if features.ema_bars is None:
            raise ValueError("F2_tsm requires features.ema_bars")
        if params.regime_filter and features.regime_er_bars is None:
            raise ValueError("F2_tsm regime_filter requires features.regime_er_bars / regime_rank_bars")
        self.p = params
        self.costs = costs
        self.execution = execution
        self.n = features.ema_bars
        self.atr_n = features.atr_bars
        self.er_n = features.regime_er_bars
        self.version = params.version

    def estimated_round_trip_cost_bps(self, spread_bps: Decimal | None) -> Decimal:
        return self.costs.fee_rate * 2 * BPS + self.costs.backtest_slippage_bps * 2 + (spread_bps or Decimal(0))

    def _regime_allowed(self, sym: str, f: dict[str, Decimal | None], close: Decimal) -> bool:
        vol, er = f.get(f"{sym}.vol_rank.{self.atr_n}"), f.get(f"{sym}.er_rank.{self.er_n}")
        lag = f.get(f"{sym}.close_lag.{self.er_n}")
        if vol is None or er is None or lag is None:
            return False
        if vol >= SHOCK_RANK or vol <= QUIET_RANK:
            return False
        return er < TREND_RANK or close > lag

    def brief(self, symbol: str, interval_s: int) -> StrategyBrief:
        """Deterministic description for the decision agent (no tradable parameter is open to the model)."""
        p, n, a = self.p, self.n, self.atr_n
        ids = [f"{symbol}.close.1", f"{symbol}.ema.{n}", f"{symbol}.close_lag.{n}", f"{symbol}.atr.{a}", f"{symbol}.spread_bps.0"]
        regime = "no regime filter"
        if p.regime_filter and self.er_n is not None:
            ids += [f"{symbol}.vol_rank.{a}", f"{symbol}.er_rank.{self.er_n}", f"{symbol}.close_lag.{self.er_n}"]
            regime = (f"regime-r2: entries only when ATR% rank vol_rank is strictly between {QUIET_RANK} and {SHOCK_RANK} "
                      f"(not QUIET/SHOCK) and either er_rank < {TREND_RANK} (RANGE) or close > close {self.er_n} bars ago "
                      "(TREND_UP). The filter mainly reduces drawdown; RANGE and a low er_rank are ALLOWED by design.")
        hours = interval_s // 3600
        return StrategyBrief(
            brief_version=BRIEF_VERSION, strategy_id=STRATEGY_ID, strategy_version=self.version,
            timeframe=f"{hours}h closed decision bars; multi-day holding (cap {p.max_holding_s // 3600}h)",
            hypothesis=("time-series momentum: BTC/ETH TMN trends persist for days; entering while the price is above "
                        "its trend and holding until the trend is lost clears the ~60 bps round trip and beat random entries "
                        "with identical exits on unseen 2025-03..2026-10 data. Most trades lose a little; a few long trends pay."),
            entry_rule=f"close >= EMA{n} + {p.entry_band_atr} x ATR{a} and close > close {n} bars ago",
            exit_rule=(f"no take-profit; initial stop = reference - {p.stop_atr} ATR; chandelier trail = highest high since entry "
                       f"- {p.trail_atr} ATR (only tightens); exit when close < EMA{n} at a decision close; time cap "
                       f"{p.max_holding_s // 3600}h. All fixed by code."),
            regime_interpretation=regime,
            evidence_feature_ids=tuple(ids),
            invalidating_evidence=(
                "data-quality problems: quality flags on the snapshot, or a missing close/EMA/close-lag/ATR/rank value "
                "(a null spread_bps only means no live book is attached; the risk engine checks the live spread)",
                f"the entry rule no longer holds on the shown values (close not >= EMA{n} + {p.entry_band_atr} ATR, or not "
                f"above close {n} bars ago)",
                f"volatility at the shock edge (vol_rank >= 0.90) together with a falling close vs {self.er_n or n} bars ago",
                "a bid/ask spread that is large relative to the estimated round-trip cost",
                "unsafe execution state: kill switch, unresolved orders or a non-ready recovery in EXECUTION_STATE"),
            not_applicable=(
                "distance to a prior N-bar high or breakout confirmation: F2 is not a breakout strategy",
                "take-profit target or target edge: F2 has no take-profit; profit comes from trailing multi-day trends",
                "volume confirmation: volume is not part of the F2 hypothesis",
                "'too extended above the average' or short-horizon mean-reversion arguments: F2 enters extended trends by design",
                "a low efficiency rank on its own: RANGE is an allowed regime"),
            candidate_reason=f"the F2 entry rule held on the closed {hours}h bar")

    def exit_signal(self, snapshot: MarketSnapshot, features: FeatureSet) -> bool:
        f = feature_map(features)
        close, ema = f.get(f"{snapshot.symbol}.close.1"), f.get(f"{snapshot.symbol}.ema.{self.n}")
        return close is not None and ema is not None and close < ema

    def candidates(self, snapshot: MarketSnapshot, features: FeatureSet) -> list[SignalCandidate]:
        sym = snapshot.symbol
        f = feature_map(features)
        atr_id, ema_id, lag_id = f"{sym}.atr.{self.atr_n}", f"{sym}.ema.{self.n}", f"{sym}.close_lag.{self.n}"
        close, atr, ema, lag = f.get(f"{sym}.close.1"), f.get(atr_id), f.get(ema_id), f.get(lag_id)
        if close is None or atr is None or atr <= 0 or ema is None or lag is None:
            return []
        if close < ema + self.p.entry_band_atr * atr or close <= lag:
            return []
        if self.p.regime_filter and not self._regime_allowed(sym, f, close):
            return []
        ref = snapshot.book.ask if snapshot.book is not None else close
        q = Decimal(1).scaleb(-snapshot.meta.price_precision)
        stop = (ref - self.p.stop_atr * atr).quantize(q, rounding=ROUND_FLOOR)
        if stop <= 0 or stop >= ref:
            return []
        spread_id = f"{sym}.spread_bps.0"
        cost = self.estimated_round_trip_cost_bps(f.get(spread_id))
        cid = "f2-" + content_id({"snapshot": snapshot.snapshot_id, "strategy": STRATEGY_ID, "version": self.version})[:16]
        ids = [ema_id, lag_id, atr_id, spread_id]
        if self.p.regime_filter:
            ids += [f"{sym}.vol_rank.{self.atr_n}", f"{sym}.er_rank.{self.er_n}", f"{sym}.close_lag.{self.er_n}"]
        evidence = tuple(k for k in ids if f.get(k) is not None)
        return [SignalCandidate(
            candidate_id=cid, snapshot_id=snapshot.snapshot_id, strategy_id=STRATEGY_ID, strategy_version=self.version,
            symbol=sym, reference_price=ref, stop_price=stop, take_profit_price=None,
            max_holding_seconds=self.p.max_holding_s, trail_distance=self.p.trail_atr * atr,
            exit_policy_id="chandelier-signal-v1", estimated_round_trip_cost_bps=cost.quantize(Decimal("0.01")),
            evidence_feature_ids=evidence, created_at=snapshot.as_of,
            expires_at=snapshot.as_of + timedelta(seconds=self.p.candidate_ttl_s))]
