#!/usr/bin/env python3
"""Resolve 13F CUSIPs to Longbridge symbols."""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import time
from typing import Any, Dict, Iterable, List, Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]


def yaml_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def append_mapping(path: pathlib.Path, cusip: str, mapped: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if re.search(rf'^\s*["\']?{re.escape(cusip)}["\']?\s*:', text, flags=re.MULTILINE):
            return
        needs_newline = bool(text) and not text.endswith("\n")
    else:
        text = ""
        needs_newline = False

    lines = []
    if not text:
        lines.append("mappings:")
    elif "mappings:" not in text:
        raise ValueError(f"{path} does not contain a mappings object")
    if needs_newline:
        lines.append("")
    lines.extend(
        [
            f"  {yaml_string(cusip)}:",
            f"    symbol: {yaml_string(mapped['symbol'])}",
            f"    company_name: {yaml_string(mapped.get('company_name') or mapped['symbol'])}",
            f"    mapping_source: {yaml_string(mapped.get('mapping_source') or 'longbridge_security_list')}",
        ]
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


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
    raise ValueError(f"no JSON object found in output: {text[:200]!r}")


ABBREVIATIONS = {
    "ADR": "",
    "ADS": "",
    "CL": "CLASS",
    "COS": "COMPANIES",
    "HLDG": "HOLDINGS",
    "HLDGS": "HOLDINGS",
    "INTL": "INTERNATIONAL",
    "INSTRS": "INSTRUMENTS",
    "LABS": "LABORATORIES",
    "MKTS": "MARKETS",
    "TECH": "TECHNOLOGY",
    "TECHS": "TECHNOLOGIES",
    "SYS": "SYSTEMS",
}

DROP_TOKENS = {
    "AG",
    "CO",
    "COMPANY",
    "CORP",
    "CORPORATION",
    "DEL",
    "INC",
    "INCORPORATED",
    "LIMITED",
    "LLC",
    "LP",
    "LTD",
    "NEW",
    "NV",
    "PLC",
    "SA",
    "SE",
    "THE",
}


def name_tokens(value: str) -> List[str]:
    normalized = value.upper().replace("&", " AND ")
    normalized = re.sub(r"[^A-Z0-9]+", " ", normalized)
    tokens = []
    for token in normalized.split():
        token = ABBREVIATIONS.get(token, token)
        if token:
            tokens.append(token)
    return tokens


def strict_name_key(value: str) -> str:
    return " ".join(name_tokens(value))


def compact_name_key(value: str) -> str:
    return " ".join(token for token in name_tokens(value) if token not in DROP_TOKENS)


def first_unique(rows: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    unique: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        symbol = row.get("symbol")
        if symbol:
            unique[symbol] = row
    if len(unique) == 1:
        return next(iter(unique.values()))
    return None


class LongbridgeSymbolResolver:
    def __init__(
        self,
        mappings: Dict[str, Dict[str, Any]],
        mapping_path: Optional[pathlib.Path] = None,
        market: str = "US",
        page_size: int = 1000,
        sleep_seconds: float = 0.0,
        enabled: bool = True,
        persist: bool = True,
    ) -> None:
        self.mappings = mappings
        self.mapping_path = mapping_path
        self.market = market
        self.page_size = page_size
        self.sleep_seconds = sleep_seconds
        self.enabled = enabled
        self.persist = persist
        self._security_rows: Optional[List[Dict[str, Any]]] = None
        self._strict_index: Optional[Dict[str, List[Dict[str, Any]]]] = None
        self._compact_index: Optional[Dict[str, List[Dict[str, Any]]]] = None
        self.auto_resolved_count = 0

    def resolve(self, cusip: str, issuer_name: str = "") -> Optional[Dict[str, Any]]:
        normalized_cusip = str(cusip or "").strip().upper()
        if not normalized_cusip:
            return None
        if mapped := self.mappings.get(normalized_cusip):
            return mapped
        if not self.enabled or not issuer_name:
            return None

        try:
            row = self._match_security(issuer_name)
        except Exception:
            self.enabled = False
            raise
        if not row:
            return None

        mapped = {
            "symbol": row["symbol"],
            "company_name": row.get("name_en") or row["symbol"],
            "mapping_source": "longbridge_security_list",
        }
        self.mappings[normalized_cusip] = mapped
        if self.persist and self.mapping_path:
            append_mapping(self.mapping_path, normalized_cusip, mapped)
        self.auto_resolved_count += 1
        return mapped

    def _match_security(self, issuer_name: str) -> Optional[Dict[str, Any]]:
        self._ensure_indexes()
        assert self._strict_index is not None
        assert self._compact_index is not None

        strict_key = strict_name_key(issuer_name)
        if strict_key and (row := first_unique(self._strict_index.get(strict_key, []))):
            return row

        compact_key = compact_name_key(issuer_name)
        if compact_key and (row := first_unique(self._compact_index.get(compact_key, []))):
            return row

        return None

    def _ensure_indexes(self) -> None:
        if self._strict_index is not None and self._compact_index is not None:
            return

        strict_index: Dict[str, List[Dict[str, Any]]] = {}
        compact_index: Dict[str, List[Dict[str, Any]]] = {}
        for row in self._load_security_rows():
            names = [row.get("name_en") or "", row.get("name_cn") or ""]
            for name in names:
                if not name:
                    continue
                strict_index.setdefault(strict_name_key(name), []).append(row)
                compact_index.setdefault(compact_name_key(name), []).append(row)
        self._strict_index = strict_index
        self._compact_index = compact_index

    def _load_security_rows(self) -> List[Dict[str, Any]]:
        if self._security_rows is not None:
            return self._security_rows

        rows: List[Dict[str, Any]] = []
        page = 1
        while True:
            page_rows = self._fetch_security_page(page)
            rows.extend(page_rows)
            if len(page_rows) < self.page_size:
                break
            page += 1
        self._security_rows = rows
        return rows

    def _fetch_security_page(self, page: int) -> List[Dict[str, Any]]:
        command = [
            "longbridge",
            "security-list",
            self.market,
            "--page",
            str(page),
            "--count",
            str(self.page_size),
            "--format",
            "json",
        ]
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            stdout = completed.stdout.strip()
            detail = stderr or stdout or f"exit code {completed.returncode}"
            raise RuntimeError(f"{' '.join(command)} failed: {detail}")
        payload = extract_json(completed.stdout)
        if not isinstance(payload, list):
            raise ValueError(f"unexpected longbridge security-list payload: {type(payload).__name__}")
        return [row for row in payload if isinstance(row, dict) and row.get("symbol")]
