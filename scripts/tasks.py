#!/usr/bin/env python3
"""Convenience tasks for the Value Tracker data/build pipeline."""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
from typing import Any, Dict, List, Optional

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "raw/generated/snapshot.yaml"
SIMULATION = ROOT / "raw/generated/historical_simulation.yaml"
DEFAULT_BACKTEST_START = "2024-01-01"


def run(command: List[str]) -> None:
    print("+ " + " ".join(command), file=sys.stderr)
    subprocess.run(command, cwd=ROOT, check=True)


def add_fetch_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--top", type=int, default=50, help="Top current holdings to fetch per manager.")
    parser.add_argument("--manager-limit", type=int, default=None, help="Limit managers for smoke tests.")
    parser.add_argument("--backtest-start", default=DEFAULT_BACKTEST_START, help="Historical backtest start date.")
    parser.add_argument("--end-date", default=None, help="Historical backtest end date. Defaults to today.")
    parser.add_argument("--live-batch-size", type=int, default=30, help="Symbols per Longbridge live market-data command.")
    parser.add_argument("--live-sleep", type=float, default=0.0, help="Seconds to sleep after each Longbridge live command.")


def last_rebalance_date(path: pathlib.Path = SIMULATION) -> Optional[str]:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return (
        payload.get("simulation", {})
        .get("meta", {})
        .get("last_rebalance_date")
    )


def run_live_input(args: argparse.Namespace) -> Dict[str, Any]:
    from scripts import build_live_input

    live_args = argparse.Namespace(
        config=ROOT / "config/stockhunt.yaml",
        cusip_map=ROOT / "config/cusip-symbols.yaml",
        top=args.top,
        manager_limit=args.manager_limit,
        batch_size=args.live_batch_size,
        sleep=args.live_sleep,
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
    ]
    if args.end_date:
        backtest_command.extend(["--end-date", args.end_date])
    if args.manager_limit:
        backtest_command.extend(["--manager-limit", str(args.manager_limit)])
    if full:
        backtest_command.extend(["--refresh-sec", "--refresh-prices"])
    elif refresh_submissions:
        backtest_command.append("--refresh-submissions")
    if not allow_rebalance:
        frozen_rebalance_date = last_rebalance_date()
        if not frozen_rebalance_date:
            raise SystemExit("daily/hold mode requires an existing historical_simulation.yaml with last_rebalance_date")
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
    args = parser.parse_args()
    if args.mode == "daily":
        run_backtest(args, full=False, allow_rebalance=False, refresh_submissions=False)
        run_export()
    else:
        fetch_pipeline(args, full=False, allow_rebalance=True)
    run(["hugo", "--minify", "--cleanDestinationDir"])


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
