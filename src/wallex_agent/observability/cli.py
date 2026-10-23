"""Small read-only command adapter. It never loads run configs or instantiates a trading runtime."""
import argparse
import re
import sys
from pathlib import Path
from typing import Any

from .bundle import export_evidence, safe_source, verify_bundle
from .inspector import inspect_run
from .presentation import explain_decisions, render_demo_fa, render_explanation_fa, render_performance_fa, render_status_fa
from .source import EvidenceError, read_run, timestamp


def add_parsers(sub: Any) -> None:
    for name in ("performance", "explain", "demo-evidence"):
        parser = sub.add_parser(name, help="read-only persisted run evidence")
        parser.add_argument("run_id")
        parser.add_argument("--db", default="state.sqlite")
        if name == "explain":
            parser.add_argument("--last", type=int, default=5)
    evidence = sub.add_parser("evidence", help="read-only evidence export/reconstruction")
    commands = evidence.add_subparsers(dest="evidence_command", required=True)
    export = commands.add_parser("export")
    export.add_argument("run_id")
    export.add_argument("--db", default="state.sqlite")
    export.add_argument("--out", help="new directory (never overwrite)")
    export.add_argument("--generated-at", help="fixed UTC timestamp for byte-identical exports")
    verify = commands.add_parser("verify")
    verify.add_argument("directory")


def command(args: argparse.Namespace, state: Path) -> int:
    try:
        if args.command == "evidence" and args.evidence_command == "verify":
            result = verify_bundle(Path(args.directory))
            print(f"PASS | هش‌ها و بازسازی مشاهده یکسان است | {result['reproducible_content_sha256']}")
            return 0
        # Preserve the old --run-id form; accept the compact positional form too.
        run_id = getattr(args, "run_id", None) or getattr(args, "run_id_option", None)
        if args.command == "status" and getattr(args, "run_id", None) and getattr(args, "run_id_option", None):
            raise EvidenceError("provide run id once")
        db = Path(args.db)
        if not db.is_absolute():
            db = state / db
        source = safe_source(read_run(db, run_id))
        summary = inspect_run(source)
        match args.command:
            case "status":
                latest = explain_decisions(source, last=1)
                print(render_status_fa(summary, latest[-1] if latest else None), end="")
            case "performance":
                print(render_performance_fa(source, summary), end="")
            case "explain":
                explanations = explain_decisions(source, last=args.last)
                print("".join(render_explanation_fa(e) for e in explanations) if explanations else "تصمیم ثبت‌شده ناموجود است.", end="\n")
            case "demo-evidence":
                print(render_demo_fa(source), end="")
            case "evidence":
                if not args.out and not re.fullmatch(r"[A-Za-z0-9_.-]+", source.run_id):
                    raise EvidenceError("unsafe run id for default output path; provide --out")
                out = Path(args.out) if args.out else Path("evidence") / source.run_id
                when = timestamp(args.generated_at) if args.generated_at else None
                export_evidence(source, out, generated_at=when)
                print(f"PASS | بسته شواهد: {out} | REAL WRITE={summary['real_wallex_write_count']}")
            case _:
                return 2
        return 0
    except (EvidenceError, OSError):
        # Deliberately omit exception payloads: malformed rows can themselves contain secrets.
        print("FAIL | شواهد کافی/معتبر یا مسیر امن در دسترس نیست؛ هیچ مقدار غایبی صفر فرض نشد. دفتر ثبت تغییر نکرد.", file=sys.stderr)
        return 2
