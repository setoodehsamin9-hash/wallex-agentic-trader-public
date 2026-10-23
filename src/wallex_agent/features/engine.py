"""Deterministic features from CLOSED bars (no look-ahead) plus the current book.

Feature ids are stable strings "{symbol}.{name}.{window}" that the AI cites as evidence. A feature that cannot be
computed (not enough history, zero median volume, no book) has value None; it is never estimated.
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from statistics import median

from wallex_agent.contracts import FeatureSet, FeatureValue, RegimeLabel
from wallex_agent.market.bars import Bar, closed_before
from wallex_agent.market.book import OrderBook
from wallex_agent.runtime.config import FeatureSettings

FEATURE_ENGINE_VERSION = "features-1.0.0"
FEATURE_ENGINE_VERSION_R2 = "features-1.1.0"  # 1.0.0 + optional ema / close_lag / regime-r2 ranks (only when configured)


def _fid(symbol: str, name: str, window: int) -> str:
    return f"{symbol}.{name}.{window}"


def _atr(bars: Sequence[Bar], n: int) -> Decimal | None:
    if len(bars) < n + 1:
        return None
    trs: list[Decimal] = []
    for prev, cur in zip(bars[-n - 1:-1], bars[-n:]):
        trs.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    return sum(trs, Decimal(0)) / n


def _ema(closes: Sequence[Decimal], n: int) -> Decimal | None:
    if len(closes) < n:
        return None
    k = Decimal(2) / (n + 1)
    e = closes[0]
    for c in closes[1:]:
        e = c * k + e * (1 - k)
    return e


def _rank_last(values: Sequence[Decimal | None], window: int) -> Decimal | None:
    """Causal percentile rank of the last value among the previous `window` defined values (>= window // 2 needed)."""
    if not values or values[-1] is None:
        return None
    cur = values[-1]
    prev = [v for v in values[-window - 1:-1] if v is not None]
    if len(prev) < window // 2:
        return None
    return Decimal(sum(1 for v in prev if v < cur)) / len(prev)


def _regime_r2_ranks(bars: Sequence[Bar], er_n: int, atr_n: int, rank_n: int) -> tuple[Decimal | None, Decimal | None]:
    """(ER rank, ATR% rank) with rolling sums: O(len(bars)) Decimal operations per call."""
    closes = [b.close for b in bars]
    n = len(closes)
    er: list[Decimal | None] = [None] * n
    vol: list[Decimal | None] = [None] * n
    diff_sum, tr_sum = Decimal(0), Decimal(0)
    diffs = [Decimal(0)] + [abs(closes[i] - closes[i - 1]) for i in range(1, n)]
    trs = [Decimal(0)] + [max(bars[i].high - bars[i].low, abs(bars[i].high - closes[i - 1]), abs(bars[i].low - closes[i - 1]))
                          for i in range(1, n)]
    for i in range(1, n):
        diff_sum += diffs[i]
        tr_sum += trs[i]
        if i > er_n:
            diff_sum -= diffs[i - er_n]
        if i > atr_n:
            tr_sum -= trs[i - atr_n]
        if i >= er_n and diff_sum > 0:
            er[i] = abs(closes[i] - closes[i - er_n]) / diff_sum
        if i >= atr_n and closes[i] > 0:
            vol[i] = tr_sum / atr_n / closes[i]
    return _rank_last(er, rank_n), _rank_last(vol, rank_n)


def compute_features(
    symbol: str,
    snapshot_id: str,
    bars: Sequence[Bar],
    as_of: datetime,
    interval_s: int,
    book: OrderBook | None,
    settings: FeatureSettings,
    lookback_bars: int,
) -> FeatureSet:
    closed = closed_before(bars, as_of, interval_s)
    s = settings
    vals: dict[str, tuple[str, Decimal | None, str, int]] = {}

    def put(name: str, window: int, value: Decimal | None, unit: str) -> None:
        vals[_fid(symbol, name, window)] = (name, value, unit, window)

    close = closed[-1].close if closed else None
    put("close", 1, close, "TMN")
    atr = _atr(closed, s.atr_bars)
    put("atr", s.atr_bars, atr, "TMN")
    atr_pct = atr / close if atr is not None and close else None
    put("atr_pct", s.atr_bars, atr_pct, "fraction")

    sma = sum((b.close for b in closed[-s.trend_bars:]), Decimal(0)) / s.trend_bars if len(closed) >= s.trend_bars else None
    put("sma", s.trend_bars, sma, "TMN")
    trend_dist = (close - sma) / atr if close is not None and sma is not None and atr else None
    put("trend_dist_atr", s.trend_bars, trend_dist, "ATR")
    ret = (close / closed[-s.trend_bars].close - 1) if close is not None and len(closed) >= s.trend_bars else None
    put("return", s.trend_bars, ret, "fraction")

    prior = closed[-lookback_bars - 1:-1] if len(closed) >= lookback_bars + 1 else []
    prior_high = max(b.high for b in prior) if prior else None
    put("prior_high", lookback_bars, prior_high, "TMN")
    margin = (close - prior_high) / atr if close is not None and prior_high is not None and atr else None
    put("breakout_margin_atr", lookback_bars, margin, "ATR")

    vol_ratio: Decimal | None = None
    if len(closed) >= s.volume_bars + 1:
        med = Decimal(str(median(b.volume for b in closed[-s.volume_bars - 1:-1])))
        vol_ratio = closed[-1].volume / med if med > 0 else None
    put("volume_ratio", s.volume_bars, vol_ratio, "x median")

    put("spread_bps", 0, book.spread_bps() if book is not None else None, "bps")

    r2 = s.ema_bars is not None or s.regime_er_bars is not None
    if s.ema_bars is not None:
        put("ema", s.ema_bars, _ema([b.close for b in closed], s.ema_bars), "TMN")
        put("close_lag", s.ema_bars, closed[-1 - s.ema_bars].close if len(closed) > s.ema_bars else None, "TMN")
    if s.regime_er_bars is not None and s.regime_rank_bars is not None:
        er_rank, vol_rank = _regime_r2_ranks(closed, s.regime_er_bars, s.atr_bars, s.regime_rank_bars)
        put("er_rank", s.regime_er_bars, er_rank, "rank")
        put("vol_rank", s.atr_bars, vol_rank, "rank")
        put("close_lag", s.regime_er_bars, closed[-1 - s.regime_er_bars].close if len(closed) > s.regime_er_bars else None,
            "TMN")

    if atr_pct is None or trend_dist is None:
        regime = RegimeLabel.UNCERTAIN
    elif atr_pct >= s.regime_shock_atr_pct:
        regime = RegimeLabel.SHOCK
    elif trend_dist >= s.regime_trend_atr:
        regime = RegimeLabel.TRENDING_UP
    elif trend_dist <= -s.regime_trend_atr:
        regime = RegimeLabel.TRENDING_DOWN
    else:
        regime = RegimeLabel.RANGE

    features = tuple(FeatureValue(feature_id=fid, name=n, value=v, unit=u, window_bars=w)
                     for fid, (n, v, u, w) in sorted(vals.items()))
    return FeatureSet(snapshot_id=snapshot_id, feature_engine_version=FEATURE_ENGINE_VERSION_R2 if r2 else FEATURE_ENGINE_VERSION,
                      features=features,
                      deterministic_regime=regime)


def feature_map(fs: FeatureSet) -> dict[str, Decimal | None]:
    return {f.feature_id: f.value for f in fs.features}
