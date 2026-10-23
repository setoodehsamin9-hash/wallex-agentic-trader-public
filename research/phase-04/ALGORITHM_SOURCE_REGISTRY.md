# Algorithm / open-source source registry

Evidence date: 2026-10-22. This is a source map for Claude, **not a strategy recommendation**, parameter search,
profitability conclusion, dependency approval or security certification. No framework, strategy, model or upstream
numeric default is imported. All repository rows are reference-only unless explicitly marked hosted-API use.

Method: public repository metadata and default-branch commit → license at that exact commit → pinned README
and selected component-directory listing. Directory existence and documented capability are verified; algorithm
implementation, mathematical correctness, profitability and Wallex compatibility are not audited here.
Full SHA, license/README hashes and inspected paths: [ALGORITHM_SOURCE_EVIDENCE.json](ALGORITHM_SOURCE_EVIDENCE.json).
All repositories were reported not archived. The activity column is **default-branch commit date / repository
last-push date**, not a promise of support; pushes can affect another branch.

| Repository/resource | Pinned commit | License | Activity UTC | Relevant components/families | Reuse decision | Risks / why project should care |
|---|---|---|---|---|---|---|
| [hummingbot/hummingbot](https://github.com/hummingbot/hummingbot/tree/9af100d6822da7d2d0291a906c730ef172284ee2) | `9af100d6822d` | Apache-2.0 ([file](https://github.com/hummingbot/hummingbot/blob/9af100d6822da7d2d0291a906c730ef172284ee2/LICENSE)) | 2026-09-22 / 2026-10-20 | V2 controllers/executors; directional position lifecycle, triple barriers, TWAP, market-making/order-book orchestration | No framework dependency. Permissive components may be evaluated later; current use reference only. | Exchange-specific fill/cancel assumptions and live-tunable settings must be checked against Wallex; no upstream sizing, fees or defaults. |
| [freqtrade/freqtrade](https://github.com/freqtrade/freqtrade/tree/c051e9065c5b677f03817dd1f83cb980af1ed684) | `c051e9065c5b` | GPL-3.0 ([file](https://github.com/freqtrade/freqtrade/blob/c051e9065c5b677f03817dd1f83cb980af1ed684/LICENSE)) | 2026-10-22 / 2026-10-22 | Strategy template/analysis notebook; trend/volume features, backtest protections and look-ahead analysis | Reference/concepts only; GPL code requires owner decision before reuse. | No Wallex connector; execution/fill, fee, protection and time-model assumptions are not ours. |
| [freqtrade/freqtrade-strategies](https://github.com/freqtrade/freqtrade-strategies/tree/f3340ce11f5bdf62f598522e64d1f5638eaa13f5) | `f3340ce11f5b` | GPL-3.0 ([file](https://github.com/freqtrade/freqtrade-strategies/blob/f3340ce11f5bdf62f598522e64d1f5638eaa13f5/LICENSE)) | 2026-09-08 / 2026-09-08 | Verified MultiMa, Bandtastic and AlmgrenChrissStrategy examples: moving-average, band-style and execution examples to inspect | Reference only; GPL; examples are not production approvals. | No return claims accepted; individual examples require logic/leakage/fill audit and Wallex cost stress, not parameter copying. |
| [jesse-ai/jesse](https://github.com/jesse-ai/jesse/tree/9a3ebe075d9d0af5f012f921f46c2286f234af68) | `9a3ebe075d9d` | MIT ([file](https://github.com/jesse-ai/jesse/blob/9a3ebe075d9d0af5f012f921f46c2286f234af68/LICENSE)) | 2026-10-22 / 2026-10-22 | EMA, ATR, ADX, Donchian and Bollinger indicator files; strategy lifecycle/sizing documentation | MIT makes isolated future adaptation possible with notice; no dependency/adaptation now. | Framework/NumPy/compiled dependencies and position/broker assumptions add integration cost; existing project math first. |
| [jesse-ai/awesome-jesse](https://github.com/jesse-ai/awesome-jesse/tree/653b23086b4236a50b80ca8bcec1f97c1b5b1d60) | `653b23086b42` | CC0 claimed in pinned README; LICENSE file absent | 2025-02-26 / 2025-02-26 | Example-strategy, indicator and research/resource discovery list | README declares CC0; no standalone LICENSE found. Linked repositories retain their own licenses. Reference only. | Links are discovery leads, not verified algorithms or permission to copy linked code. |
| [QuantConnect/Lean](https://github.com/QuantConnect/Lean/tree/f3d841e60dfd010ee5fc293ea893e56952484108) | `f3d841e60dfd` | Apache-2.0 ([file](https://github.com/QuantConnect/Lean/blob/f3d841e60dfd010ee5fc293ea893e56952484108/LICENSE)) | 2026-10-21 / 2026-10-22 | Momentum, DonchianChannel, BollingerBands, Momersion; official supported-indicator and research resources | Apache-2.0 code may be evaluated for isolated future adaptation; no C# engine dependency here. | Indicator/event/warmup and fill semantics differ; market data, cloud services and research text have separate terms. |
| [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader/tree/a51ae015c150ef68f62d9295e3efa9534f492a4a) | `a51ae015c150` | LGPL-3.0 ([file](https://github.com/nautechsystems/nautilus_trader/blob/a51ae015c150ef68f62d9295e3efa9534f492a4a/LICENSE)) | 2026-10-22 / 2026-10-22 | Rust indicator average/book/momentum/volatility modules; event-driven execution/risk/reconciliation concepts | Reference only; LGPL requires owner decision before any code reuse. | Python indicator path returned 404 at this pin; verified current Rust crate instead. Full Rust/Python platform is unnecessary for this task. |
| [microsoft/qlib](https://github.com/microsoft/qlib/tree/54355232463878d2eebb91fe0ee5fa7fa1f5976c) | `543552324638` | MIT ([file](https://github.com/microsoft/qlib/blob/54355232463878d2eebb91fe0ee5fa7fa1f5976c/LICENSE)) | 2026-10-08 / 2026-10-22 | Signal strategy, cost-control and order-generator modules; research workflow and model/portfolio layers | MIT, possible future isolated evaluation; reference only now. | Equity-oriented calendars/labels, data pipelines and artifact-loading assumptions need audit; no untrusted model artifact loading or new ML stack. |
| [AI4Finance-Foundation/FinRL](https://github.com/AI4Finance-Foundation/FinRL/tree/c0018c25de864157dc8470f0047b0d10ec721715) | `c0018c25de86` | MIT ([file](https://github.com/AI4Finance-Foundation/FinRL/blob/c0018c25de864157dc8470f0047b0d10ec721715/LICENSE)) | 2026-10-20 / 2026-10-20 | Data processor plus cryptocurrency and portfolio-allocation environment directories | MIT project, reference only; no RL training/runtime dependency. | A realistic Wallex simulator is not established; reward, fill, costs, training splits and external data licenses require independent evidence. |
| [AI4Finance-Foundation/FinGPT](https://github.com/AI4Finance-Foundation/FinGPT/tree/fdb04c9a273d1ccc3764b09b8e3ed1e708e57566) | `fdb04c9a273d` | MIT ([file](https://github.com/AI4Finance-Foundation/FinGPT/blob/fdb04c9a273d1ccc3764b09b8e3ed1e708e57566/LICENSE)) | 2026-09-23 / 2026-09-23 | Financial sentiment analysis and forecaster directories; NLP dataset/evaluation workflow references | MIT project code, reference only; no model/SDK/training/API added. | Model weights, upstream datasets and APIs have separate permissions; published benchmark claims do not establish trading value or Persian/crypto calibration. |
| [AI4Finance-Foundation/FinNLP](https://github.com/AI4Finance-Foundation/FinNLP/tree/be4dfd5c2526e88bbb1307d444086c46a4b47e3a) | `be4dfd5c2526` | MIT ([file](https://github.com/AI4Finance-Foundation/FinNLP/blob/be4dfd5c2526e88bbb1307d444086c46a4b47e3a/LICENSE)) | 2024-07-01 / 2024-07-01 | Financial news/social/announcement data_sources and data_engineering modules | MIT code, reference only; no crawler imports. | Default-branch activity last observed 2024-07-01; source scraping/API contracts may be stale and publisher terms differ. |
| [nirholas/cryptocurrency.cv](https://github.com/nirholas/cryptocurrency.cv/tree/e9a07f459b30ef3b2f7b91719dba2f2d22026550) | `e9a07f459b30` | Proprietary / all rights reserved ([file](https://github.com/nirholas/cryptocurrency.cv/blob/e9a07f459b30ef3b2f7b91719dba2f2d22026550/LICENSE)) | 2026-10-11 / 2026-10-22 | Documented public news JSON, attribution/optional enrichment and claimed historical archive | Hosted API only is concretely used; software/SDK source is proprietary/all rights reserved, not copied. | Fair-use service, no guaranteed quota/history; source-specific rights and point-in-time publication/observation must be preserved. |

## Family lookup (evidence, not selection)

| Family | Sources worth inspecting for the mechanism | Required project-specific checks |
|---|---|---|
| Momentum/trend | Jesse EMA/ADX; LEAN Momentum; Freqtrade strategy templates; MultiMa example | Closed-bar timing, warmup, Decimal precision and costs; choose no parameter here |
| Breakout | Jesse Donchian; LEAN DonchianChannel | Next-observable-bar availability, gap handling and Wallex fill assumptions |
| Mean reversion | Jesse/LEAN Bollinger; band-style strategy examples | Churn/cost stress and regime assumptions; no strategy approval |
| Regime detection | ADX/Momersion references; Qlib research/label workflow | Causal features and time-ordered evaluation; no fitted threshold imported |
| Volatility filters | Jesse ATR/Bollinger width; Nautilus volatility crate | Formula/warmup/units and missing-bar behavior |
| Volume/order book | LEAN volume indicators/catalog; Nautilus book modules; Hummingbot market data/executors | Historical depth availability, units and cancellations; no fabricated historical book |
| Execution | Hummingbot executors/TWAP; Nautilus lifecycle; AlmgrenChriss example | Wallex partial fills, UNKNOWN reconciliation, precision and one-order path |
| Portfolio/risk | Existing project risk remains authoritative; Qlib/FinRL/Nautilus are references | Never replace ledger/risk checks or copy defaults without owner-approved design |
| News/sentiment | FinNLP sources, FinGPT evaluation references, public cryptocurrency.cv JSON | Publisher/weight/data licenses, languages, point-in-time observations and independent calibration |

Official LEAN resources additionally inspected: [indicator catalog](https://www.quantconnect.com/docs/v2/writing-algorithms/indicators/supported-indicators)
and [research landing page](https://www.quantconnect.com/research/). The latter did not expose a reproducible strategy
study in the fetched view; no performance result was relied on. Indicators are references for documented mechanisms,
not approval to introduce a C# engine or external data service.

## Reuse boundary

- First use this project's already implemented bars, features, risk, ledger and execution interfaces.
- Permissive repository license does not prove every dependency, strategy example, dataset, model weight or API is reusable.
- GPL/LGPL/unclear licenses require the owner's recorded decision **before code enters**. CC0 on awesome-jesse's
  list does not license projects it links to.
- Evaluate an isolated component only if it supplies a missing capability; retain required notices, pin its exact source,
  classify dependency/adaptation/concept/reference in OPEN_SOURCE_DECISIONS, and test against Wallex behavior.
- Source statistics, benchmark scores and reported returns are not evidence of a usable strategy for this competition.
