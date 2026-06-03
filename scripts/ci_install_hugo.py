#!/usr/bin/env python3
"""Install a pinned Hugo extended binary for CI."""

from __future__ import annotations

import argparse
import os
import pathlib
import platform
import stat
import tarfile
import tempfile
import urllib.request


DEFAULT_VERSION = "0.156.0"


def asset_name(version: str) -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "linux" and machine in {"x86_64", "amd64"}:
        target = "linux-amd64"
    elif system == "linux" and machine in {"aarch64", "arm64"}:
        target = "linux-arm64"
    elif system == "darwin" and machine in {"arm64", "aarch64"}:
        target = "darwin-arm64"
    elif system == "darwin" and machine in {"x86_64", "amd64"}:
        target = "darwin-amd64"
    else:
        raise SystemExit(f"unsupported Hugo CI platform: {system}/{machine}")
    return f"hugo_extended_{version}_{target}.tar.gz"


def install_hugo(version: str, install_dir: pathlib.Path) -> pathlib.Path:
    install_dir.mkdir(parents=True, exist_ok=True)
    name = asset_name(version)
    url = f"https://github.com/gohugoio/hugo/releases/download/v{version}/{name}"
    print(f"downloading {url}")
    with tempfile.TemporaryDirectory() as tmp:
        archive = pathlib.Path(tmp) / name
        urllib.request.urlretrieve(url, archive)
        with tarfile.open(archive, "r:gz") as tar:
            member = next((item for item in tar.getmembers() if item.name == "hugo"), None)
            if member is None:
                raise SystemExit("hugo binary not found in archive")
            tar.extract(member, tmp)
        source = pathlib.Path(tmp) / "hugo"
        target = install_dir / "hugo"
        target.write_bytes(source.read_bytes())
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"installed {target}")
    return target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=os.environ.get("HUGO_VERSION", DEFAULT_VERSION))
    parser.add_argument("--install-dir", type=pathlib.Path, default=pathlib.Path.home() / ".local/bin")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    install_hugo(args.version, args.install_dir)


if __name__ == "__main__":
    main()
