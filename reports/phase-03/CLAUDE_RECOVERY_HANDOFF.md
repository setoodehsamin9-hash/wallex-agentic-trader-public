# Claude → integration handoff — persistent restart / crash recovery

Status: **PASS** for SHADOW/BACKTEST recovery (deterministic, tested); **PARTIAL** for LIVE (interfaces + fake-venue
tests only; no private Wallex call was made or is wired).
Date: 2026-10-22 · Branch: **`phase-03/restart-recovery`** (pushed, not merged) · Base: `fbac63e` (no Codex WIP).

## Commits

| Commit | Change |
|---|---|
| `9d95d6d` | Hash-chained state log, engine recorder / ledger reservation hooks, deterministic rebuild |
| `b3148b0` | Recovery coordinator state machine, runtime `resume=` / entry gate / risk checkpoints / `prime_snapshot` |
| `ed9ef15` | `resume_shadow` (loop extracted to `_drive`), CLI `shadow --resume`, read-only `recover` |
| `492ce62` | ADR-003, DECISION_LOG, OPEN_SOURCE_DECISIONS, LIVE_RUNBOOK, ARCHITECTURE |
| `9b1ff7a` | Safety-review fixes (deferred balance gate, RECOVERY_ERROR, LIVE latch on INCOMPATIBLE, `abandon`, tz checks, unflushed trades) |
| (this report) | handoff |

## Architecture (ADR-003)

```
TradingRuntime(resume=True)   (no journaling hooks yet)
  → RecoveryCoordinator.recover(now)
      LOAD       run exists, not sealed, state_log hash chain intact            (else BLOCKED, kill latched)
      VALIDATE   schema/env/autonomy/config/limits/window/capital/code/strategy/model == manifest   (else BLOCKED)
      REBUILD    replay ORDER/FILL/STATE/RESERVE/RELEASE/RISK/CHECKPOINT into the runtime's fresh ledger;
                 verify recorded balances + digests; adopt orders; restore risk memory; attach state log
      CLASSIFY   every non-terminal order → UNKNOWN (journaled)
      RECONCILE  simulator: settle from persisted fills (FILLED / remainder CANCELED), never resubmit
                 exchange: query by client_order_id, apply new fill ids once; not found stays UNKNOWN → entry gate;
                 then balances vs kill.reconciliation_tolerance_tmn (mismatch → BLOCKED + kill)
      APPROVALS  PENDING/APPROVED tickets → EXPIRED ("invalidated by restart recovery")
      RESERVATIONS keep only non-terminal entry orders' reservations; release the rest (journaled)
      REARM      every position keeps its ExitPlan; pending (incl. UNKNOWN) SELL stays its only exit
      SCHEDULE   window/cutoff state reported (must_flatten → liquidation on first maintenance pass)
      VERIFY     cash/spendable/position/reservation invariants
  → READY | EXITS_ONLY (exits run, every cycle BLOCKED before the model) | BLOCKED (loop never starts)
```

State log = new SQLite table `state_log` in the same journal (append-only, per-run contiguous seq, sha256 chain).
Journal events appended: `RECOVERY_STARTED`, `_STATE_RECONSTRUCTED`, `_AMBIGUITY_DETECTED`,
`_RECONCILIATION_REQUIRED`, `_APPROVALS_INVALIDATED`, `_EXIT_REARMED`, `_BALANCES_RECONCILED`,
`_ORDER_RECONCILED`, `_RECONCILED`, `_READY` / `_EXITS_ONLY` / `_BLOCKED`; full report at payload ref `recovery:<time>`.

## Files changed

New: `src/wallex_agent/recovery/{__init__,state_log,rebuild,coordinator}.py`, `docs/adr/ADR-003-restart-recovery.md`,
`tests/recovery/{conftest,test_state_log,test_restart_recovery,test_shadow_resume,test_recover_cli}.py`,
`tests/support/recovery_fakes.py`, this report.
Modified (kernel): `execution/engine.py` (recorder hook, `adopt`, `mark_unknown`, `settle_simulated`),
`portfolio/ledger.py` (`on_reservation` hook), `journal/store.py` (`state_log` table, `run_ended_at`).
Modified (runtime): `runtime/engine.py` (`resume=`, `install_recovered`, risk checkpoints, `entry_gate`,
`prime_snapshot`, status fields), `runtime/shadow.py` (loop → `_drive`, `resume_shadow`, SHUTDOWN/INTERRUPTED
checkpoints), `interface/cli.py` (`shadow --resume`, read-only `recover`, audited `abandon`).
Docs: `docs/DECISION_LOG.md` (4 rows inserted after the old recovery row), `docs/OPEN_SOURCE_DECISIONS.md`
(paragraph before §3), `docs/LIVE_RUNBOOK.md` (item 3), `docs/ARCHITECTURE.md` (one table cell).

## Scenarios tested (tests/recovery, all fakes, no network/credentials)

| # | Scenario | Test |
|---|---|---|
| 1 | clean shutdown → restart → identical ledger digest, checkpoint verified | `test_clean_shutdown_then_restart_restores_identical_state` |
| 2 | crash with open position → no second entry, stop fires once | `test_crash_with_open_position_rearms_stop_and_never_double_sells` |
| 3 | crash after INTENT/SUBMITTING persisted, before submit (sim → settled CANCELED; exchange → UNKNOWN, gated, reservation kept) | `test_crash_after_intent_persisted_before_submit_simulated`, `test_crash_before_exchange_received_order_stays_unknown_and_blocks_entries` |
| 4 | exchange accepted, response lost → reconciled by client id, exactly one submit | `test_exchange_accepted_response_lost_reconciles_without_resubmit` |
| 5 | partial fill before crash, remainder while down → only new fill applied | `test_partial_fill_before_crash_applies_only_new_fills` |
| 6 | FILL persisted, terminal STATE not → not lost, not doubled | `test_fill_persisted_immediately_before_crash_is_not_lost_or_doubled` |
| 7 | resting entry keeps reservation; approval reservation released | `test_resting_entry_keeps_reservation_across_restart`, approvals test |
| 8 | kill switch latched survives | `test_kill_switch_stays_latched_after_restart` |
| 9 | loss-streak cooldown, drawdown pause, peak equity survive | `test_loss_streak_cooldown_and_drawdown_pause_survive_restart` |
| 10 | approved-but-unconsumed ticket invalidated, cannot be re-approved | `test_outstanding_approval_is_invalidated_on_restart` |
| 11 | limits / autonomy / code change → BLOCKED (no kill latch) | `test_incompatible_restart_is_blocked[*]` |
| 12 | tampered log / missing log / sealed run / balance mismatch → BLOCKED | `test_corrupt_state_log_blocks_and_latches_kill`, `test_incomplete_persisted_state_blocks`, `test_sealed_run_is_not_resumed`, `test_balance_mismatch_beyond_tolerance_blocks_and_latches`, `test_state_log.py` (hash, gap, divergence) |
| 13 | SHADOW crash → `resume_shadow` → same ledger, sealed, only GET calls, ShadowVenue | `test_shadow_crash_then_resume_continues_the_same_run`, `test_blocked_recovery_never_starts_the_loop` |
| 14 | UNKNOWN exit after restart → no second sell until reconciled, then gate clears | `test_unresolved_exit_after_restart_blocks_a_second_sell_until_reconciled` |
| — | balance check deferred until UNKNOWN orders resolve; late mismatch keeps entries gated + latches kill | `test_late_balance_mismatch_keeps_entries_gated_and_latches` |
| — | unexpected error inside recovery → journaled RECOVERY_ERROR, BLOCKED, kill | `test_unexpected_recovery_error_is_journaled_blocked_and_latched` |
| — | trade closed by a fill persisted before the crash reaches the trades table | `test_trade_closed_but_not_flushed_before_crash_reaches_the_trades_table` |
| — | BACKTEST state log replays to the final ledger; CLI `recover` read-only / missing DB; audited `abandon` | `test_backtest_state_log_replays_to_the_final_ledger`, `test_recover_cli.py` |

## Decisions

- Event-sourced state log + deterministic replay (not snapshots, not a framework cache).
- **Approvals are invalidated on restart** (safer option B).
- Simulators settle lost outcomes from the journal; a real exchange's not-found order is never assumed absent.
- Incompatible state → BLOCKED without kill; corrupt/unrebuildable/balance-mismatch → BLOCKED + kill latch.
- Sealed runs are never reopened; BACKTEST is re-run (deterministic), verified with `inspect_run`.

## OSS

No new dependency, no copied code. NautilusTrader live-reconciliation concept (docs checked 2026-10-22) reimplemented;
Hummingbot/Freqtrade reference only. Recorded in `docs/OPEN_SOURCE_DECISIONS.md`.

## Shared-interface changes that may conflict with Codex

1. `runtime/shadow.py`: everything after the `run_shadow` guard moved into `_drive(...)`; `resume_shadow` added.
   Codex's working-tree change at the `run_shadow` guard (line 83, non-SHADOW config refusal) touches different
   lines; expect a clean merge. If Codex edited the loop body, re-apply those edits inside `_drive`.
2. `runtime/engine.py`: new `resume` kwarg (default False, old behaviour); `status_summary` gains `entry_gate` and
   `recovery_outcome` keys (Codex's reporter tests read status — additive only).
3. `DecisionWait` is wrapped around `rt.decider` inside `_drive` (was the `decider` argument; same object).
4. `docs/DECISION_LOG.md` / `docs/OPEN_SOURCE_DECISIONS.md`: inserted mid-file, Codex appends at the end.
No Agent / NineRouter / evaluation / reporter / Shadow-evidence file was touched.

## Integration instructions

1. After Codex commits its work on `phase-03/trading-kernel`: `git fetch && git merge --no-ff refs/heads/phase-03/restart-recovery`
   (or open a PR from this branch into `phase-03/trading-kernel`).
2. Resolve any `runtime/shadow.py` conflict by keeping `_drive` + `resume_shadow` and Codex's guard/edits.
3. Run `uv run pytest`, `uv run pyright`, then `uv run pytest tests/recovery tests/runtime -q`.
4. Do not merge to `main` before the Phase 03 safety review covers both branches together.

## Remaining blockers

- LIVE: verified private order lookup + balance source (`AccountPort`) still absent; LIVE stays disabled.
- Owner decision: may an order that stays `UNKNOWN`/not-found for long ever be declared absent? (today: never).
- Equity-curve history and the consecutive-error counter are not restored (reporting / non-latched counter only).

## Verification (one final gate, after review fixes)

- `uv run pytest`: **237 passed, 1 deselected** (opt-in NineRouter). Recovery suite: **40 tests**. Baseline was 197.
- `uv run pyright` (strict): **0 errors, 0 warnings**.
- Secret scan (repository `SECRET_PATTERNS` + 3 generic patterns, 233 tracked/non-ignored files, no configured
  credential literals present): **PASS**. Only hits: the two pre-existing synthetic fixtures already listed as
  exceptions in `CODEX_SECRET_SCAN.json` (`tests/agent/test_decision_agent.py:34`, `tests/journal/test_journal.py:68`);
  neither file is touched by this branch.
- Mutation check: disabling the pending-exit marker in rebuild made both double-sell guards fail.
- No real-model call, no network in tests, no private Wallex call, no real order.

## Safety-boundary review (`.claude/agents/safety-boundary-reviewer.md`)

Verdict on `492ce62`: CHANGES_REQUIRED, no blocker. All seven checks clean (no resubmit/second exit path, no
risk/policy/approval/kill bypass, `settle_simulated` cannot reach an exchange venue, no new model capability,
fail-closed, Decimal/UTC, no secrets). Fixed in `9b1ff7a`:
- MAJOR: entry gate could reopen before exchange balances were ever compared → deferred
  `BALANCE_RECONCILIATION_PENDING` gate, cleared only by a passing check (tests added).
- MINOR: unexpected exceptions not journaled; INCOMPATIBLE unlatched in LIVE / no way to retire a crashed SHADOW run;
  naive timestamps; unflushed trade rows; quote-asset parsing; `recover` creating a missing DB / uncaught config
  validation — all fixed with tests.
- Not changed, documented in ADR-003: the unkeyed hash chain does not detect a deliberate full rewrite or tail
  truncation (balance check is the LIVE backstop). A re-review of `9b1ff7a` has not been run.

## خلاصه برای مالک پروژه (فارسی)

**چه ساخته شد؟** حالا اگر برنامه وسط کار خاموش شود یا کرش کند، دوباره «از صفر» شروع نمی‌کند و چیزی را هم فراموش
نمی‌کند. هر تغییری در پول، سفارش‌ها و «رزرو» (پولی که برای یک سفارش در راه کنار گذاشته شده) همان لحظه در یک
دفترچهٔ زنجیره‌ای (هر خط با اثر انگشت خط قبل) ثبت می‌شود. بعد از روشن‌شدن، یک «هماهنگ‌کنندهٔ بازیابی» این دفترچه
را دوباره اجرا می‌کند و دقیقاً همان وضعیت قبل را می‌سازد.

**قانون مهم:** اگر معلوم نباشد سفارشی به صرافی رسیده یا نه، آن سفارش «نامعلوم» (UNKNOWN) می‌شود و **هرگز دوباره
فرستاده نمی‌شود**. تا وقتی نامعلوم است، ورود (خرید) جدید ممنوع است ولی خروج‌های محافظ (حد ضرر = فروش خودکار هنگام
افت قیمت تا سقف ضرر، حد سود، مهلت نگهداری) ادامه دارند و فروش دوم برای همان موقعیت فرستاده نمی‌شود.

**تأیید انسانی:** تأییدهای منتظر بعد از ری‌استارت باطل می‌شوند؛ الگوریتم دوباره پیشنهاد می‌دهد و ریسک دوباره
بررسی می‌کند.

**توقف ایمن:** اگر دفترچه دست‌کاری/ناقص باشد یا تنظیمات و نسخهٔ کد عوض شده باشد، سیستم ادامه نمی‌دهد (BLOCKED) و
دلیل را ثبت می‌کند؛ در حالت دست‌کاری، «کلید قطع اضطراری» هم فعال می‌شود.

**دستورها:** بررسی بدون هیچ تغییری: `wallex-agent recover RUN_ID` · ادامهٔ Shadow: `wallex-agent shadow --resume RUN_ID`.

**هنوز انجام نشده:** برای LIVE، خواندن واقعی وضعیت سفارش و موجودی از حساب والکس هنوز پیاده/تأیید نشده؛ LIVE همچنان
خاموش است. هیچ سفارش واقعی ثبت نشده است.
