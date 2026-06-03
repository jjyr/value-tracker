#!/usr/bin/env python3
"""Run the Value Tracker data job selected by CI."""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
from typing import List


ROOT = pathlib.Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "raw/generated/snapshot.yaml"
SIMULATION = ROOT / "raw/generated/historical_simulation.yaml"


def run(command: List[str]) -> None:
    print("+ " + " ".join(command), file=sys.stderr)
    subprocess.run(command, cwd=ROOT, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["daily", "weekly", "fetch-all"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mode == "fetch-all":
        run(["uv", "run", "fetch-all"])
        run(["uv", "run", "build"])
        return
    if args.mode == "weekly":
        run(["uv", "run", "schedule", "weekly"])
        return
    if not SNAPSHOT.exists() or not SIMULATION.exists():
        print("daily job has no cached snapshot/simulation; falling back to fetch-all", file=sys.stderr)
        run(["uv", "run", "fetch-all"])
        run(["uv", "run", "build"])
        return
    run(["uv", "run", "schedule", "daily"])


if __name__ == "__main__":
    main()
