#!/usr/bin/env python3
"""Restore the Longbridge SDK OAuth token from GitHub Actions secrets."""

from __future__ import annotations

import base64
import os
import pathlib
import stat


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"missing required environment variable: {name}")
    return value


def decode_secret(name: str, value: str) -> bytes:
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"failed to decode {name}: {exc}") from exc
    if not decoded:
        raise SystemExit(f"decoded {name} is empty")
    return decoded


def safe_token_name(value: str) -> str:
    if "/" in value or "\\" in value or value in {".", ".."}:
        raise SystemExit("LONGBRIDGE_CLIENT_ID must be a token filename, not a path")
    return value


def main() -> None:
    client_id = safe_token_name(required_env("LONGBRIDGE_CLIENT_ID"))
    token_bytes = decode_secret("LONGBRIDGE_TOKEN_FILE_B64", required_env("LONGBRIDGE_TOKEN_FILE_B64"))
    token_dir = pathlib.Path.home() / ".longbridge/openapi/tokens"
    token_dir.mkdir(parents=True, exist_ok=True)
    token_dir.chmod(0o700)
    token_path = token_dir / client_id
    token_path.write_bytes(token_bytes)
    token_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print(f"restored Longbridge token file: {token_path}")


if __name__ == "__main__":
    main()
