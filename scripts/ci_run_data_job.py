#!/usr/bin/env python3
"""Run the Value Tracker data job selected by CI."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
from typing import List


ROOT = pathlib.Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "raw/generated/snapshot.yaml"
SIMULATION = ROOT / "raw/generated/historical"


def run(command: List[str]) -> None:
    print("+ " + " ".join(command), file=sys.stderr)
    subprocess.run(command, cwd=ROOT, check=True)


def set_github_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def schedule_state_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("RUNNER_TEMP") or "/tmp") / "value-tracker-schedule-state.json"


def load_schedule_state(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["daily", "weekly", "fetch-all"])
    parser.add_argument("--check-only", action="store_true", help="Only run cheap early-exit checks and emit CI outputs.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.check_only:
        if args.mode == "weekly":
            state_path = schedule_state_path()
            run(["uv", "run", "schedule", "weekly", "--check-only", "--state-output", str(state_path)])
            skipped = bool(load_schedule_state(state_path).get("skipped"))
            set_github_output("skipped", "true" if skipped else "false")
        else:
            set_github_output("skipped", "false")
        return

    if args.mode == "fetch-all":
        run(["uv", "run", "fetch-all"])
        run(["uv", "run", "build"])
        set_github_output("skipped", "false")
        return
    if args.mode == "weekly":
        state_path = schedule_state_path()
        run(["uv", "run", "schedule", "weekly", "--state-output", str(state_path)])
        skipped = bool(load_schedule_state(state_path).get("skipped"))
        set_github_output("skipped", "true" if skipped else "false")
        return
    if not SNAPSHOT.exists() or not SIMULATION.exists():
        print("daily job has no cached snapshot/simulation; falling back to fetch-all", file=sys.stderr)
        run(["uv", "run", "fetch-all"])
        run(["uv", "run", "build"])
        set_github_output("skipped", "false")
        return
    run(["uv", "run", "schedule", "daily"])
    set_github_output("skipped", "false")


if __name__ == "__main__":
    main()
