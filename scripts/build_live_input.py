#!/usr/bin/env python3
"""Build normalized raw input from live Longbridge data."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys
import time
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]


class NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:
        return True


def load_yaml(path: pathlib.Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML object")
    return data


def write_yaml(path: pathlib.Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.dump(data, handle, Dumper=NoAliasDumper, allow_unicode=True, sort_keys=False, width=120)


def today() -> str:
    return dt.date.today().isoformat()


def now_iso() -> str:
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def normalize_cik(cik: Any) -> str:
    digits = "".join(ch for ch in str(cik) if ch.isdigit())
    if not digits:
        raise ValueError(f"invalid CIK: {cik!r}")
    return digits.zfill(10)


def enabled_managers(config: Dict[str, Any], manager_limit: Optional[int]) -> List[Dict[str, Any]]:
    rows = []
    for manager in config.get("institutions", {}).get("managers", []):
        if manager.get("enabled", True):
            row = dict(manager)
            row["cik"] = normalize_cik(row["cik"])
            rows.append(row)
    return rows[:manager_limit] if manager_limit else rows


def load_cusip_map(path: pathlib.Path) -> Dict[str, Dict[str, Any]]:
    raw = load_yaml(path).get("mappings", {})
    output: Dict[str, Dict[str, Any]] = {}
    for cusip, value in raw.items():
        normalized = str(cusip).strip().upper()
        if isinstance(value, str):
            output[normalized] = {"symbol": value}
        elif isinstance(value, dict) and value.get("symbol"):
            output[normalized] = dict(value)
    return output


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


class LongbridgeClient:
    def __init__(self, sleep_seconds: float = 0.0) -> None:
        self.sleep_seconds = sleep_seconds

    def run_json(self, args: List[str]) -> Any:
        command = ["longbridge", *args, "--format", "json"]
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            stdout = completed.stdout.strip()
            detail = stderr or stdout or f"exit code {completed.returncode}"
            raise RuntimeError(f"{' '.join(command)} failed: {detail}")
        return extract_json(completed.stdout)

    def investor_holdings(self, cik: str, top: int) -> Dict[str, Any]:
        return self.run_json(["investors", cik, "--top", str(top)])

    def investor_changes(self, cik: str) -> Dict[str, Any]:
        return self.run_json(["investors", "changes", cik])

    def quote(self, symbols: List[str]) -> List[Dict[str, Any]]:
        return self.run_json(["quote", *symbols]) if symbols else []

    def static(self, symbols: List[str]) -> List[Dict[str, Any]]:
        return self.run_json(["static", *symbols]) if symbols else []

    def calc_index(self, symbols: List[str]) -> List[Dict[str, Any]]:
        if not symbols:
            return []
        return self.run_json(["calc-index", *symbols, "--fields", "pe,total_market_value"])


def number(value: Any) -> Optional[float]:
    if value in (None, "", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def value_number(value: Any) -> float:
    parsed = number(value)
    return parsed if parsed is not None else 0.0


def map_holding(
    holding: Dict[str, Any],
    cusip_map: Dict[str, Dict[str, Any]],
    warnings: List[str],
    manager_name: str,
) -> Optional[Dict[str, Any]]:
    cusip = str(holding.get("cusip") or "").strip().upper()
    mapped = cusip_map.get(cusip)
    if not mapped:
        issuer = holding.get("name") or holding.get("issuer_name") or "unknown issuer"
        warnings.append(f"unmapped CUSIP {cusip} ({issuer}) from {manager_name}; skipped")
        return None
    symbol = mapped["symbol"]
    return {
        "symbol": symbol,
        "cusip": cusip,
        "issuer_name": holding.get("name") or mapped.get("company_name") or symbol,
        "share_type": holding.get("share_type", "SH"),
        "shares": value_number(holding.get("shares")),
        "value_usd": value_number(holding.get("value_usd")),
    }


def index_by_cusip(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(row.get("cusip") or "").strip().upper(): row for row in rows if row.get("cusip")}


def build_manager_filings(
    client: LongbridgeClient,
    manager: Dict[str, Any],
    top: int,
    cusip_map: Dict[str, Dict[str, Any]],
    warnings: List[str],
) -> List[Dict[str, Any]]:
    cik = manager["cik"]
    display_name = manager.get("display_name") or manager.get("name") or cik
    holdings_payload = client.investor_holdings(cik, top)
    changes_payload: Dict[str, Any] = {}
    try:
        changes_payload = client.investor_changes(cik)
    except Exception as exc:  # noqa: BLE001 - keep live build partial.
        warnings.append(f"changes unavailable for {display_name} ({cik}): {exc}")

    period = holdings_payload.get("period") or changes_payload.get("period")
    if not period:
        raise ValueError(f"Longbridge investor payload missing period for {display_name} ({cik})")
    previous_period = changes_payload.get("prev_report_date")
    filing_date = holdings_payload.get("filing_date") or changes_payload.get("filing_date") or period
    manager_name = holdings_payload.get("investor") or holdings_payload.get("firm") or manager.get("name") or display_name
    current_accession = holdings_payload.get("accession_number") or f"{cik}-{period}-longbridge"
    previous_accession = f"{cik}-{previous_period or 'previous'}-longbridge-synthetic"

    current_by_cusip = index_by_cusip(holdings_payload.get("holdings") or [])
    previous_by_cusip: Dict[str, Dict[str, Any]] = {}
    for change in changes_payload.get("changes", []) or []:
        cusip = str(change.get("cusip") or "").strip().upper()
        if not cusip:
            continue
        if value_number(change.get("shares")) > 0:
            current_by_cusip.setdefault(
                cusip,
                {
                    "cusip": cusip,
                    "name": change.get("name"),
                    "share_type": change.get("share_type", "SH"),
                    "shares": change.get("shares"),
                    "value_usd": change.get("value_usd"),
                },
            )
        if value_number(change.get("prev_shares")) > 0:
            previous_by_cusip[cusip] = {
                "cusip": cusip,
                "name": change.get("name"),
                "share_type": change.get("share_type", "SH"),
                "shares": change.get("prev_shares"),
                "value_usd": change.get("prev_value_usd"),
            }

    for cusip, current in current_by_cusip.items():
        previous_by_cusip.setdefault(cusip, current)

    current_holdings = [
        mapped
        for holding in current_by_cusip.values()
        if (mapped := map_holding(holding, cusip_map, warnings, display_name))
    ]
    previous_holdings = [
        mapped
        for holding in previous_by_cusip.values()
        if (mapped := map_holding(holding, cusip_map, warnings, display_name))
    ]

    filings = [
        {
            "accession_number": current_accession,
            "cik": cik,
            "manager_name": manager_name,
            "filing_type": "13F-HR",
            "filing_date": filing_date,
            "report_period": period,
            "sec_url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{current_accession.replace('-', '')}",
            "source": "longbridge_investors",
            "holdings": current_holdings,
        }
    ]
    if previous_period:
        filings.append(
            {
                "accession_number": previous_accession,
                "cik": cik,
                "manager_name": manager_name,
                "filing_type": "13F-HR",
                "filing_date": previous_period,
                "report_period": previous_period,
                "sec_url": None,
                "source": "longbridge_investors_synthetic_previous",
                "holdings": previous_holdings,
            }
        )
    return filings


def chunks(values: List[str], size: int) -> Iterable[List[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def first_by_symbol(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    output: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        symbol = row.get("symbol")
        if symbol and symbol not in output:
            output[symbol] = row
    return output


def enrich_market(
    client: LongbridgeClient,
    symbols: List[str],
    cusip_map: Dict[str, Dict[str, Any]],
    warnings: List[str],
    batch_size: int,
) -> List[Dict[str, Any]]:
    quote_rows: List[Dict[str, Any]] = []
    static_rows: List[Dict[str, Any]] = []
    calc_rows: List[Dict[str, Any]] = []
    for batch in chunks(symbols, batch_size):
        try:
            quote_rows.extend(client.quote(batch))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"quote unavailable for {','.join(batch)}: {exc}")
        try:
            static_rows.extend(client.static(batch))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"static unavailable for {','.join(batch)}: {exc}")
        try:
            calc_rows.extend(client.calc_index(batch))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"calc-index unavailable for {','.join(batch)}: {exc}")

    quotes = first_by_symbol(quote_rows)
    statics = first_by_symbol(static_rows)
    calcs = first_by_symbol(calc_rows)
    mapping_by_symbol = {value["symbol"]: value for value in cusip_map.values() if value.get("symbol")}
    market = []
    for symbol in symbols:
        quote = quotes.get(symbol, {})
        static = statics.get(symbol, {})
        calc = calcs.get(symbol, {})
        mapped = mapping_by_symbol.get(symbol, {})
        last = number(quote.get("last") or quote.get("last_done"))
        prev_close = number(quote.get("prev_close"))
        change_pct = None
        if last is not None and prev_close:
            change_pct = round((last - prev_close) / prev_close * 100, 4)
        market.append(
            {
                "symbol": symbol,
                "company_name": mapped.get("company_name") or static.get("name") or symbol,
                "exchange": static.get("exchange"),
                "sector": mapped.get("sector"),
                "industry": mapped.get("industry"),
                "tags": mapped.get("tags") or [],
                "detail_tags": mapped.get("detail_tags") or mapped.get("tags") or [],
                "risk_tags": mapped.get("risk_tags") or [],
                "price": last,
                "price_change_pct": change_pct,
                "market_cap_usd": number(calc.get("total_market_value")),
                "pe": number(calc.get("pe")),
                "forward_pe": number(calc.get("forward_pe")),
                "ps": number(calc.get("ps")),
                "source": "longbridge",
            }
        )
    return market


def most_common(values: Iterable[Optional[str]]) -> Optional[str]:
    cleaned = [value for value in values if value]
    if not cleaned:
        return None
    return Counter(cleaned).most_common(1)[0][0]


def build_live_raw(args: argparse.Namespace) -> Dict[str, Any]:
    config = load_yaml(args.config)
    cusip_map = load_cusip_map(args.cusip_map)
    client = LongbridgeClient(sleep_seconds=args.sleep)
    warnings: List[str] = []
    managers = enabled_managers(config, args.manager_limit)
    filings: List[Dict[str, Any]] = []
    for manager in managers:
        display_name = manager.get("display_name") or manager.get("name") or manager["cik"]
        print(f"fetching 13F for {display_name} ({manager['cik']})", file=sys.stderr)
        try:
            filings.extend(build_manager_filings(client, manager, args.top, cusip_map, warnings))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"investor fetch failed for {display_name} ({manager['cik']}): {exc}")

    latest_period = most_common(filing.get("report_period") for filing in filings if filing.get("source") != "longbridge_investors_synthetic_previous")
    previous_period = most_common(filing.get("report_period") for filing in filings if filing.get("source") == "longbridge_investors_synthetic_previous")
    symbols = sorted(
        {
            holding["symbol"]
            for filing in filings
            for holding in filing.get("holdings", [])
            if holding.get("symbol")
        }
    )
    print(f"enriching market data for {len(symbols)} symbols", file=sys.stderr)
    market = enrich_market(client, symbols, cusip_map, warnings, args.batch_size)

    warnings = list(dict.fromkeys(warnings))
    return {
        "data_date": args.data_date or today(),
        "market_data_date": args.market_data_date or args.data_date or today(),
        "latest_13f_report_period": latest_period,
        "previous_13f_report_period": previous_period,
        "build": {
            "build_id": f"live-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}",
            "built_at": now_iso(),
            "metrics_version": "0.1",
            "status": "OK" if filings else "partial",
            "warnings": warnings,
        },
        "market": market,
        "filings": filings,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=pathlib.Path, default=ROOT / "config/stockhunt.yaml")
    parser.add_argument("--cusip-map", type=pathlib.Path, default=ROOT / "config/cusip-symbols.yaml")
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "raw/generated/live_13f_holdings.yaml")
    parser.add_argument("--top", type=int, default=50, help="Top current holdings to fetch per manager from Longbridge.")
    parser.add_argument("--manager-limit", type=int, default=None, help="Limit enabled managers for smoke tests.")
    parser.add_argument("--batch-size", type=int, default=30, help="Symbols per Longbridge market-data command.")
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep after each Longbridge command.")
    parser.add_argument("--data-date", default=None)
    parser.add_argument("--market-data-date", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = build_live_raw(args)
    if not data.get("latest_13f_report_period"):
        raise SystemExit("no latest 13F report period found; see warnings in generated output")
    write_yaml(args.output, data)
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
