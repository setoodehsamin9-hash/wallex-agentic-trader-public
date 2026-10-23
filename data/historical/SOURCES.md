# Sources and observation boundaries

- Official developer documentation: https://developers.wallex.ir/ (authoritative; not copied as code).
- Official model-use rules: https://api.wallex.ir/llm.txt ; repository evidence copy:
  `research/phase-02/evidence/wallex_llm_txt_2026-10-22.txt`.
- Public candles: `GET https://api.wallex.ir/v1/udf/history` with symbol/resolution/from/to. Each exact query,
  raw-byte SHA-256, byte count and original UTC capture time is retained in `MANIFEST.json` → `raw_responses`.
- Public interval advertisement: `GET https://api.wallex.ir/v1/udf/config`. Advertisement does not prove actual spacing.
- Current public reference data: `GET /v1/markets`, `GET /hector/web/v1/markets`, `GET /v1/prop/packages/183`.
  These are not account or fee reads. No headers contain an account credential.
- Candidate provenance: owner-provided Muse `WALLEX_MARKET_SELECTION_RESEARCH_FA.md`, section 9, SHA-256
  `06666ae219c635bdf52f924dfedc17e098c59131dc4d8c1a47bc0d52b6e1f1ee`.
  Source was read from the owner's untracked research directory; the shortlist only is embedded in the recipe.
  Its market ranking and strategy suggestions are not adopted as trading decisions.

On 2026-10-22 a BTCTMN one-day `15` request returned 1440 bars spaced 60 seconds; a seven-day `240` request
returned 168 bars spaced 3600 seconds. A one-day `60` request returned 24 hourly bars. A 90-day or 365-day
`60` request ending 2026-10-22 returned only 696 hourly bars (2026-09-23 through 2026-10-21). A two-day `1`
request returned 2880 minute bars; a seven-day minute request returned `no_data`. A 1095-day `1D` request returned
799 daily bars beginning 2024-08-14. These are request-specific observations, not guaranteed retention rules.
The recipe uses short chunks and reports missing coverage instead of assuming a wide request was complete.

The archive contains exact public response bytes. Derived CSV/gzip data and quality JSON are deterministic products
of those responses and this branch. No third-party implementation or numerical trading default was copied;
existing project parsers/writer/aggregator and Python standard-library facilities are reused without new dependencies.
