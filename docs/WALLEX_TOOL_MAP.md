# Wallex MCP tool map

**Source:** official server `https://api.wallex.ir/mcp` (Streamable HTTP; `serverInfo` `api.wallex.ir` 1.0.0, protocol
`2025-03-26`), unauthenticated `tools/list` on 2026-10-22 → **52 tools**, no `nextCursor`, `listChanged=false`.
Captured: `research/phase-02/evidence/mcp_tools_list_2026-10-22.json`. Input shapes are identical to the Phase 01
capture; only descriptions were added since.

**Authoritative in code:** `src/wallex_agent/tools/catalog.py` (this table is generated from it). Drift check:
`uv run python scripts/inspect_wallex_mcp.py` (initialize + tools/list only; exits 1 on any new/removed tool).
**Tests:** `tests/test_tool_catalog.py`.

## What was and was not done in Phase 02

- Done (public, no key): `initialize`, `tools/list`, and `tools/call` on four public reads —
  `wallex_basic_get_market_depth{symbol:BTCTMN}`, `wallex_basic_get_latest_trades{symbol:BTCTMN}`,
  `wallex_basic_candle_history{BTCTMN, resolution:"5"}` and `{…, resolution:"1"}`. Phase 01 already verified
  `wallex_credit_prop_package_details{id:"183"}`. Responses saved under `research/phase-02/evidence/`.
- **Not done:** no private tool, no order, no cancel, no transfer/package/account change, no API key requested or used.

## Observations that shape the adapters

1. **No tool carries MCP annotations** (`readOnlyHint`/`destructiveHint` absent). 15 descriptions start with
   `STATE-CHANGING: requires explicit user confirmation`. Exposure is therefore an explicit reviewed allowlist.
2. **`isError=false` does not mean success.** `candle_history` with `resolution:"5"` returned `isError=false` and
   payload `{"errmsg":"Invalid resolution!","s":"error"}` although the description lists 5 as an example.
   Adapters must check the payload status (`s == "ok"` for candles).
3. `resolution:"1"` returned 30 bars at 60 s spacing for a 30-minute window; the **last bar was still forming**
   (opened 35 s before the request). The adapter drops the forming bar. Phase 01 also saw `resolution=15`
   return 1-minute spacing via REST; build larger bars from verified 1m.
4. Depth returns prices/quantities as **JSON numbers**; candles return strings. Parse everything to `Decimal`
   (`json.loads(..., parse_float=Decimal)`), never float.
5. Order placement schema (`wallex_spot_post_account_orders`): all fields `string`, `additionalProperties:false`,
   `required: [side, symbol, type]` — `quantity`, `price`, `client_id` are optional in the schema and `side`/`type`
   have no enum. Note the casing `stop_Price`. Our domain validator, not the schema, guarantees a valid order.
6. Two cancel tools with different id placement: `wallex_spot_delete_account_orders{clientOrderId}` (body) and
   `wallex_spot_delete_orders_client{client_id}` (path).
7. `wallex_credit_prop_packages_list` advertises a property with an **empty name** (`""`); not used.
8. Response headers showed `x-ratelimit-limit: 5` on the MCP endpoint for unauthenticated calls; the window is not
   documented. Treat MCP as low-rate drill-down, not a feed. Market feed = REST/WS v2.
9. WebSocket and withdrawal are not available through MCP (official `llm.txt`).

## Risk classes

| Class | Meaning | Who may call |
|---|---|---|
| `SAFE_READ` | Public market/reference data | LLM roles (subset), market-data adapter |
| `PRIVATE_READ` | Account-scoped read, needs `X-API-Key` | Deterministic account adapter only — keeps account data out of prompts |
| `WRITE_LOW` | State-changing lifecycle that adds no exposure by itself (cancel) | Execution engine, LIVE only |
| `TRADE_EXECUTION` | Places spot orders | Execution engine, LIVE only, after risk verdict |
| `DANGEROUS` | Moves funds, changes account/package, leverage, banking PII | Nobody |
| `IRRELEVANT` | Out of scope for spot TMN challenge (margin, OTC, package shopping) | Nobody |

Cancel is `WRITE_LOW`, not harmless: cancelling a protective sell increases risk, so it stays executor-only.

## Full inventory

| # | Tool | Category | Risk | Key | Consumers | Verified no-key call | Note |
|---|---|---|---|---|---|---|---|
| 1 | `wallex_margin_get_markets` | MARKET_READ | **IRRELEVANT** | no | — (denied) | — | margin markets |
| 2 | `wallex_margin_public_ratio` | MARKET_READ | **IRRELEVANT** | no | — (denied) | — | margin long/short ratio; possible future feature, not in v1 |
| 3 | `wallex_otc_get_otc_price` | MARKET_READ | **IRRELEVANT** | no | — (denied) | — | OTC quote |
| 4 | `wallex_otc_get_otcmarkets` | MARKET_READ | **IRRELEVANT** | no | — (denied) | — | OTC markets |
| 5 | `wallex_basic_candle_history` | MARKET_READ | **SAFE_READ** | no | ASSISTANT, DECISION_AGENT, MARKET_DATA_ADAPTER | yes | OHLCV; verify real bar spacing (resolution=15 returned 1m bars in Phase 01) |
| 6 | `wallex_basic_get_latest_trades` | MARKET_READ | **SAFE_READ** | no | ASSISTANT, DECISION_AGENT, MARKET_DATA_ADAPTER | yes | recent public trades |
| 7 | `wallex_basic_get_market_depth` | MARKET_READ | **SAFE_READ** | no | ASSISTANT, DECISION_AGENT, MARKET_DATA_ADAPTER | yes | one-symbol book snapshot; not a stream |
| 8 | `wallex_spot_get_depth` | MARKET_READ | **SAFE_READ** | no | MARKET_DATA_ADAPTER | — | all-market depth; large payload, adapter only |
| 9 | `wallex_margin_get_levels` | ACCOUNT_READ | **IRRELEVANT** | yes | — (denied) | — | margin level |
| 10 | `wallex_margin_get_levels_market` | ACCOUNT_READ | **IRRELEVANT** | yes | — (denied) | — | margin level |
| 11 | `wallex_basic_get_account_balances` | ACCOUNT_READ | **PRIVATE_READ** | yes | ACCOUNT_ADAPTER | — | balances; reconcile available/locked |
| 12 | `wallex_credit_active_sub_accounts_list` | ACCOUNT_READ | **PRIVATE_READ** | yes | ACCOUNT_ADAPTER | — | startup check that the key is scoped to the challenge sub-account |
| 13 | `wallex_credit_active_user_package_details` | ACCOUNT_READ | **PRIVATE_READ** | yes | ACCOUNT_ADAPTER | — | active package state and limits (warning 1000 / liquidity 900 TMN) |
| 14 | `wallex_credit_active_user_packages_overview` | ACCOUNT_READ | **PRIVATE_READ** | yes | ACCOUNT_ADAPTER | — | account-wide package overview |
| 15 | `wallex_credit_sub_account_balances_details` | ACCOUNT_READ | **PRIVATE_READ** | yes | ACCOUNT_ADAPTER | — | credit sub-account balances |
| 16 | `wallex_credit_sub_account_transfer` | ACCOUNT_READ | **PRIVATE_READ** | yes | ACCOUNT_ADAPTER | — | transfer history; audit of initial credit only |
| 17 | `wallex_spot_get_open_order` | ACCOUNT_READ | **PRIVATE_READ** | yes | ACCOUNT_ADAPTER | — | open orders; startup and periodic reconciliation |
| 18 | `wallex_spot_get_orders_client` | ACCOUNT_READ | **PRIVATE_READ** | yes | ACCOUNT_ADAPTER | — | order by client_id; resolves UNKNOWN submissions |
| 19 | `wallex_basic_get_account_fees` | ORDER_PREPARATION | **PRIVATE_READ** | yes | ACCOUNT_ADAPTER | — | maker/taker rates for cost model |
| 20 | `wallex_basic_get_markets` | ORDER_PREPARATION | **SAFE_READ** | no | ASSISTANT, MARKET_DATA_ADAPTER | — | market metadata via basic API |
| 21 | `wallex_credit_prop_package_details` | ORDER_PREPARATION | **SAFE_READ** | no | ASSISTANT, MARKET_DATA_ADAPTER | yes | package 183 terms + unavailableMarkets; feeds the universe allowlist |
| 22 | `wallex_spot_get_markets` | ORDER_PREPARATION | **SAFE_READ** | no | ASSISTANT, MARKET_DATA_ADAPTER | — | spot market metadata: precision, limits |
| 23 | `wallex_otc_place_easy_trade_order` | EXECUTION | **DANGEROUS** | yes | — (denied) | — | OTC execution outside the spot book and allowlist path |
| 24 | `wallex_spot_post_account_orders` | EXECUTION | **TRADE_EXECUTION** | yes | EXECUTION_ENGINE | — | schema requires only side/symbol/type; our validator enforces qty/price/precision |
| 25 | `wallex_spot_delete_account_orders` | EXECUTION | **WRITE_LOW** | yes | EXECUTION_ENGINE | — | cancel by clientOrderId in body; cancelling a protective order raises risk, so executor only |
| 26 | `wallex_spot_delete_orders_client` | EXECUTION | **WRITE_LOW** | yes | EXECUTION_ENGINE | — | cancel by client_id in path |
| 27 | `wallex_margin_close_position_id` | POSITION_MANAGEMENT | **DANGEROUS** | yes | — (denied) | — | margin write |
| 28 | `wallex_margin_edit_colateral` | POSITION_MANAGEMENT | **DANGEROUS** | yes | — (denied) | — | margin write |
| 29 | `wallex_margin_edit_sltp` | POSITION_MANAGEMENT | **DANGEROUS** | yes | — (denied) | — | margin write |
| 30 | `wallex_margin_open_position` | POSITION_MANAGEMENT | **DANGEROUS** | yes | — (denied) | — | leverage |
| 31 | `wallex_margin_calculate_loan` | POSITION_MANAGEMENT | **IRRELEVANT** | yes | — (denied) | — | margin preview |
| 32 | `wallex_margin_detailed_information` | POSITION_MANAGEMENT | **IRRELEVANT** | yes | — (denied) | — | margin dry-run |
| 33 | `wallex_margin_get_orders` | POSITION_MANAGEMENT | **IRRELEVANT** | yes | — (denied) | — | margin read |
| 34 | `wallex_margin_get_position_id` | POSITION_MANAGEMENT | **IRRELEVANT** | yes | — (denied) | — | margin read |
| 35 | `wallex_margin_get_positions` | POSITION_MANAGEMENT | **IRRELEVANT** | yes | — (denied) | — | margin read |
| 36 | `wallex_margin_get_pnl` | PERFORMANCE | **IRRELEVANT** | yes | — (denied) | — | margin P&L |
| 37 | `wallex_margin_profit_position_id` | PERFORMANCE | **IRRELEVANT** | yes | — (denied) | — | margin P&L preview |
| 38 | `wallex_credit_user_package_history_details` | PERFORMANCE | **PRIVATE_READ** | yes | ACCOUNT_ADAPTER | — | package history for one sub-account |
| 39 | `wallex_credit_user_package_history_list` | PERFORMANCE | **PRIVATE_READ** | yes | ACCOUNT_ADAPTER | — | package history list |
| 40 | `wallex_spot_get_orders` | PERFORMANCE | **PRIVATE_READ** | yes | ACCOUNT_ADAPTER | — | order history for reporting and reconciliation |
| 41 | `wallex_basic_add_account_iban` | OTHER | **DANGEROUS** | yes | — (denied) | — | adds bank account |
| 42 | `wallex_basic_get_account_card_numbers` | OTHER | **DANGEROUS** | yes | — (denied) | — | bank card PII |
| 43 | `wallex_basic_get_account_ibans` | OTHER | **DANGEROUS** | yes | — (denied) | — | IBAN PII |
| 44 | `wallex_basic_get_account_wallet_by_symbol` | OTHER | **DANGEROUS** | yes | — (denied) | — | deposit address; funds-movement surface |
| 45 | `wallex_credit_activate_user_package` | OTHER | **DANGEROUS** | yes | — (denied) | — | package change |
| 46 | `wallex_credit_cancel_user_package` | OTHER | **DANGEROUS** | yes | — (denied) | — | cancels the challenge package |
| 47 | `wallex_credit_create_transfer_to_sub_account` | OTHER | **DANGEROUS** | yes | — (denied) | — | moves funds |
| 48 | `wallex_credit_disable_connect_to_wallet` | OTHER | **DANGEROUS** | yes | — (denied) | — | package wallet change |
| 49 | `wallex_credit_enable_connect_to_wallet` | OTHER | **DANGEROUS** | yes | — (denied) | — | package wallet change |
| 50 | `wallex_credit_enable_user_package_renew` | OTHER | **DANGEROUS** | yes | — (denied) | — | package renewal |
| 51 | `wallex_credit_check_prop_package_availability` | OTHER | **IRRELEVANT** | yes | — (denied) | — | activation eligibility |
| 52 | `wallex_credit_prop_packages_list` | OTHER | **IRRELEVANT** | no | — (denied) | — | package shopping; schema has an empty-named property |

Counts by risk: {'SAFE_READ': 7, 'PRIVATE_READ': 12, 'TRADE_EXECUTION': 1, 'WRITE_LOW': 2, 'DANGEROUS': 15, 'IRRELEVANT': 15}
Counts by category: {'MARKET_READ': 8, 'ORDER_PREPARATION': 4, 'ACCOUNT_READ': 10, 'PERFORMANCE': 5, 'EXECUTION': 4, 'POSITION_MANAGEMENT': 9, 'OTHER': 12}

## Exposure summary (`uv run wallex-agent tool-exposure`)

| Consumer | BACKTEST | SHADOW | LIVE |
|---|---|---|---|
| DECISION_AGENT | 0 | 3 | 3 |
| ASSISTANT | 0 | 6 | 6 |
| MARKET_DATA_ADAPTER | 7 | 7 | 7 |
| ACCOUNT_ADAPTER | 0 | 12 | 12 |
| EXECUTION_ENGINE | 0 | 0 | 3 |

30 of 52 tools (all 15 `DANGEROUS` + 15 `IRRELEVANT`) are reachable by no consumer in any environment.

## Open questions about tools (feed Phase 03 read-only spikes)

- Private reads with the challenge key: is the key scoped to the credit sub-account; shapes of balances
  (available/locked), fees, open orders, order-by-client-id (U05, U06).
- Accepted `resolution` values for MCP `candle_history` (only `"1"` confirmed) and maximum range per call (U08).
- Whether the organiser requires orders via MCP rather than REST, and how "explicit confirmation" applies to an
  automatic executor (U04). Until answered, live writes default to REST from the deterministic executor.
