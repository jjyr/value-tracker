#!/usr/bin/env python3
"""Convenience tasks for the StockHunt data/build pipeline."""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
from typing import List


ROOT = pathlib.Path(__file__).resolve().parents[1]


def run(command: List[str]) -> None:
    print("+ " + " ".join(command), file=sys.stderr)
    subprocess.run(command, cwd=ROOT, check=True)


def build_live() -> None:
    parser = argparse.ArgumentParser(description="Build StockHunt from live Longbridge data.")
    parser.add_argument("--incremental", action="store_true", help="Do not reset SQLite before recomputing metrics.")
    parser.add_argument("--skip-hugo", action="store_true", help="Skip Hugo static-site build.")
    parser.add_argument("--top", type=int, default=50, help="Top current holdings to fetch per manager.")
    parser.add_argument("--manager-limit", type=int, default=None, help="Limit managers for smoke tests.")
    args = parser.parse_args()

    live_input = ROOT / "raw/generated/live_13f_holdings.yaml"
    snapshot = ROOT / "raw/generated/snapshot.yaml"
    live_command = [sys.executable, "scripts/build_live_input.py", "--top", str(args.top), "--output", str(live_input)]
    if args.manager_limit:
        live_command.extend(["--manager-limit", str(args.manager_limit)])
    run(live_command)

    backend_command = [sys.executable, "scripts/stockhunt_backend.py", "--raw", str(live_input)]
    if not args.incremental:
        backend_command.append("--reset-db")
    run(backend_command)
    run([sys.executable, "scripts/generate_stockhunt_data.py", "--snapshot", str(snapshot)])
    if not args.skip_hugo:
        run(["hugo", "--minify"])


def build_sample() -> None:
    parser = argparse.ArgumentParser(description="Build StockHunt from the checked-in sample data.")
    parser.add_argument("--skip-hugo", action="store_true", help="Skip Hugo static-site build.")
    args = parser.parse_args()

    snapshot = ROOT / "raw/generated/snapshot.yaml"
    run([sys.executable, "scripts/stockhunt_backend.py", "--reset-db", "--raw", "raw/sample/13f_holdings.yaml"])
    run([sys.executable, "scripts/generate_stockhunt_data.py", "--snapshot", str(snapshot)])
    if not args.skip_hugo:
        run(["hugo", "--minify"])


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
            "scripts/stockhunt_backend.py",
            "scripts/generate_stockhunt_data.py",
            "scripts/tasks.py",
        ]
    )
    if not args.skip_hugo:
        run(["hugo", "--minify"])
