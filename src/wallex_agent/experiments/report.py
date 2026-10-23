"""Derived, inspectable period/cost matrices. Observations never select a strategy."""

import csv
import io
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from wallex_agent.contracts.hashing import content_id, sha256_hex
from wallex_agent.runtime.config import RunConfig

from .runner import TrialResult, trial_config, trial_identity
from .spec import ExperimentSpec, parameter_configs


def analyze_trials(rows: list[dict[str, Any]], *, base_cost_id: str, min_trades: int) -> list[dict[str, str]]:
    observations: list[dict[str, str]] = []
    for parameter_hash in sorted({r["parameter_hash"] for r in rows}):
        group = [r for r in rows if r["parameter_hash"] == parameter_hash]
        valid = [r for r in group if r["status"] == "PASS" and r["metrics"] is not None]
        def observe(code: str, detail: str) -> None:
            observations.append({"parameter_hash": parameter_hash, "code": code, "detail": detail})
        for r in group:
            if r["status"] != "PASS":
                observe("FAILED_TRIAL", f"{r['split']['name']}/{r['cost']['name']}")
        for r in valid:
            if r["metrics"]["trades"] < min_trades:
                observe("LOW_TRADE_COUNT", f"{r['split']['name']}/{r['cost']['name']}: {r['metrics']['trades']} < {min_trades}")
        baseline = [r for r in valid if r["cost"]["name"] == base_cost_id]
        returns = [Decimal(r["metrics"]["return_pct"]) for r in baseline]
        if returns and min(returns) < 0 < max(returns):
            observe("RETURN_SIGN_CHANGE", "positive and negative returns across base-cost periods")
        for b in baseline:
            if Decimal(b["metrics"]["return_pct"]) <= 0:
                continue
            for r in valid:
                cost, bc = r["cost"], b["cost"]
                higher = (Decimal(cost["fee_rate"]) >= Decimal(bc["fee_rate"])
                          and Decimal(cost["slippage_bps"]) >= Decimal(bc["slippage_bps"])
                          and (Decimal(cost["fee_rate"]), Decimal(cost["slippage_bps"])) != (Decimal(bc["fee_rate"]), Decimal(bc["slippage_bps"])))
                if r["split"]["name"] == b["split"]["name"] and higher and Decimal(r["metrics"]["return_pct"]) <= 0:
                    observe("COST_ERASED_POSITIVE_RETURN", f"{b['split']['name']}: {base_cost_id} → {cost['name']}")
    return observations


def _expected(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    spec = ExperimentSpec.model_validate(manifest["spec"])
    base = RunConfig.model_validate(manifest["base_config"])
    variants = parameter_configs(spec, base)
    if manifest["evaluate_test"]:
        variants = tuple(v for v in variants if v.parameter_hash == manifest["selected_parameter_hash"])
        if len(variants) != 1:
            raise ValueError("selected parameter provenance mismatch")
    expected: dict[str, dict[str, Any]] = {}
    for variant in variants:
        for split in spec.splits:
            if (split.role == "TEST") != manifest["evaluate_test"]:
                continue
            for cost in spec.costs:
                cfg = trial_config(variant, split, cost, manifest["dataset_name"])
                identity = trial_identity(cfg, manifest["dataset_hash"], manifest["splits"][split.name]["split_hash"],
                                          manifest["source_hash"], manifest["dependency_hash"])
                expected[content_id(identity)] = {**identity, "parameter_hash": variant.parameter_hash,
                    "parameters": variant.parameters, "split": split.model_dump(mode="json"), "cost": cost.model_dump(mode="json"),
                    "strategy_id": spec.strategy_id, "strategy_version": spec.strategy_version, "seed": spec.seed,
                    "code": manifest["code"]}
    if len(expected) != manifest["trial_count"]:
        raise ValueError("planned trial provenance mismatch")
    return expected


def _read(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    manifest: dict[str, Any] = json.loads((path / "MANIFEST.json").read_text())
    if content_id({k: v for k, v in manifest.items() if k != "manifest_hash"}) != manifest["manifest_hash"]:
        raise ValueError("manifest integrity mismatch")
    expected = _expected(manifest)
    raw = (path / "TRIALS.jsonl").read_bytes()
    complete = (path / "COMPLETE.json").exists()
    if complete and json.loads((path / "COMPLETE.json").read_text())["trials_sha256"] != sha256_hex(raw):
        raise ValueError("trial integrity mismatch")
    rows: list[dict[str, Any]] = []
    ids: set[str] = set()
    for line in raw.splitlines():
        trial = TrialResult.model_validate_json(line)
        identity = {k: getattr(trial, k) for k in ("config_hash", "dataset_hash", "split_hash", "source_hash", "dependency_hash")}
        metrics = trial.metrics.model_dump(mode="json") if trial.metrics else None
        result_hash = content_id({"identity": identity, "metrics": metrics, "error_type": trial.error_type})
        if (trial.trial_id in ids or content_id(identity) != trial.trial_id or result_hash != trial.result_hash
                or (trial.status == "PASS") != (metrics is not None and trial.error_type is None)):
            raise ValueError("trial integrity mismatch")
        record = trial.model_dump(mode="json")
        planned = expected.get(trial.trial_id)
        if planned is None or any(record[k] != v for k, v in planned.items()):
            raise ValueError("trial provenance mismatch with experiment manifest")
        if trial.metrics is not None and (trial.metrics.run_id != trial.trial_id or trial.metrics.environment != "BACKTEST"
                or trial.metrics.period_start is None or trial.metrics.period_end is None
                or datetime.fromisoformat(trial.metrics.period_start) != trial.split.start_utc
                or datetime.fromisoformat(trial.metrics.period_end) != trial.split.end_utc):
            raise ValueError("metric period provenance mismatch")
        ids.add(trial.trial_id)
        rows.append(record)
    if not ids <= set(expected) or (complete and ids != set(expected)):
        raise ValueError("trial count integrity mismatch")
    return manifest, rows, complete


def _derived_files(out: Path, files: dict[str, str]) -> None:
    for filename, text in files.items():
        path = out / filename
        if path.exists() and path.read_text() != text:
            raise ValueError("derived artifacts differ; preserve the original and use a fresh comparison directory")
    out.mkdir(parents=True, exist_ok=True)
    for filename, text in files.items():
        path = out / filename
        if not path.exists():
            path.write_text(text)


def summarize_experiment(path: Path, *, evaluation: Path | None = None) -> dict[str, Any]:
    manifest, rows, complete = _read(path)
    out = path
    if evaluation is not None:
        other, evaluated, other_complete = _read(evaluation)
        if manifest["evaluate_test"] or not other["evaluate_test"]:
            raise ValueError("comparison requires a development experiment and intentional TEST evaluation")
        for key in ("dataset_hash", "source_hash", "dependency_hash", "base_config_hash", "splits"):
            if manifest[key] != other[key]:
                raise ValueError("evaluation provenance differs from development experiment")
        a, b = manifest["spec"], other["spec"]
        if any(a[k] != b[k] for k in ("strategy_id", "strategy_version", "seed", "costs", "base_cost_id")):
            raise ValueError("evaluation protocol differs from development experiment")
        if not {r["parameter_hash"] for r in evaluated} <= {r["parameter_hash"] for r in rows}:
            raise ValueError("evaluation parameter not present in development experiment")
        rows += evaluated
        complete = complete and other_complete
        out = path / "comparisons" / other["manifest_hash"][:16]
    spec = manifest["spec"]
    observations = analyze_trials(rows, base_cost_id=spec["base_cost_id"], min_trades=spec["min_trades_observation"])
    summary: dict[str, Any] = {"schema_version": "1", "experiment_id": spec["experiment_id"], "complete": complete,
        "status": "PASS" if complete and all(r["status"] == "PASS" for r in rows) else "PARTIAL",
        "manifest_hash": manifest["manifest_hash"], "test_evaluated": any(r["split"]["role"] == "TEST" for r in rows),
        "trials": rows, "observations": observations,
        "configurations_for_claude_review": sorted({r["parameter_hash"] for r in rows}),
        "review_policy": "all configurations, ordered by hash; no ranking, selection or KEEP/MODIFY/REJECT decision",
        "metric_definitions": {"gross_profit_tmn": "sum of positive CLOSED trade net P&L after fees",
            "gross_loss_tmn": "absolute sum of negative CLOSED trade net P&L after fees",
            "profit_factor": "gross_profit / gross_loss; null when gross_loss = 0",
            "fee_burden_pct": "all fees paid / starting capital * 100",
            "time_in_market_pct": "union of holding intervals / split duration * 100",
            "undefined": "null in JSON; empty in CSV; — in Persian report"}}
    fields = ["parameter_hash", "trial_id", "status", "split", "role", "cost", "fee_rate", "slippage_bps",
              "starting_capital_tmn", "final_equity_tmn", "realized_pnl_tmn", "return_pct", "max_drawdown_pct", "trades",
              "wins", "losses", "breakeven_trades", "win_rate_pct", "avg_trade_pnl_tmn", "gross_profit_tmn", "gross_loss_tmn",
              "profit_factor", "fees_tmn", "fee_burden_pct", "avg_holding_s", "time_in_market_pct", "error_type"]
    csv_text = io.StringIO()
    writer = csv.DictWriter(csv_text, fields, lineterminator="\n")
    writer.writeheader()
    lines = ["# گزارش آزمایش زیرساخت", "", f"شناسه: `{spec['experiment_id']}` · وضعیت: {summary['status']} · اجراها: {len(rows)}",
        "", "این جدول آزمون تاریخی/مصنوعی با انتخاب‌گر قطعی B0 است؛ مدل زبانی فراخوانی نشده و پول واقعی جابه‌جا نشده است.",
        "هر بازه با سرمایه و دفتر معاملات مستقل شروع می‌شود. دادهٔ گذشته فقط برای آماده‌سازی ویژگی‌ها استفاده می‌شود.",
        "", "## دوره‌ها و هزینه‌ها", ""]
    for s in spec["splits"]:
        lines.append(f"- {s['name']} ({s['role']}): {s['start_utc']} تا {s['end_utc']} UTC؛ انتهای بازه منظور نمی‌شود.")
    lines.append(f"- داده: {manifest['dataset_name']}؛ هش: `{manifest['dataset_hash']}`؛ پوشش/دقیقه‌های مفقود در MANIFEST.json.")
    for c in spec["costs"]:
        lines.append(f"- {c['name']}: کارمزد هر سمت {c['fee_rate']}، لغزش {c['slippage_bps']} واحد در ده‌هزار؛ منبع {c['fee_source']}.")
    lines += ["- TEST: " + ("عمداً اجرا شده؛ فقط برای ارزیابی پیکربندی از پیش تعیین‌شده." if summary["test_evaluated"] else "اجرا نشده است."),
        "", "## نتیجه‌ها", "", "| هش پارامتر | بازه | هزینه | وضعیت | بازده٪ | افت بیشینه٪ | معامله |", "|---|---|---|---|---:|---:|---:|"]
    for r in rows:
        m: dict[str, Any] = r["metrics"] or {}
        writer.writerow({k: ({"split": r["split"]["name"], "role": r["split"]["role"], "cost": r["cost"]["name"],
            "fee_rate": r["cost"]["fee_rate"], "slippage_bps": r["cost"]["slippage_bps"]}.get(k, r.get(k, m.get(k)))) for k in fields})
        lines.append(f"| {r['parameter_hash'][:12]} | {r['split']['name']} | {r['cost']['name']} | {r['status']} | {m.get('return_pct', '—')} | {m.get('max_drawdown_pct', '—')} | {m.get('trades', '—')} |")
    lines += ["", "## مشاهده‌های ناپایداری", "", "این برچسب‌ها مشاهدهٔ عددی‌اند؛ توصیهٔ معامله نیستند. افت حساب یعنی کاهش از بالاترین ارزش قبلی حساب."]
    labels = {"LOW_TRADE_COUNT": "تعداد معامله کمتر از حد مشاهدهٔ تنظیم‌شده", "RETURN_SIGN_CHANGE": "علامت بازده میان دوره‌ها عوض شده",
              "COST_ERASED_POSITIVE_RETURN": "با هزینهٔ بیشتر بازده مثبت از بین رفته", "FAILED_TRIAL": "اجرای ناموفق؛ معیار مالی نامشخص است"}
    lines += [f"- `{o['parameter_hash'][:12]}`: {labels[o['code']]}؛ {o['detail']}" for o in observations] or ["موردی ثبت نشد."]
    lines += ["", "## برای بررسی Claude", "", "تمام هش‌های پارامتر در SUMMARY.json فهرست شده‌اند؛ هیچ‌کدام برنده یا مناسب LIVE اعلام نشده است.",
              "سود/زیان و ضریب سود از معاملات بسته‌شده پس از کارمزد حساب می‌شوند. ضریب سود با زیان صفر تعریف نمی‌شود؛ — یعنی نامشخص.",
              "بار کارمزد، درصد مجموع کارمزد از سرمایهٔ شروع است. درصد زمان بازار، اجتماع زمان نگهداری موقعیت‌هاست.",
              "بک‌تست صف سفارش و تأخیر واقعی را مدل نمی‌کند؛ نتیجهٔ شبیه‌سازی تضمین سود آینده نیست.", ""]
    _derived_files(out, {"SUMMARY.json": json.dumps(summary, ensure_ascii=False, indent=1) + "\n",
                         "SUMMARY.csv": csv_text.getvalue(), "REPORT_FA.md": "\n".join(lines)})
    return summary
