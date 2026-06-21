#!/usr/bin/env python3
"""Convenience tasks for the Value Tracker data/build pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import historical_store

SNAPSHOT = ROOT / "raw/generated/snapshot.yaml"
SIMULATION = ROOT / "raw/generated/historical"
DATA = ROOT / "data/stockhunt.json"
DEFAULT_BACKTEST_START = "2024-01-01"


def run(command: List[str]) -> None:
    print("+ " + " ".join(command), file=sys.stderr)
    subprocess.run(command, cwd=ROOT, check=True)


def resolve_output_path(path: pathlib.Path) -> pathlib.Path:
    return path if path.is_absolute() else ROOT / path


def write_schedule_state(path: Optional[pathlib.Path], state: Dict[str, Any]) -> None:
    if not path:
        return
    target = resolve_output_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, sort_keys=True)


def add_fetch_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--top", type=int, default=50, help="Top current holdings to fetch per manager.")
    parser.add_argument("--manager-limit", type=int, default=None, help="Limit managers for smoke tests.")
    parser.add_argument("--backtest-start", default=DEFAULT_BACKTEST_START, help="Historical backtest start date.")
    parser.add_argument("--end-date", default=None, help="Historical backtest end date. Defaults to today.")
    parser.add_argument("--live-batch-size", type=int, default=30, help="Symbols per Longbridge live market-data command.")
    parser.add_argument("--live-sleep", type=float, default=0.0, help="Seconds to sleep after each Longbridge live command.")
    parser.add_argument("--sec-user-agent", default="ValueTracker/0.1 contact@example.com", help="User-Agent for SEC early-update checks.")
    parser.add_argument("--disable-auto-map", action="store_true", help="Only use explicit CUSIP mappings.")
    parser.add_argument("--no-persist-auto-map", action="store_true", help="Do not append successful auto-maps to the CUSIP map.")


def last_rebalance_date(path: pathlib.Path = SIMULATION) -> Optional[str]:
    return historical_store.last_rebalance_date(path) if path.exists() else None


def load_yaml_file(path: pathlib.Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return payload if isinstance(payload, dict) else {}


def load_json_file(path: pathlib.Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def normalize_cik(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits.zfill(10) if digits else ""


def config_hash(config: Dict[str, Any]) -> str:
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def current_config_hash() -> Optional[str]:
    config = load_yaml_file(ROOT / "config/stockhunt.yaml")
    return config_hash(config) if config else None


def current_13f_state() -> Tuple[Optional[str], List[Dict[str, Any]], Optional[str]]:
    snapshot = load_yaml_file(SNAPSHOT)
    if snapshot:
        build = snapshot.get("build") or {}
        return (
            snapshot.get("latest_13f_report_period"),
            snapshot.get("latest_13f_fingerprint") or [],
            build.get("config_hash"),
        )

    data = load_json_file(DATA)
    build = data.get("build") or {}
    return build.get("latest_13f_report_period"), build.get("latest_13f_fingerprint") or [], build.get("config_hash")


def enabled_config_managers(args: argparse.Namespace) -> List[Dict[str, Any]]:
    config = load_yaml_file(ROOT / "config/stockhunt.yaml")
    managers = []
    for manager in config.get("institutions", {}).get("managers", []):
        if not manager.get("enabled", True):
            continue
        row = dict(manager)
        row["cik"] = normalize_cik(row.get("cik"))
        if row["cik"]:
            managers.append(row)
    return managers[: args.manager_limit] if args.manager_limit else managers


def fetch_sec_submissions(cik: str, user_agent: str) -> Dict[str, Any]:
    request = urllib.request.Request(
        f"https://data.sec.gov/submissions/CIK{cik}.json",
        headers={"User-Agent": user_agent},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def latest_sec_13f_state(args: argparse.Namespace) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    rows = []
    for manager in enabled_config_managers(args):
        recent = fetch_sec_submissions(manager["cik"], args.sec_user_agent).get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        for index, form in enumerate(forms):
            if str(form).upper() not in {"13F-HR", "13F-HR/A"}:
                continue
            rows.append(
                {
                    "cik": manager["cik"],
                    "accession_number": recent.get("accessionNumber", [None])[index],
                    "filing_date": recent.get("filingDate", [None])[index],
                    "report_period": recent.get("reportDate", [None])[index],
                }
            )
            break
    rows = sorted(rows, key=lambda row: (str(row.get("cik") or ""), str(row.get("accession_number") or "")))
    latest_period = max((str(row.get("report_period")) for row in rows if row.get("report_period")), default=None)
    return latest_period, rows


def weekly_13f_unchanged(args: argparse.Namespace) -> bool:
    current_period, current_fingerprint, saved_config_hash = current_13f_state()
    if not current_period and not current_fingerprint:
        print("no local 13F state found; continuing weekly pipeline", file=sys.stderr)
        return False

    active_config_hash = current_config_hash()
    if not saved_config_hash or saved_config_hash != active_config_hash:
        print(
            f"config changed or missing hash (local={saved_config_hash}, current={active_config_hash}); continuing weekly pipeline",
            file=sys.stderr,
        )
        return False

    try:
        latest_period, latest_fingerprint = latest_sec_13f_state(args)
    except Exception as exc:  # noqa: BLE001 - fail open so schedule still updates on probe issues.
        print(f"SEC 13F early-check failed; continuing weekly pipeline: {exc}", file=sys.stderr)
        return False

    if current_fingerprint and latest_fingerprint:
        latest_ciks = {row.get("cik") for row in latest_fingerprint if row.get("cik")}
        if latest_ciks:
            current_fingerprint = [row for row in current_fingerprint if row.get("cik") in latest_ciks]
        unchanged = current_fingerprint == latest_fingerprint
    else:
        unchanged = bool(current_period and latest_period and latest_period <= current_period)

    if unchanged:
        print(
            f"13F unchanged (local={current_period}, latest={latest_period}); skipping weekly pipeline",
            file=sys.stderr,
        )
    else:
        print(
            f"13F changed or newer (local={current_period}, latest={latest_period}); continuing weekly pipeline",
            file=sys.stderr,
        )
    return unchanged


def run_live_input(args: argparse.Namespace) -> Dict[str, Any]:
    from scripts import build_live_input

    live_args = argparse.Namespace(
        config=ROOT / "config/stockhunt.yaml",
        cusip_map=ROOT / "config/cusip-symbols.yaml",
        top=args.top,
        manager_limit=args.manager_limit,
        batch_size=args.live_batch_size,
        sleep=args.live_sleep,
        disable_auto_map=args.disable_auto_map,
        no_persist_auto_map=args.no_persist_auto_map,
        data_date=None,
        market_data_date=None,
    )
    raw = build_live_input.build_live_raw(live_args)
    if not raw.get("latest_13f_report_period"):
        raise SystemExit("no latest 13F report period found; see warnings in live raw input")
    return raw


def run_backend(raw: Dict[str, Any]) -> None:
    from scripts import stockhunt_backend

    config_path = ROOT / "config/stockhunt.yaml"
    config = stockhunt_backend.load_yaml(config_path)
    cfg_hash = stockhunt_backend.config_hash(config)
    metrics = stockhunt_backend.compute_metrics(config, raw)
    snapshot = stockhunt_backend.build_snapshot(config, raw, cfg_hash, metrics)
    stockhunt_backend.write_yaml(SNAPSHOT, snapshot)
    print(f"wrote {SNAPSHOT}")


def run_backtest(
    args: argparse.Namespace,
    full: bool,
    allow_rebalance: bool,
    refresh_submissions: bool = True,
) -> None:
    backtest_command = [
        sys.executable,
        "scripts/historical_backtest.py",
        "--start-date",
        args.backtest_start,
        "--output",
        str(SIMULATION),
        "--sec-user-agent",
        args.sec_user_agent,
    ]
    if args.end_date:
        backtest_command.extend(["--end-date", args.end_date])
    if args.manager_limit:
        backtest_command.extend(["--manager-limit", str(args.manager_limit)])
    if full:
        backtest_command.extend(["--refresh-sec", "--refresh-prices"])
    elif refresh_submissions:
        backtest_command.append("--incremental")
        backtest_command.append("--refresh-submissions")
    else:
        backtest_command.append("--incremental")
    if args.disable_auto_map:
        backtest_command.append("--disable-auto-map")
    if args.no_persist_auto_map:
        backtest_command.append("--no-persist-auto-map")
    if not allow_rebalance:
        frozen_rebalance_date = last_rebalance_date()
        if not frozen_rebalance_date:
            raise SystemExit("daily/hold mode requires an existing raw/generated/historical store with last_rebalance_date")
        backtest_command.extend(["--rebalance-until", frozen_rebalance_date])
    run(backtest_command)


def run_export() -> None:
    export_command = [
        sys.executable,
        "scripts/generate_stockhunt_data.py",
        "--snapshot",
        str(SNAPSHOT),
        "--simulation",
        str(SIMULATION),
    ]
    run(export_command)


def fetch_pipeline(args: argparse.Namespace, full: bool, allow_rebalance: bool) -> None:
    raw = run_live_input(args)
    run_backend(raw)
    run_backtest(args, full=full, allow_rebalance=allow_rebalance)
    run_export()


def build() -> None:
    argparse.ArgumentParser(description="Build the Hugo static site from existing data.").parse_args()
    run(["hugo", "--minify", "--cleanDestinationDir"])


def fetch() -> None:
    parser = argparse.ArgumentParser(description="Incrementally fetch Value Tracker data without running Hugo.")
    add_fetch_args(parser)
    parser.add_argument("--hold-positions", action="store_true", help="Update prices and P/L without creating a new rebalance.")
    args = parser.parse_args()
    fetch_pipeline(args, full=False, allow_rebalance=not args.hold_positions)


def fetch_all() -> None:
    parser = argparse.ArgumentParser(description="Fully refresh Value Tracker data without running Hugo.")
    add_fetch_args(parser)
    args = parser.parse_args()
    fetch_pipeline(args, full=True, allow_rebalance=True)


def schedule() -> None:
    parser = argparse.ArgumentParser(description="Run scheduled Value Tracker jobs and then build Hugo.")
    parser.add_argument("mode", choices=["daily", "weekly"], help="daily updates prices/P&L; weekly allows rebalance.")
    add_fetch_args(parser)
    parser.add_argument("--force", action="store_true", help="Run weekly pipeline even when SEC 13F metadata is unchanged.")
    parser.add_argument("--check-only", action="store_true", help="Only run weekly early-exit checks and write state.")
    parser.add_argument("--state-output", type=pathlib.Path, default=None, help="Write scheduled job state as JSON.")
    args = parser.parse_args()
    if args.check_only and args.mode == "daily":
        write_schedule_state(args.state_output, {"mode": args.mode, "skipped": False, "reason": "daily_no_early_check"})
        return
    if args.mode == "daily":
        run_backtest(args, full=False, allow_rebalance=False, refresh_submissions=False)
        run_export()
    else:
        if not args.force and weekly_13f_unchanged(args):
            write_schedule_state(args.state_output, {"mode": args.mode, "skipped": True, "reason": "13f_unchanged"})
            return
        if args.check_only:
            write_schedule_state(args.state_output, {"mode": args.mode, "skipped": False, "reason": "13f_changed_or_force"})
            return
        fetch_pipeline(args, full=False, allow_rebalance=True)
    run(["hugo", "--minify", "--cleanDestinationDir"])
    write_schedule_state(args.state_output, {"mode": args.mode, "skipped": False})


def check() -> None:
    parser = argparse.ArgumentParser(description="Run local pipeline sanity checks.")
    parser.add_argument("--skip-hugo", action="store_true", help="Skip Hugo static-site build.")
    args = parser.parse_args()

    run(
        [
            sys.executable,
            "-m",
            "py_compile",
            "scripts/build_live_input.py",
            "scripts/historical_backtest.py",
            "scripts/stockhunt_backend.py",
            "scripts/generate_stockhunt_data.py",
            "scripts/tasks.py",
        ]
    )
    if not args.skip_hugo:
        run(["hugo", "--minify", "--cleanDestinationDir"])
