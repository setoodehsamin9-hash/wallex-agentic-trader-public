# Challenge submission manifest

This is an inventory, not a claim of LIVE readiness or profitability. Each evidence bundle covers exactly one
persisted run; BACKTEST, SHADOW and LIVE must remain separate. Future competition artifacts are pending.

| Item | Source / command | Status |
|---|---|---|
| Source commit / clean competition tag | RunManifest.code; final clean `live-YYYYMMDD-N` tag must identify exactly the code that traded | PENDING competition release |
| Architecture and permissions | `docs/ARCHITECTURE.md`, `docs/AGENT_WORKFLOW.md`, `docs/AUTONOMY_AND_PERMISSIONS.md`, `docs/adr/` | Existing source docs; include final revision |
| Strategy explanation and frozen parameters | `reports/phase-04/` and final strategy/config references; cite id/version/config hash from each run | Existing research; final competition explanation PENDING |
| AI role and bounded decisions | `DECISIONS.jsonl`, `MODEL_EVENTS.jsonl`; distinguish LLM / BASELINE / REPLAY; only stored concise rationale | Export supported; final actual-model evidence PENDING |
| Deterministic risk system | Risk docs/tests plus `RISK_EVENTS.jsonl` and stored verdicts | Export supported |
| Backtest evidence | Separate bundle from `--db backtest.sqlite`; include data hashes, fees/fill/slippage assumptions and time splits | Export supported; final selected-run bundle PENDING |
| Shadow evidence | Separate SHADOW bundle, zero-write proof and UNKNOWN/failure evidence | Export supported; final operational gate PENDING Claude |
| Live evidence | Separate LIVE bundle; exchange evidence, realized TMN outcome, open positions and official cutoff | **PENDING** — no LIVE run performed by this task |
| Complete Wallex write audit | Persisted operation-level audit covering submissions/cancels/modifications and gaps | **PENDING** — legacy lifecycle cannot prove full count; observer returns UNKNOWN |
| Trade logs | `TRADES.jsonl`, `EXECUTION_EVENTS.jsonl`; simulation/exchange source retained | Export supported |
| Decision logs | `DECISIONS.jsonl`, source context in `SOURCE.json` | Export supported |
| Performance report | `PERFORMANCE_FA.md`, `SUMMARY.json`, `EQUITY.jsonl` per run | Export supported; no merged simulated/real P&L |
| Reproducibility | `RUN_MANIFEST.json`, `REPRODUCIBILITY.json`, `SOURCE.json`, `FILE_HASHES.json` | Export supported; identifiers never substitute for missing raw datasets |
| Offline observable reconstruction | `uv run wallex-agent evidence verify evidence/RUN_ID` | Hash and reconstruction check; creates no run |
| Original backtest replay (separate workflow) | Existing `uv run wallex-agent replay RUN_ID`; exact source dataset/config required | Existing command **writes a new simulated run**; not part of observer |
| Demo checklist and recorded output | `DEMO_FLOW_FA.md`, generated `DEMO_FA.md`, `demo-evidence RUN_ID` | Checklist/output supported |
| Demo video (~5 minutes) | Use recorded evidence and show WAIT/REJECT honestly | **PENDING** owner recording |
| Known limitations | See phase-05 observability report, per-bundle reproducibility limitations and pending LIVE inputs | Must accompany final submission |

Generate and validate a submission evidence directory (new destination only):

```bash
uv run wallex-agent evidence export RUN_ID --db /absolute/path/to/journal.sqlite --out evidence/RUN_ID
uv run wallex-agent evidence verify evidence/RUN_ID
```

Do not include raw config/environment values, keys, headers, raw model responses or private account/session identifiers.
Run the repository secret scan before publication. The verifier proves internal consistency, not authenticity of an
exchange fill or future profitability. Missing metrics remain null/UNKNOWN. A journal prefix is not proof that no
unrecorded tail exists. Dataset hashes identify the source bytes; provide the matching public dataset separately
when full trading replay is required.
