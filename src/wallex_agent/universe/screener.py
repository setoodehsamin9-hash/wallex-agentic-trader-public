"""Market screener: rank ELIGIBLE spot TMN markets for the owner to confirm. Read-only, public data, no model.

Reimplementation of concept (no upstream code): Freqtrade's pairlist chain (GPL-3.0) — VolumePairList (rank by 24h
quote volume), AgeFilter (listing age), SpreadFilter (bid/ask spread), RangeStabilityFilter (high/low range band) and
PrecisionFilter (price step too coarse for the stop), read in its documentation on 2026-10-23. Every threshold here is
ours, derived from this project's cost model, risk limits and the active strategy's warm-up history; none is a
Freqtrade default.

The screener never changes a run. Its result is a proposal: a symbol trades only after the owner confirms it
(`eligible_symbols_confirmed`), it is added to `[run].symbols`, and the strategy has been backtested on it. Ineligible
symbols (`eligible = spot ∩ metadata − package_183 − restrictions`) are never ranked. Missing data fails closed.
"""

import math
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import Field

from wallex_agent.contracts import Contract, UtcDatetime
from wallex_agent.runtime.config import RunConfig
from wallex_agent.strategies import strategy_history

SCREEN_VERSION = "screen-1"
PARTICIPATION = Decimal("0.01")
"""Ours: one full-size position may be at most 1% of the market's 24h quote volume."""
SHOCK_RANGE_PCT = Decimal(25)
"""Ours: a wider 24h high/low range is treated as a shock day, not a tradable trend."""
MAX_TICK_BPS = Decimal(10)
"""Ours: one price step above 10 bps distorts limit prices and stops (PrecisionFilter concept)."""
COST_MULTIPLE = Decimal("1.5")
"""Same economics gate the strategy research used: gross move must be >= 1.5x the round-trip cost."""


class ScreenSettings(Contract):
    version: str = SCREEN_VERSION
    min_quote_volume_tmn: Decimal = Field(ge=0)
    min_age_days: int = Field(ge=0)
    max_spread_bps: Decimal = Field(gt=0)
    min_range_pct: Decimal = Field(ge=0)
    max_range_pct: Decimal = Field(gt=0)
    max_tick_bps: Decimal = Field(gt=0)
    top_n: int = Field(gt=0)


class ScreenRow(Contract):
    symbol: str
    passed: bool
    reasons: tuple[str, ...]
    quote_volume_24h_tmn: Decimal | None
    range_24h_pct: Decimal | None
    change_24h_pct: Decimal | None
    spread_bps: Decimal | None
    age_days: int | None
    zero_fee_flag: bool
    rank: int | None = None


class ScreenReport(Contract):
    version: str
    as_of: UtcDatetime
    settings: ScreenSettings
    eligible_count: int
    rows: tuple[ScreenRow, ...]
    selected: tuple[str, ...]
    note: str = ("proposal only: a symbol trades after owner confirmation, [run].symbols and a backtest of the strategy on it")


def settings_for(cfg: RunConfig, *, top_n: int = 10) -> ScreenSettings:
    """Thresholds derived from THIS run's own config (fail closed when a needed limit is missing)."""
    risk = cfg.limits.risk
    if risk.max_position_notional_tmn is None or risk.max_spread_bps is None or cfg.strategy is None or cfg.features is None:
        raise ValueError("screener needs limits.risk.max_position_notional_tmn, limits.risk.max_spread_bps, strategy and features")
    round_trip_bps = 2 * (cfg.costs.fee_rate * 10000 + cfg.costs.backtest_slippage_bps)
    history_s = strategy_history(cfg.strategy, cfg.features) * cfg.run.bar_interval_seconds
    return ScreenSettings(min_quote_volume_tmn=risk.max_position_notional_tmn / PARTICIPATION,
                          min_age_days=math.ceil(history_s / 86400), max_spread_bps=risk.max_spread_bps,
                          min_range_pct=round_trip_bps * COST_MULTIPLE / 100, max_range_pct=SHOCK_RANGE_PCT,
                          max_tick_bps=MAX_TICK_BPS, top_n=top_n)


def _num(v: Any) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _row(m: Mapping[str, Any], *, now: datetime, s: ScreenSettings, spread: Decimal | None, spread_checked: bool) -> ScreenRow:
    reasons: list[str] = []
    qv, hi, lo, price = (_num(m.get(k)) for k in ("quote_volume_24h", "highest_24h_price", "lowest_24h_price", "price"))
    rng = (hi - lo) / lo * 100 if hi is not None and lo is not None and lo > 0 else None
    created = m.get("created_at")
    try:
        age = (now - datetime.fromisoformat(str(created))).days if created else None
    except (ValueError, TypeError):  # unparsable or timezone-naive listing date: unknown age fails closed
        age = None
    if qv is None or qv < s.min_quote_volume_tmn:
        reasons.append("LOW_VOLUME")
    if age is None or age < s.min_age_days or m.get("is_new") or m.get("spot_is_new"):
        reasons.append("TOO_NEW_FOR_WARMUP")
    if rng is None:
        reasons.append("RANGE_UNKNOWN")
    elif rng < s.min_range_pct:
        reasons.append("RANGE_BELOW_COST_HURDLE")
    elif rng > s.max_range_pct:
        reasons.append("RANGE_SHOCK")
    precision = m.get("price_precision")
    if price is None or price <= 0 or not isinstance(precision, int):
        reasons.append("PRICE_STEP_UNKNOWN")
    elif Decimal(10) ** -precision / price * 10000 > s.max_tick_bps:
        reasons.append("PRICE_STEP_TOO_COARSE")
    if not m.get("is_market_type_enable", False):
        reasons.append("MARKET_ORDERS_DISABLED")
    if not spread_checked:
        reasons.append("SPREAD_UNCHECKED")
    elif spread is None:
        reasons.append("SPREAD_UNKNOWN")
    elif spread > s.max_spread_bps:
        reasons.append("WIDE_SPREAD")
    return ScreenRow(symbol=str(m["symbol"]), passed=not reasons, reasons=tuple(reasons), quote_volume_24h_tmn=qv,
                     range_24h_pct=None if rng is None else rng.quantize(Decimal("0.01")), change_24h_pct=_num(m.get("change_24h")),
                     spread_bps=None if spread is None else spread.quantize(Decimal("0.1")), age_days=age,
                     zero_fee_flag=bool(m.get("is_zero_fee")))


def screen(hector_markets: Mapping[str, Any], eligible: frozenset[str], *, now: datetime, settings: ScreenSettings,
           spreads: Mapping[str, Decimal | None] | None = None) -> ScreenReport:
    """Pure: the same inputs give the same report. `spreads=None` means the spread step was not run (nothing passes)."""
    markets = [m for m in hector_markets["result"]["markets"]
               if m.get("is_spot") and m.get("quote_asset") == "TMN" and m.get("symbol") in eligible]
    rows = [_row(m, now=now, s=settings, spread=(spreads or {}).get(str(m["symbol"])),
                 spread_checked=spreads is not None and str(m["symbol"]) in spreads) for m in markets]
    passed = sorted((r for r in rows if r.passed), key=lambda r: (-(r.quote_volume_24h_tmn or 0), r.symbol))
    ranked = {r.symbol: i + 1 for i, r in enumerate(passed)}
    rows = [r.model_copy(update={"rank": ranked.get(r.symbol)}) for r in rows]
    rows.sort(key=lambda r: (r.rank is None, r.rank or 0, -(r.quote_volume_24h_tmn or 0), r.symbol))
    return ScreenReport(version=SCREEN_VERSION, as_of=now, settings=settings, eligible_count=len(markets), rows=tuple(rows),
                        selected=tuple(r.symbol for r in passed[:settings.top_n]))


def spread_candidates(report: ScreenReport, *, limit: int) -> list[str]:
    """Symbols worth one public depth read: everything that failed ONLY because the spread was not checked yet."""
    pending = [r for r in report.rows if r.reasons == ("SPREAD_UNCHECKED",)]
    return [r.symbol for r in sorted(pending, key=lambda r: -(r.quote_volume_24h_tmn or 0))[:limit]]


def render_fa(report: ScreenReport) -> str:
    s = report.settings
    lines = [f"غربال بازار {report.version} · {report.as_of.isoformat()} · بازار مجاز اسپات تومانی: {report.eligible_count}",
             f"آستانه‌ها (از تنظیمات خود پروژه): حجم ۲۴ساعته ≥ {s.min_quote_volume_tmn:,.0f} تومان · سن ≥ {s.min_age_days} روز "
             f"(گرم‌شدن استراتژی) · اسپرد ≤ {s.max_spread_bps} bps · دامنهٔ ۲۴ساعته بین {s.min_range_pct:.2f}٪ و {s.max_range_pct}٪",
             "رتبه | نماد | حجم ۲۴ساعته (تومان) | دامنه٪ | تغییر٪ | اسپرد bps | سن روز | بدون‌کارمزد | دلیل رد"]
    for r in report.rows:
        lines.append(f"{r.rank or '-'} | {r.symbol} | {r.quote_volume_24h_tmn or 0:,.0f} | {r.range_24h_pct} | {r.change_24h_pct} | "
                     f"{r.spread_bps} | {r.age_days} | {'بله' if r.zero_fee_flag else 'خیر'} | {', '.join(r.reasons) or '—'}")
    lines.append(f"پیشنهاد (فقط پیشنهاد؛ بدون تأیید مالک و بک‌تست معامله نمی‌شود): {', '.join(report.selected) or '—'}")
    return "\n".join(lines) + "\n"
