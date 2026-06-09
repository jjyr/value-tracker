#!/usr/bin/env python3
"""Fail CI when Longbridge CLI reports an invalid session token."""

from __future__ import annotations

import json
import sys
from typing import Any


def extract_json(text: str) -> Any:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
            return value
        except json.JSONDecodeError:
            continue
    raise ValueError("no JSON object found in Longbridge check output")


def main() -> None:
    try:
        payload = extract_json(sys.stdin.read())
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"failed to parse Longbridge check output: {exc}") from exc

    session = payload.get("session") or {}
    token_status = session.get("token")
    detail = session.get("detail") or "no session detail"
    if token_status != "valid":
        raise SystemExit(f"Longbridge token invalid: {detail}")
    print(f"Longbridge token valid: {detail}")


if __name__ == "__main__":
    main()
