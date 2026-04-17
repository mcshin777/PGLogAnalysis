from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analyzer import analyze_events, evidence_bundle
from .llm import summarize_with_lmstudio
from .parser import discover_log_files, parse_paths
from .report import render_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pglog-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Analyze PostgreSQL text logs")
    analyze.add_argument("paths", nargs="+", help="Log file or directory paths")
    analyze.add_argument("--output", default="./out", help="Output directory")
    analyze.add_argument("--llm", choices=["off", "lmstudio"], default="off", help="Optional LLM report enhancer")
    analyze.add_argument("--lmstudio-base-url", default="http://localhost:1234/v1")
    analyze.add_argument("--lmstudio-model", default="google/gemma-4-e4b")
    analyze.add_argument("--lmstudio-timeout", type=int, default=60)
    analyze.add_argument("--redact-identities", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "analyze":
        return run_analyze(args)
    parser.error("unknown command")
    return 2


def run_analyze(args: argparse.Namespace) -> int:
    input_paths = []
    for raw_path in args.paths:
        path = Path(raw_path)
        if not path.exists():
            raise SystemExit(f"Input path does not exist: {path}")
        input_paths.extend(discover_log_files(path))
    if not input_paths:
        raise SystemExit("No log files found.")

    events, parse_errors = parse_paths(input_paths)
    observations, findings, summary = analyze_events(events, redact_identities=args.redact_identities)
    evidence = evidence_bundle(observations, findings, summary)

    llm_summary = None
    if args.llm == "lmstudio":
        try:
            llm_summary = summarize_with_lmstudio(
                evidence,
                base_url=args.lmstudio_base_url,
                model=args.lmstudio_model,
                timeout=args.lmstudio_timeout,
            )
        except RuntimeError as exc:
            llm_summary = f"LLM summary skipped: {exc}"

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    _write_json(output_dir / "findings.json", evidence)
    _write_jsonl(output_dir / "observations.jsonl", [obs.to_dict() for obs in observations])
    _write_jsonl(output_dir / "parse_errors.jsonl", parse_errors)
    (output_dir / "report.md").write_text(
        render_report(observations, findings, summary, llm_summary=llm_summary),
        encoding="utf-8",
    )

    print(f"Parsed {len(events)} log events from {len(input_paths)} file(s).")
    print(f"Created {len(observations)} query observations and {len(findings)} findings.")
    print(f"Wrote outputs to {output_dir}")
    return 0


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

