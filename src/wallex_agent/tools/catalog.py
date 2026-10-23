"""Catalog of the 52 tools advertised by the official Wallex MCP server (tools/list, 2026-10-22),
classified by category and risk, with an explicit default-deny exposure rule.

Wallex tools carry no MCP safety annotations, so nothing here is inferred from the server; every
entry is a reviewed decision (docs/WALLEX_TOOL_MAP.md). A tool missing from this catalog is denied
to every consumer, and `tests/test_tool_catalog.py` fails until it is classified.

Hard invariant: no LLM-facing consumer is ever exposed to a state-changing or dangerous tool.
"""

from dataclasses import dataclass
from enum import StrEnum

from wallex_agent.contracts import Environment

CATALOG_VERSION = "wallex-mcp-tools-2026-10-22"


class ToolCategory(StrEnum):
    MARKET_READ = "MARKET_READ"
    ACCOUNT_READ = "ACCOUNT_READ"
    ORDER_PREPARATION = "ORDER_PREPARATION"
    EXECUTION = "EXECUTION"
    POSITION_MANAGEMENT = "POSITION_MANAGEMENT"
    PERFORMANCE = "PERFORMANCE"
    OTHER = "OTHER"


class ToolRisk(StrEnum):
    SAFE_READ = "SAFE_READ"
    """Public market/reference data. No key, no account data."""
    PRIVATE_READ = "PRIVATE_READ"
    """Account-scoped read; needs X-API-Key. Kept out of prompts; only deterministic adapters call it."""
    WRITE_LOW = "WRITE_LOW"
    """State-changing order lifecycle that adds no exposure by itself (cancel). Executor only."""
    TRADE_EXECUTION = "TRADE_EXECUTION"
    """Places spot orders. Executor only, behind the risk engine."""
    DANGEROUS = "DANGEROUS"
    """Moves funds, changes the account/package, uses leverage, or exposes banking/PII data. Never exposed."""
    IRRELEVANT = "IRRELEVANT"
    """Out of scope for a spot TMN challenge (margin, OTC, package shopping). Never exposed."""


class Consumer(StrEnum):
    DECISION_AGENT = "DECISION_AGENT"
    """LLM in the trading cycle."""
    ASSISTANT = "ASSISTANT"
    """LLM answering user questions. Read-only."""
    MARKET_DATA_ADAPTER = "MARKET_DATA_ADAPTER"
    ACCOUNT_ADAPTER = "ACCOUNT_ADAPTER"
    """Deterministic portfolio sync and reconciliation."""
    EXECUTION_ENGINE = "EXECUTION_ENGINE"
    """Alternative write transport to REST; see ADR-002."""


LLM_CONSUMERS = frozenset({Consumer.DECISION_AGENT, Consumer.ASSISTANT})
WRITE_RISKS = frozenset({ToolRisk.WRITE_LOW, ToolRisk.TRADE_EXECUTION})
NEVER_EXPOSED_RISKS = frozenset({ToolRisk.DANGEROUS, ToolRisk.IRRELEVANT})


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    category: ToolCategory
    risk: ToolRisk
    requires_key: bool
    consumers: frozenset[Consumer]
    note: str
    public_call_verified: bool = False
    """True only where a no-key call was actually observed to succeed."""


_A = Consumer.DECISION_AGENT
_S = Consumer.ASSISTANT
_M = Consumer.MARKET_DATA_ADAPTER
_P = Consumer.ACCOUNT_ADAPTER
_X = Consumer.EXECUTION_ENGINE
_NONE: frozenset[Consumer] = frozenset()


def _t(
    name: str,
    category: ToolCategory,
    risk: ToolRisk,
    consumers: frozenset[Consumer],
    note: str,
    *,
    requires_key: bool,
    verified: bool = False,
) -> ToolSpec:
    return ToolSpec(name, category, risk, requires_key, consumers, note, verified)


C, R = ToolCategory, ToolRisk

_SPECS: tuple[ToolSpec, ...] = (
    # --- market read (public) ---
    _t("wallex_basic_candle_history", C.MARKET_READ, R.SAFE_READ, frozenset({_A, _S, _M}),
       "OHLCV; verify real bar spacing (resolution=15 returned 1m bars in Phase 01)", requires_key=False, verified=True),
    _t("wallex_basic_get_market_depth", C.MARKET_READ, R.SAFE_READ, frozenset({_A, _S, _M}),
       "one-symbol book snapshot; not a stream", requires_key=False, verified=True),
    _t("wallex_basic_get_latest_trades", C.MARKET_READ, R.SAFE_READ, frozenset({_A, _S, _M}),
       "recent public trades", requires_key=False, verified=True),
    _t("wallex_spot_get_depth", C.MARKET_READ, R.SAFE_READ, frozenset({_M}),
       "all-market depth; large payload, adapter only", requires_key=False),
    # --- order preparation (public reference data) ---
    _t("wallex_basic_get_markets", C.ORDER_PREPARATION, R.SAFE_READ, frozenset({_S, _M}),
       "market metadata via basic API", requires_key=False),
    _t("wallex_spot_get_markets", C.ORDER_PREPARATION, R.SAFE_READ, frozenset({_S, _M}),
       "spot market metadata: precision, limits", requires_key=False),
    _t("wallex_credit_prop_package_details", C.ORDER_PREPARATION, R.SAFE_READ, frozenset({_S, _M}),
       "package 183 terms + unavailableMarkets; feeds the universe allowlist", requires_key=False, verified=True),
    # --- order preparation (private) ---
    _t("wallex_basic_get_account_fees", C.ORDER_PREPARATION, R.PRIVATE_READ, frozenset({_P}),
       "maker/taker rates for cost model", requires_key=True),
    # --- account read (private) ---
    _t("wallex_basic_get_account_balances", C.ACCOUNT_READ, R.PRIVATE_READ, frozenset({_P}),
       "balances; reconcile available/locked", requires_key=True),
    _t("wallex_credit_sub_account_balances_details", C.ACCOUNT_READ, R.PRIVATE_READ, frozenset({_P}),
       "credit sub-account balances", requires_key=True),
    _t("wallex_credit_active_sub_accounts_list", C.ACCOUNT_READ, R.PRIVATE_READ, frozenset({_P}),
       "startup check that the key is scoped to the challenge sub-account", requires_key=True),
    _t("wallex_credit_active_user_package_details", C.ACCOUNT_READ, R.PRIVATE_READ, frozenset({_P}),
       "active package state and limits (warning 1000 / liquidity 900 TMN)", requires_key=True),
    _t("wallex_credit_active_user_packages_overview", C.ACCOUNT_READ, R.PRIVATE_READ, frozenset({_P}),
       "account-wide package overview", requires_key=True),
    _t("wallex_credit_sub_account_transfer", C.ACCOUNT_READ, R.PRIVATE_READ, frozenset({_P}),
       "transfer history; audit of initial credit only", requires_key=True),
    _t("wallex_spot_get_open_order", C.ACCOUNT_READ, R.PRIVATE_READ, frozenset({_P}),
       "open orders; startup and periodic reconciliation", requires_key=True),
    _t("wallex_spot_get_orders_client", C.ACCOUNT_READ, R.PRIVATE_READ, frozenset({_P}),
       "order by client_id; resolves UNKNOWN submissions", requires_key=True),
    # --- performance (private) ---
    _t("wallex_spot_get_orders", C.PERFORMANCE, R.PRIVATE_READ, frozenset({_P}),
       "order history for reporting and reconciliation", requires_key=True),
    _t("wallex_credit_user_package_history_details", C.PERFORMANCE, R.PRIVATE_READ, frozenset({_P}),
       "package history for one sub-account", requires_key=True),
    _t("wallex_credit_user_package_history_list", C.PERFORMANCE, R.PRIVATE_READ, frozenset({_P}),
       "package history list", requires_key=True),
    # --- execution (executor only, LIVE only) ---
    _t("wallex_spot_post_account_orders", C.EXECUTION, R.TRADE_EXECUTION, frozenset({_X}),
       "schema requires only side/symbol/type; our validator enforces qty/price/precision", requires_key=True),
    _t("wallex_spot_delete_account_orders", C.EXECUTION, R.WRITE_LOW, frozenset({_X}),
       "cancel by clientOrderId in body; cancelling a protective order raises risk, so executor only",
       requires_key=True),
    _t("wallex_spot_delete_orders_client", C.EXECUTION, R.WRITE_LOW, frozenset({_X}),
       "cancel by client_id in path", requires_key=True),
    _t("wallex_otc_place_easy_trade_order", C.EXECUTION, R.DANGEROUS, _NONE,
       "OTC execution outside the spot book and allowlist path", requires_key=True),
    # --- position management (margin: leverage is out of scope) ---
    _t("wallex_margin_open_position", C.POSITION_MANAGEMENT, R.DANGEROUS, _NONE, "leverage", requires_key=True),
    _t("wallex_margin_close_position_id", C.POSITION_MANAGEMENT, R.DANGEROUS, _NONE, "margin write", requires_key=True),
    _t("wallex_margin_edit_colateral", C.POSITION_MANAGEMENT, R.DANGEROUS, _NONE, "margin write", requires_key=True),
    _t("wallex_margin_edit_sltp", C.POSITION_MANAGEMENT, R.DANGEROUS, _NONE, "margin write", requires_key=True),
    _t("wallex_margin_calculate_loan", C.POSITION_MANAGEMENT, R.IRRELEVANT, _NONE, "margin preview", requires_key=True),
    _t("wallex_margin_detailed_information", C.POSITION_MANAGEMENT, R.IRRELEVANT, _NONE, "margin dry-run", requires_key=True),
    _t("wallex_margin_get_orders", C.POSITION_MANAGEMENT, R.IRRELEVANT, _NONE, "margin read", requires_key=True),
    _t("wallex_margin_get_position_id", C.POSITION_MANAGEMENT, R.IRRELEVANT, _NONE, "margin read", requires_key=True),
    _t("wallex_margin_get_positions", C.POSITION_MANAGEMENT, R.IRRELEVANT, _NONE, "margin read", requires_key=True),
    _t("wallex_margin_get_levels", C.ACCOUNT_READ, R.IRRELEVANT, _NONE, "margin level", requires_key=True),
    _t("wallex_margin_get_levels_market", C.ACCOUNT_READ, R.IRRELEVANT, _NONE, "margin level", requires_key=True),
    _t("wallex_margin_get_pnl", C.PERFORMANCE, R.IRRELEVANT, _NONE, "margin P&L", requires_key=True),
    _t("wallex_margin_profit_position_id", C.PERFORMANCE, R.IRRELEVANT, _NONE, "margin P&L preview", requires_key=True),
    _t("wallex_margin_get_markets", C.MARKET_READ, R.IRRELEVANT, _NONE, "margin markets", requires_key=False),
    _t("wallex_margin_public_ratio", C.MARKET_READ, R.IRRELEVANT, _NONE,
       "margin long/short ratio; possible future feature, not in v1", requires_key=False),
    # --- OTC reads ---
    _t("wallex_otc_get_otc_price", C.MARKET_READ, R.IRRELEVANT, _NONE, "OTC quote", requires_key=False),
    _t("wallex_otc_get_otcmarkets", C.MARKET_READ, R.IRRELEVANT, _NONE, "OTC markets", requires_key=False),
    # --- other: account / package / funds administration ---
    _t("wallex_basic_add_account_iban", C.OTHER, R.DANGEROUS, _NONE, "adds bank account", requires_key=True),
    _t("wallex_basic_get_account_card_numbers", C.OTHER, R.DANGEROUS, _NONE, "bank card PII", requires_key=True),
    _t("wallex_basic_get_account_ibans", C.OTHER, R.DANGEROUS, _NONE, "IBAN PII", requires_key=True),
    _t("wallex_basic_get_account_wallet_by_symbol", C.OTHER, R.DANGEROUS, _NONE,
       "deposit address; funds-movement surface", requires_key=True),
    _t("wallex_credit_activate_user_package", C.OTHER, R.DANGEROUS, _NONE, "package change", requires_key=True),
    _t("wallex_credit_cancel_user_package", C.OTHER, R.DANGEROUS, _NONE,
       "cancels the challenge package", requires_key=True),
    _t("wallex_credit_create_transfer_to_sub_account", C.OTHER, R.DANGEROUS, _NONE, "moves funds", requires_key=True),
    _t("wallex_credit_disable_connect_to_wallet", C.OTHER, R.DANGEROUS, _NONE, "package wallet change", requires_key=True),
    _t("wallex_credit_enable_connect_to_wallet", C.OTHER, R.DANGEROUS, _NONE, "package wallet change", requires_key=True),
    _t("wallex_credit_enable_user_package_renew", C.OTHER, R.DANGEROUS, _NONE, "package renewal", requires_key=True),
    _t("wallex_credit_check_prop_package_availability", C.OTHER, R.IRRELEVANT, _NONE,
       "activation eligibility", requires_key=True),
    _t("wallex_credit_prop_packages_list", C.OTHER, R.IRRELEVANT, _NONE,
       "package shopping; schema has an empty-named property", requires_key=False),
)

CATALOG: dict[str, ToolSpec] = {spec.name: spec for spec in _SPECS}


class UnknownToolError(LookupError):
    """Default deny: a tool that has not been reviewed and classified may not be called."""


def spec_for(name: str) -> ToolSpec:
    try:
        return CATALOG[name]
    except KeyError:
        raise UnknownToolError(f"tool {name!r} is not in catalog {CATALOG_VERSION}; denied") from None


def exposed_tools(consumer: Consumer, environment: Environment) -> frozenset[str]:
    """The exact set of Wallex tool names `consumer` may see in `environment`. Everything else is denied.

    BACKTEST gives LLM consumers no Wallex tools: live data would leak the present into a
    historical decision. Historical drill-down is served by internal tools over the frozen dataset.
    """
    if consumer in LLM_CONSUMERS and environment is Environment.BACKTEST:
        return frozenset()
    if consumer is Consumer.EXECUTION_ENGINE and environment is not Environment.LIVE:
        return frozenset()
    if consumer is Consumer.ACCOUNT_ADAPTER and environment is Environment.BACKTEST:
        return frozenset()
    return frozenset(spec.name for spec in _SPECS if consumer in spec.consumers)


def invariant_violations() -> list[str]:
    """Empty list means the catalog is safe. Checked by tests and at startup."""
    problems: list[str] = []
    for spec in _SPECS:
        llm = spec.consumers & LLM_CONSUMERS
        if llm and spec.risk is not ToolRisk.SAFE_READ:
            problems.append(f"{spec.name}: {spec.risk} exposed to LLM consumer(s) {sorted(llm)}")
        if spec.risk in NEVER_EXPOSED_RISKS and spec.consumers:
            problems.append(f"{spec.name}: {spec.risk} must have no consumers")
        if Consumer.EXECUTION_ENGINE in spec.consumers and spec.risk not in WRITE_RISKS:
            problems.append(f"{spec.name}: executor should only hold write tools")
        if spec.risk in WRITE_RISKS and spec.consumers - {Consumer.EXECUTION_ENGINE}:
            problems.append(f"{spec.name}: write tool exposed beyond the execution engine")
    if len(CATALOG) != len(_SPECS):
        problems.append("duplicate tool names in catalog")
    return problems
