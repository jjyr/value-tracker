#!/usr/bin/env python3
"""Build a weekly historical portfolio simulation from SEC 13F filings."""

from __future__ import annotations

import argparse
import bisect
import copy
import datetime as dt
import hashlib
import json
import math
import pathlib
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import generate_stockhunt_data as hugo_data
from scripts import historical_store
from scripts.build_live_input import extract_json, load_cusip_map, normalize_cik
from scripts.symbol_resolver import LongbridgeSymbolResolver


DEFAULT_START_DATE = "2024-01-01"
BENCHMARKS = {"spy": "SPY.US", "qqq": "QQQ.US"}
CHART_COLORS = {
    "portfolio": "#54d690",
    "spy": "#67d4ff",
    "qqq": "#b69cff",
    "institutions": ["#f3c969", "#ff8a65", "#7dd3fc", "#c4b5fd", "#f472b6", "#a3e635"],
}


def parse_date(value: Any) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def today() -> str:
    return dt.date.today().isoformat()


def now_iso() -> str:
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def stable_hash(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def number(value: Any) -> Optional[float]:
    if value in (None, "", "--"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def value_number(value: Any) -> float:
    parsed = number(value)
    return parsed if parsed is not None else 0.0


def enabled_managers(config: Dict[str, Any], manager_limit: Optional[int] = None) -> List[Dict[str, Any]]:
    managers = []
    for manager in config.get("institutions", {}).get("managers", []):
        if not manager.get("enabled", True):
            continue
        row = dict(manager)
        row["cik"] = normalize_cik(row["cik"])
        managers.append(row)
    return managers[:manager_limit] if manager_limit else managers


def key_ciks(config: Dict[str, Any]) -> set:
    members = config.get("key_institutions", {}).get("members", [])
    return {normalize_cik(member["cik"]) for member in members if member.get("enabled", True)}


def key_names(config: Dict[str, Any]) -> set:
    members = config.get("key_institutions", {}).get("members", [])
    return {member.get("display_name") for member in members if member.get("enabled", True)}


def cache_read(path: pathlib.Path) -> Optional[Any]:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def cache_write(path: pathlib.Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)


class SecClient:
    def __init__(
        self,
        cache_dir: pathlib.Path,
        user_agent: str,
        sleep_seconds: float = 0.1,
        refresh: bool = False,
        refresh_submissions: bool = False,
    ) -> None:
        self.cache_dir = cache_dir
        self.user_agent = user_agent
        self.sleep_seconds = sleep_seconds
        self.refresh = refresh
        self.refresh_submissions = refresh_submissions

    def fetch_json(self, url: str, cache_name: str, force_refresh: bool = False) -> Any:
        cache_path = self.cache_dir / "sec" / f"{cache_name}.json"
        if not (self.refresh or force_refresh) and (cached := cache_read(cache_path)) is not None:
            return cached
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        cache_write(cache_path, data)
        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)
        return data

    def fetch_text(self, url: str, cache_name: str) -> str:
        cache_path = self.cache_dir / "sec" / f"{cache_name}.txt"
        if not self.refresh and cache_path.exists():
            return cache_path.read_text(encoding="utf-8")
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8", errors="replace")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="utf-8")
        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)
        return text

    def submissions(self, cik: str) -> Dict[str, Any]:
        return self.fetch_json(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            f"submissions-{cik}",
            force_refresh=self.refresh_submissions,
        )

    def filing_index(self, cik: str, accession_number: str) -> Dict[str, Any]:
        accession = accession_number.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/index.json"
        return self.fetch_json(url, f"index-{cik}-{accession}")

    def filing_text(self, cik: str, accession_number: str, file_name: str) -> str:
        accession = accession_number.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{file_name}"
        return self.fetch_text(url, f"filing-{cik}-{accession}-{file_name.replace('/', '_')}")


class LongbridgePriceClient:
    def __init__(
        self,
        cache_dir: pathlib.Path,
        sleep_seconds: float = 0.0,
        refresh: bool = False,
        cache_only: bool = False,
    ) -> None:
        self.cache_dir = cache_dir
        self.sleep_seconds = sleep_seconds
        self.refresh = refresh
        self.cache_only = cache_only

    def history(self, symbol: str, start_date: str, end_date: str) -> Dict[str, float]:
        cache_dir = self.cache_dir / "longbridge-kline"
        symbol_key = symbol.replace(".", "-")
        cache_path = cache_dir / f"{symbol_key}-day-forward.json"
        cached = {} if self.refresh else self._cached_rows(cache_dir, symbol_key, cache_path)
        start = parse_date(start_date)
        end = parse_date(end_date)

        ranges: List[Tuple[dt.date, dt.date]] = []
        if not cached:
            ranges.append((start, end))
        else:
            cached_dates = sorted(parse_date(date) for date in cached)
            if start < cached_dates[0]:
                ranges.append((start, cached_dates[0] - dt.timedelta(days=1)))
            if end > cached_dates[-1]:
                ranges.append((cached_dates[-1] + dt.timedelta(days=1), end))

        for range_start, range_end in ranges:
            if range_start <= range_end:
                if self.cache_only:
                    continue
                cached.update(self._fetch_range(symbol, range_start.isoformat(), range_end.isoformat()))

        if cached:
            rows = [{"date": date, "close": cached[date]} for date in sorted(cached)]
            cache_write(cache_path, rows)
        return {date: close for date, close in cached.items() if start_date <= date <= end_date}

    def _cached_rows(self, cache_dir: pathlib.Path, symbol_key: str, cache_path: pathlib.Path) -> Dict[str, float]:
        rows: Dict[str, float] = {}
        paths = []
        if cache_path.exists():
            paths.append(cache_path)
        paths.extend(sorted(cache_dir.glob(f"{symbol_key}-*-day-forward.json")))
        for path in paths:
            cached = cache_read(path)
            if not isinstance(cached, list):
                continue
            for row in cached:
                close = number(row.get("close"))
                date = str(row.get("date") or "")[:10]
                if date and close is not None:
                    rows[date] = close
        return rows

    def _fetch_range(self, symbol: str, start_date: str, end_date: str) -> Dict[str, float]:
        command = [
            "longbridge",
            "kline",
            "history",
            symbol,
            "--start",
            start_date,
            "--end",
            end_date,
            "--period",
            "day",
            "--adjust",
            "forward",
            "--format",
            "json",
        ]
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
            raise RuntimeError(f"{' '.join(command)} failed: {detail}")
        rows = []
        for row in extract_json(completed.stdout):
            close = number(row.get("close"))
            if close is None:
                continue
            rows.append({"date": str(row["time"])[:10], "close": close})
        return {row["date"]: float(row["close"]) for row in rows}


def recent_13f_metadata(
    client: SecClient,
    manager: Dict[str, Any],
    min_report_date: dt.date,
    end_date: dt.date,
) -> List[Dict[str, Any]]:
    cik = manager["cik"]
    recent = client.submissions(cik).get("filings", {}).get("recent", {})
    accessions = recent.get("accessionNumber", [])
    output = []
    for index, accession_number in enumerate(accessions):
        form = (recent.get("form", [])[index] or "").upper()
        if form not in {"13F-HR", "13F-HR/A"}:
            continue
        filing_date = parse_date(recent["filingDate"][index])
        report_date = parse_date(recent["reportDate"][index])
        if report_date < min_report_date or filing_date > end_date:
            continue
        output.append(
            {
                "accession_number": accession_number,
                "cik": cik,
                "manager_name": manager.get("name") or manager.get("display_name") or cik,
                "filing_type": form,
                "filing_date": filing_date.isoformat(),
                "report_period": report_date.isoformat(),
                "is_amendment": form.endswith("/A"),
                "primary_document": recent.get("primaryDocument", [None])[index],
                "source": "sec_edgar",
            }
        )
    output.sort(key=lambda row: (row["filing_date"], row["report_period"], row["accession_number"]))
    return output


def info_table_name(index_payload: Dict[str, Any]) -> Optional[str]:
    items = index_payload.get("directory", {}).get("item", [])
    xml_names = [item.get("name") for item in items if str(item.get("name", "")).lower().endswith(".xml")]
    preferred = [name for name in xml_names if name and pathlib.PurePosixPath(name).name != "primary_doc.xml"]
    return preferred[0] if preferred else None


def child_text(node: ET.Element, local_name: str) -> Optional[str]:
    for child in node.iter():
        if child.tag.rsplit("}", 1)[-1] == local_name:
            return child.text
    return None


def parse_info_table(
    xml_text: str,
    symbol_resolver: LongbridgeSymbolResolver,
    warnings: List[str],
    manager_name: str,
) -> List[Dict[str, Any]]:
    root = ET.fromstring(xml_text.encode("utf-8"))
    holdings = []
    for info in root.iter():
        if info.tag.rsplit("}", 1)[-1] != "infoTable":
            continue
        put_call = child_text(info, "putCall")
        share_type = child_text(info, "sshPrnamtType") or "SH"
        if put_call or share_type.upper() != "SH":
            continue
        cusip = (child_text(info, "cusip") or "").strip().upper()
        issuer = child_text(info, "nameOfIssuer") or "unknown issuer"
        try:
            mapped = symbol_resolver.resolve(cusip, issuer)
        except Exception as exc:  # noqa: BLE001 - keep backtest partial.
            warnings.append(f"symbol auto-map unavailable for {cusip} ({issuer}) from {manager_name}: {exc}")
            mapped = symbol_resolver.mappings.get(cusip)
        if not mapped:
            warnings.append(f"unmapped CUSIP {cusip} ({issuer}) from {manager_name}; skipped")
            continue
        holdings.append(
            {
                "symbol": mapped["symbol"],
                "cusip": cusip,
                "issuer_name": child_text(info, "nameOfIssuer") or mapped.get("company_name") or mapped["symbol"],
                "share_type": share_type,
                "shares": value_number(child_text(info, "sshPrnamt")),
                "value_usd": value_number(child_text(info, "value")),
            }
        )
    return holdings


def fetch_historical_filings(
    config: Dict[str, Any],
    symbol_resolver: LongbridgeSymbolResolver,
    args: argparse.Namespace,
    warnings: List[str],
) -> List[Dict[str, Any]]:
    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)
    min_report_date = start_date - dt.timedelta(days=450)
    client = SecClient(args.cache_dir, args.sec_user_agent, args.sec_sleep, args.refresh_sec, args.refresh_submissions)
    filings = []
    for manager in enabled_managers(config, args.manager_limit):
        display_name = manager.get("display_name") or manager.get("name") or manager["cik"]
        print(f"fetching SEC 13F history for {display_name} ({manager['cik']})")
        for filing in recent_13f_metadata(client, manager, min_report_date, end_date):
            try:
                index_payload = client.filing_index(manager["cik"], filing["accession_number"])
                table_name = info_table_name(index_payload)
                if not table_name:
                    warnings.append(f"missing information table XML for {display_name} {filing['accession_number']}")
                    continue
                xml_text = client.filing_text(manager["cik"], filing["accession_number"], table_name)
                filing["sec_url"] = (
                    f"https://www.sec.gov/Archives/edgar/data/{int(manager['cik'])}/"
                    f"{filing['accession_number'].replace('-', '')}/{table_name}"
                )
                filing["holdings"] = parse_info_table(xml_text, symbol_resolver, warnings, display_name)
                filings.append(filing)
            except Exception as exc:  # noqa: BLE001 - keep backtest partial.
                warnings.append(f"failed to parse {display_name} {filing['accession_number']}: {exc}")
    filings.sort(key=lambda row: (row["filing_date"], row["cik"], row["report_period"], row["accession_number"]))
    return filings


def filing_fingerprint_rows(filings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for filing in filings:
        holdings = [
            {
                "cusip": holding.get("cusip"),
                "symbol": holding.get("symbol"),
                "shares": holding.get("shares"),
                "value_usd": holding.get("value_usd"),
            }
            for holding in filing.get("holdings") or []
        ]
        rows.append(
            {
                "cik": normalize_cik(filing.get("cik")),
                "accession_number": filing.get("accession_number"),
                "filing_date": filing.get("filing_date"),
                "report_period": filing.get("report_period"),
                "holdings_hash": stable_hash(sorted(holdings, key=lambda row: (str(row.get("symbol")), str(row.get("cusip"))))),
            }
        )
    return sorted(rows, key=lambda row: (str(row.get("cik")), str(row.get("accession_number"))))


def changed_filing_dates(old_rows: List[Dict[str, Any]], new_rows: List[Dict[str, Any]]) -> List[dt.date]:
    def keyed(rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
        return {
            (normalize_cik(row.get("cik")), str(row.get("accession_number") or "")): row
            for row in rows
            if row.get("cik") and row.get("accession_number")
        }

    old_by_key = keyed(old_rows)
    new_by_key = keyed(new_rows)
    dates = []
    for key, row in new_by_key.items():
        if old_by_key.get(key) != row and row.get("filing_date"):
            dates.append(parse_date(row["filing_date"]))
    for key, row in old_by_key.items():
        if key not in new_by_key and row.get("filing_date"):
            dates.append(parse_date(row["filing_date"]))
    return sorted(dates)


def infer_dirty_from(
    args: argparse.Namespace,
    existing_payload: Dict[str, Any],
    config_hash_value: str,
    cusip_map_hash_value: str,
    filing_fingerprint: List[Dict[str, Any]],
) -> Optional[dt.date]:
    if not args.incremental or not existing_payload:
        return None
    if args.manager_limit or args.symbol_limit:
        print("incremental disabled: manager/symbol limit set", file=sys.stderr)
        return None
    if args.dirty_from:
        return parse_date(args.dirty_from)

    build = existing_payload.get("build") or {}
    if build.get("config_hash") != config_hash_value:
        print("incremental disabled: config hash changed or missing", file=sys.stderr)
        return None
    if build.get("cusip_map_hash") != cusip_map_hash_value:
        print("incremental disabled: CUSIP map hash changed or missing", file=sys.stderr)
        return None

    changed_dates = changed_filing_dates(build.get("filing_fingerprint") or [], filing_fingerprint)
    if changed_dates:
        dirty_from = min(changed_dates)
        print(f"incremental dirty_from from changed 13F filing_date: {dirty_from}", file=sys.stderr)
        return dirty_from

    last_date = historical_store.last_equity_date(existing_payload)
    if last_date:
        dirty_from = parse_date(last_date)
        print(f"incremental dirty_from from latest equity point: {dirty_from}", file=sys.stderr)
        return dirty_from
    return None


def aggregate_holdings(holdings: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    output: Dict[str, Dict[str, Any]] = {}
    for holding in holdings:
        symbol = holding["symbol"]
        row = output.setdefault(
            symbol,
            {
                "symbol": symbol,
                "issuer_name": holding.get("issuer_name") or symbol,
                "cusip": holding.get("cusip"),
                "shares": 0.0,
                "value_usd": 0.0,
            },
        )
        row["shares"] += float(holding.get("shares") or 0)
        row["value_usd"] += float(holding.get("value_usd") or 0)
    return output


def filing_sort_key(filing: Dict[str, Any]) -> Tuple[str, str, int, str]:
    return (
        filing["report_period"],
        filing["filing_date"],
        int(bool(filing.get("is_amendment"))),
        filing["accession_number"],
    )


def group_filings_by_cik(filings: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    output: Dict[str, List[Dict[str, Any]]] = {}
    for filing in filings:
        filing = dict(filing)
        filing["aggregated_holdings"] = aggregate_holdings(filing.get("holdings") or [])
        output.setdefault(normalize_cik(filing["cik"]), []).append(filing)
    for rows in output.values():
        rows.sort(key=filing_sort_key)
    return output


def selected_filing(rows: List[Dict[str, Any]], as_of: dt.date) -> Optional[Dict[str, Any]]:
    eligible = [row for row in rows if parse_date(row["filing_date"]) <= as_of]
    return max(eligible, key=filing_sort_key) if eligible else None


def previous_filing(rows: List[Dict[str, Any]], current: Dict[str, Any], as_of: dt.date) -> Optional[Dict[str, Any]]:
    current_period = parse_date(current["report_period"])
    eligible = [
        row
        for row in rows
        if parse_date(row["filing_date"]) <= as_of and parse_date(row["report_period"]) < current_period
    ]
    return max(eligible, key=filing_sort_key) if eligible else None


def build_price_index(price_history: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, Any]]:
    output = {}
    for symbol, rows in price_history.items():
        dates = sorted(rows)
        output[symbol] = {"dates": dates, "prices": [rows[date] for date in dates]}
    return output


def median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def normalize_filing_value_units(
    filings: List[Dict[str, Any]],
    price_index: Dict[str, Dict[str, Any]],
    warnings: List[str],
) -> None:
    for filing in filings:
        report_period = parse_date(filing["report_period"])
        ratios = []
        for holding in filing.get("holdings") or []:
            shares = float(holding.get("shares") or 0)
            value = float(holding.get("value_usd") or 0)
            price = price_at(price_index, holding["symbol"], report_period)
            if price is None and holding["symbol"] in price_index and price_index[holding["symbol"]]["prices"]:
                price = price_index[holding["symbol"]]["prices"][0]
            if shares > 0 and value > 0 and price and price > 1:
                ratios.append((value / shares) / price)
        value_ratio = median(ratios)
        if value_ratio is not None and value_ratio < 0.2:
            for holding in filing.get("holdings") or []:
                holding["value_usd"] = float(holding.get("value_usd") or 0) * 1000
            warnings.append(
                f"scaled 13F values by 1000 for {filing['manager_name']} {filing['report_period']} "
                f"({filing['accession_number']})"
            )


def price_at(price_index: Dict[str, Dict[str, Any]], symbol: str, date: dt.date) -> Optional[float]:
    series = price_index.get(symbol)
    if not series:
        return None
    dates = series["dates"]
    index = bisect.bisect_right(dates, date.isoformat()) - 1
    if index < 0:
        return None
    return float(series["prices"][index])


def trading_dates(price_index: Dict[str, Dict[str, Any]], start_date: dt.date, end_date: dt.date) -> List[dt.date]:
    dates = price_index.get(BENCHMARKS["spy"], {}).get("dates", [])
    return [parse_date(date) for date in dates if start_date <= parse_date(date) <= end_date]


def next_trading_day(date: dt.date, trading_days: List[dt.date]) -> Optional[dt.date]:
    index = bisect.bisect_left(trading_days, date)
    return trading_days[index] if index < len(trading_days) else None


def weekly_rebalance_dates(start_date: dt.date, end_date: dt.date, trading_days: List[dt.date]) -> List[dt.date]:
    current = start_date
    while current.weekday() != 0:
        current += dt.timedelta(days=1)
    output = []
    seen = set()
    while current <= end_date:
        trading_day = next_trading_day(current, trading_days)
        if trading_day and trading_day <= end_date and trading_day not in seen:
            output.append(trading_day)
            seen.add(trading_day)
        current += dt.timedelta(days=7)
    return output


def status_for_change(previous: Optional[Dict[str, Any]], current: Optional[Dict[str, Any]], previous_known: bool) -> str:
    previous_shares = float(previous.get("shares") or 0) if previous else 0.0
    current_shares = float(current.get("shares") or 0) if current else 0.0
    if current_shares > 0 and not previous_known:
        return "unknown_previous"
    if previous_shares == 0 and current_shares > 0:
        return "new_position"
    if previous_shares > 0 and current_shares == 0:
        return "exited"
    if current_shares > previous_shares:
        return "added"
    if 0 < current_shares < previous_shares:
        return "reduced"
    if current_shares > 0 and current_shares == previous_shares:
        return "unchanged"
    return "not_held"


def signed_change_value(status: str, previous: Optional[Dict[str, Any]], current: Optional[Dict[str, Any]], price: float) -> float:
    previous_shares = float(previous.get("shares") or 0) if previous else 0.0
    current_shares = float(current.get("shares") or 0) if current else 0.0
    if status in {"new_position", "unknown_previous"}:
        return float(current.get("value_usd") or 0) if current else 0.0
    if status == "added":
        return (current_shares - previous_shares) * price
    if status == "reduced":
        return -(previous_shares - current_shares) * price
    if status == "exited":
        return -(previous_shares * price)
    return 0.0


def metric_seed(symbol: str, manager_count: int) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "manager_count": manager_count,
        "buyers_count": 0,
        "sellers_count": 0,
        "holders_count": 0,
        "new_positions_count": 0,
        "added_count": 0,
        "reduced_count": 0,
        "exits_count": 0,
        "total_bought_value_usd": 0.0,
        "total_sold_value_usd": 0.0,
        "new_position_value_usd": 0.0,
        "exit_value_usd": 0.0,
        "total_tracked_value_usd": 0.0,
        "total_tracked_shares": 0.0,
        "institutional_avg_holding_price": 0.0,
        "latest_institutional_buy_price": 0.0,
        "_latest_buy_value_usd": 0.0,
        "_latest_buy_shares": 0.0,
        "key_institution_bought": False,
        "key_institution_bought_value_usd": 0.0,
        "key_institution_holders": [],
        "managers": [],
    }


def market_metadata(cusip_map: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {value["symbol"]: value for value in cusip_map.values() if value.get("symbol")}


def metrics_as_of(
    config: Dict[str, Any],
    filings_by_cik: Dict[str, List[Dict[str, Any]]],
    price_index: Dict[str, Dict[str, Any]],
    cusip_map: Dict[str, Dict[str, Any]],
    as_of: dt.date,
) -> List[Dict[str, Any]]:
    managers = enabled_managers(config)
    manager_by_cik = {manager["cik"]: manager for manager in managers}
    key_set = key_ciks(config)
    metadata = market_metadata(cusip_map)
    current_by_cik = {}
    previous_by_cik = {}
    symbols = set()
    for cik in manager_by_cik:
        rows = filings_by_cik.get(cik) or []
        current = selected_filing(rows, as_of)
        if not current:
            continue
        previous = previous_filing(rows, current, as_of)
        current_by_cik[cik] = current
        previous_by_cik[cik] = previous
        symbols.update(current.get("aggregated_holdings", {}))
        if previous:
            symbols.update(previous.get("aggregated_holdings", {}))
    portfolio_value_by_cik = {
        cik: sum(float(holding.get("value_usd") or 0) for holding in filing.get("aggregated_holdings", {}).values())
        for cik, filing in current_by_cik.items()
    }

    metrics = {symbol: metric_seed(symbol, len(managers)) for symbol in symbols}
    for symbol in sorted(symbols):
        metric = metrics[symbol]
        for cik, manager in manager_by_cik.items():
            current_filing = current_by_cik.get(cik)
            if not current_filing:
                continue
            previous_report = previous_by_cik.get(cik)
            cur = current_filing.get("aggregated_holdings", {}).get(symbol)
            prev = previous_report.get("aggregated_holdings", {}).get(symbol) if previous_report else None
            if not cur and not prev:
                continue
            previous_known = bool(previous_report)
            status = status_for_change(prev, cur, previous_known)
            if status == "not_held":
                continue
            current_shares = float(cur.get("shares") or 0) if cur else 0.0
            previous_shares = float(prev.get("shares") or 0) if prev else 0.0
            current_value = float(cur.get("value_usd") or 0) if cur else 0.0
            previous_value = float(prev.get("value_usd") or 0) if prev else 0.0
            portfolio_total = portfolio_value_by_cik.get(cik) or 0.0
            portfolio_weight = current_value / portfolio_total * 100 if portfolio_total > 0 and current_value > 0 else 0.0
            report_price = current_value / current_shares if current_shares > 0 and current_value > 0 else None
            if report_price is None:
                report_price = price_at(price_index, symbol, as_of) or 0.0
            change_value = signed_change_value(status, prev, cur, report_price)

            if current_shares > 0:
                metric["holders_count"] += 1
                metric["total_tracked_value_usd"] += current_value
                metric["total_tracked_shares"] += current_shares
                if cik in key_set:
                    metric["key_institution_holders"].append(hugo_data.manager_display_fields(manager))
            if status in {"new_position", "added"}:
                metric["buyers_count"] += 1
                if status == "new_position":
                    metric["new_positions_count"] += 1
                    metric["new_position_value_usd"] += current_value
                    bought_value = current_value
                    bought_shares = current_shares
                else:
                    metric["added_count"] += 1
                    bought_value = max(change_value, 0)
                    bought_shares = max(current_shares - previous_shares, 0)
                metric["total_bought_value_usd"] += bought_value
                if bought_value > 0 and bought_shares > 0:
                    metric["_latest_buy_value_usd"] += bought_value
                    metric["_latest_buy_shares"] += bought_shares
                if cik in key_set:
                    metric["key_institution_bought"] = True
                    metric["key_institution_bought_value_usd"] += bought_value
            elif status in {"reduced", "exited"}:
                metric["sellers_count"] += 1
                sold_value = abs(change_value)
                metric["total_sold_value_usd"] += sold_value
                if status == "reduced":
                    metric["reduced_count"] += 1
                else:
                    metric["exits_count"] += 1
                    metric["exit_value_usd"] += sold_value

            metric["managers"].append(
                {
                    "cik": cik,
                    "name": manager.get("name"),
                    **hugo_data.manager_display_fields(manager),
                    "status": status,
                    "previous_shares": previous_shares,
                    "current_shares": current_shares,
                    "change_shares": current_shares - previous_shares,
                    "change_value_usd": change_value,
                    "current_value_usd": current_value,
                    "portfolio_weight_pct": portfolio_weight,
                    "filing_date": current_filing["filing_date"],
                    "report_period": current_filing["report_period"],
                }
            )
        if metric["total_tracked_shares"] > 0:
            metric["institutional_avg_holding_price"] = metric["total_tracked_value_usd"] / metric["total_tracked_shares"]
        if metric["_latest_buy_shares"] > 0:
            metric["latest_institutional_buy_price"] = metric["_latest_buy_value_usd"] / metric["_latest_buy_shares"]

    rows = []
    for symbol, metric in metrics.items():
        price = price_at(price_index, symbol, as_of)
        if price is None:
            continue
        mapped = metadata.get(symbol, {})
        row = {
            "symbol": symbol,
            "slug": hugo_data.slugify_symbol(symbol),
            "company_name": mapped.get("company_name") or symbol,
            "tags": mapped.get("tags") or [],
            "price": price,
            "price_change_pct": None,
            "market_cap_usd": None,
            "pe": None,
            "forward_pe": None,
            "ps": None,
            **metric,
        }
        row.pop("_latest_buy_value_usd", None)
        row.pop("_latest_buy_shares", None)
        rows.append(row)
    return rows


def ranks_for_rows(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    return {
        "buying": hugo_data.rank_by_value(rows, "total_bought_value_usd"),
        "selling": hugo_data.rank_by_value(rows, "total_sold_value_usd"),
        "new": hugo_data.rank_by_value(rows, "new_position_value_usd"),
        "exit": hugo_data.rank_by_value(rows, "exit_value_usd"),
        "holding": hugo_data.rank_holding(rows),
    }


def candidates_for_date(
    config: Dict[str, Any],
    rows: List[Dict[str, Any]],
    ranks: Dict[str, Dict[str, int]],
    existing_symbols: Optional[set] = None,
) -> List[Dict[str, Any]]:
    return hugo_data.simulation_candidates(config, rows, ranks, existing_symbols)


def portfolio_value(positions: Dict[str, Dict[str, Any]], cash: float, price_index: Dict[str, Dict[str, Any]], date: dt.date) -> float:
    value = cash
    for symbol, position in positions.items():
        price = price_at(price_index, symbol, date)
        if price is None:
            price = position.get("last_price") or position.get("avg_cost") or 0.0
        position["last_price"] = price
        value += position["shares"] * price
    return value


def percent_change(current: float, previous: Optional[float]) -> float:
    if previous in (None, 0):
        return 0.0
    return (current - previous) / previous * 100


def max_drawdown_pct(values: List[float]) -> float:
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            max_drawdown = min(max_drawdown, (value - peak) / peak * 100)
    return max_drawdown


def build_benchmark_value(
    price_index: Dict[str, Dict[str, Any]],
    symbol: str,
    date: dt.date,
    start_price: Optional[float],
    initial_value: float,
) -> float:
    price = price_at(price_index, symbol, date)
    if not price or not start_price:
        return initial_value
    return initial_value * price / start_price


def base_chart_series() -> List[Dict[str, Any]]:
    return [
        {
            "key": "value",
            "valueKey": "return_pct",
            "amountKey": "value",
            "label": "Value Tracker",
            "label_en": "Value Tracker",
            "label_key": "site_title",
            "className": "line-portfolio",
            "pointClass": "point-portfolio",
            "color": CHART_COLORS["portfolio"],
        },
        {
            "key": "spy_value",
            "valueKey": "spy_return_pct",
            "amountKey": "spy_value",
            "label": "SPY",
            "className": "line-spy",
            "pointClass": "point-spy",
            "color": CHART_COLORS["spy"],
        },
        {
            "key": "qqq_value",
            "valueKey": "qqq_return_pct",
            "amountKey": "qqq_value",
            "label": "QQQ",
            "className": "line-qqq",
            "pointClass": "point-qqq",
            "color": CHART_COLORS["qqq"],
        },
    ]


def report_price_for_holding(holding: Dict[str, Any]) -> Optional[float]:
    shares = float(holding.get("shares") or 0)
    value = float(holding.get("value_usd") or 0)
    if shares <= 0 or value <= 0:
        return None
    return value / shares


def unique_report_filings(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    latest_by_period: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        period = row.get("report_period")
        if not period:
            continue
        current = latest_by_period.get(period)
        if current is None or filing_sort_key(row) > filing_sort_key(current):
            latest_by_period[period] = row
    return sorted(latest_by_period.values(), key=lambda row: (row["report_period"], row["filing_date"], row["accession_number"]))


def first_trading_day_on_or_after(trading_days: List[dt.date], date: dt.date) -> Optional[dt.date]:
    index = bisect.bisect_left(trading_days, date)
    if index >= len(trading_days):
        return None
    return trading_days[index]


def report_context_for_date(points: List[Dict[str, Any]], date: dt.date) -> Dict[str, Any]:
    if not points:
        return {}
    dates = [point["date_value"] for point in points]
    index = bisect.bisect_right(dates, date)
    if index <= 0:
        return points[0]
    return points[index - 1]


def institution_report_profit_index(
    report: Dict[str, Any],
    price_index: Dict[str, Dict[str, Any]],
    date: dt.date,
) -> Tuple[float, float, float]:
    market_value = 0.0
    cost_value = 0.0
    for symbol, holding in (report.get("holdings") or {}).items():
        shares = float(holding.get("shares") or 0)
        report_price = float(holding.get("report_price") or 0)
        avg_cost = float(holding.get("avg_cost") or 0)
        if shares <= 0 or avg_cost <= 0:
            continue
        price = price_at(price_index, symbol, date) or report_price
        if price <= 0:
            continue
        market_value += shares * price
        cost_value += shares * avg_cost
    if market_value <= 0 or cost_value <= 0:
        return 1.0, market_value, cost_value
    return market_value / cost_value, market_value, cost_value


def daily_institution_points(
    reports: List[Dict[str, Any]],
    price_index: Dict[str, Dict[str, Any]],
    trading_days: List[dt.date],
    start_trade_date: dt.date,
    final_date: dt.date,
    initial_value: float,
) -> List[Dict[str, Any]]:
    if not reports:
        return []
    start_context = report_context_for_date(reports, start_trade_date)
    start_index, _, _ = institution_report_profit_index(start_context, price_index, start_trade_date)
    start_index = start_index or 1.0
    event_by_date: Dict[dt.date, Dict[str, Any]] = {}
    for report in reports:
        if report["date_value"] < start_trade_date:
            continue
        event_date = first_trading_day_on_or_after(trading_days, report["date_value"])
        if event_date is None or event_date < start_trade_date or event_date > final_date:
            continue
        if report.get("new_positions"):
            event_by_date[event_date] = report

    points = []
    for date in trading_days:
        if date < start_trade_date or date > final_date:
            continue
        context = report_context_for_date(reports, date)
        profit_index, market_value, cost_value = institution_report_profit_index(context, price_index, date)
        return_pct = percent_change(profit_index, start_index)
        book_return_pct = (market_value - cost_value) / cost_value * 100 if cost_value > 0 else 0.0
        event = event_by_date.get(date)
        point = {
            "date": date.isoformat(),
            "report_period": context.get("report_period"),
            "filing_date": context.get("filing_date"),
            "value": round(market_value, 2),
            "return_pct": round(return_pct, 2),
            "book_return_pct": round(book_return_pct, 2),
            "total_value_usd": round(market_value, 2),
            "holdings_count": context.get("holdings_count", 0),
        }
        if event:
            point["event_report_period"] = event["report_period"]
            point["event_filing_date"] = event["filing_date"]
            point["new_positions"] = event["new_positions"]
        points.append(point)
    return points


def benchmark_curve_from(
    price_index: Dict[str, Dict[str, Any]],
    trading_days: List[dt.date],
    first_date: dt.date,
    final_date: dt.date,
    initial_value: float,
) -> List[Dict[str, Any]]:
    spy_start = price_at(price_index, BENCHMARKS["spy"], first_date)
    qqq_start = price_at(price_index, BENCHMARKS["qqq"], first_date)
    points = []
    for date in trading_days:
        if date < first_date or date > final_date:
            continue
        spy_value = build_benchmark_value(price_index, BENCHMARKS["spy"], date, spy_start, initial_value)
        qqq_value = build_benchmark_value(price_index, BENCHMARKS["qqq"], date, qqq_start, initial_value)
        points.append(
            {
                "date": date.isoformat(),
                "spy_value": round(spy_value, 2),
                "qqq_value": round(qqq_value, 2),
                "spy_return_pct": round(percent_change(spy_value, initial_value), 2),
                "qqq_return_pct": round(percent_change(qqq_value, initial_value), 2),
            }
        )
    if not points:
        points.append(
            {
                "date": first_date.isoformat(),
                "spy_value": initial_value,
                "qqq_value": initial_value,
                "spy_return_pct": 0,
                "qqq_return_pct": 0,
            }
        )
    return points


def build_key_institution_curves(
    config: Dict[str, Any],
    filings_by_cik: Dict[str, List[Dict[str, Any]]],
    price_index: Dict[str, Dict[str, Any]],
    cusip_map: Dict[str, Dict[str, Any]],
    trading_days: List[dt.date],
    start_trade_date: dt.date,
    final_date: dt.date,
    initial_value: float,
) -> Dict[str, Dict[str, Any]]:
    key_set = key_ciks(config)
    metadata = market_metadata(cusip_map)
    managers = [manager for manager in enabled_managers(config) if manager["cik"] in key_set]
    curves: Dict[str, Dict[str, Any]] = {}

    for manager_index, manager in enumerate(managers):
        cik = manager["cik"]
        slug = hugo_data.manager_slug(cik)
        rows = unique_report_filings(filings_by_cik.get(cik) or [])
        avg_cost_by_symbol: Dict[str, float] = {}
        previous_holdings: Dict[str, Dict[str, Any]] = {}
        raw_points = []

        for filing in rows:
            holdings = filing.get("aggregated_holdings") or {}
            if not holdings:
                previous_holdings = {}
                avg_cost_by_symbol = {}
                continue

            current_symbols = set(holdings)
            for symbol in list(avg_cost_by_symbol):
                if symbol not in current_symbols:
                    avg_cost_by_symbol.pop(symbol, None)

            new_positions = []
            cost_value = 0.0
            market_value = 0.0
            holdings_count = 0
            report_holdings: Dict[str, Dict[str, float]] = {}

            for symbol, holding in sorted(holdings.items()):
                shares = float(holding.get("shares") or 0)
                value = float(holding.get("value_usd") or 0)
                price = report_price_for_holding(holding)
                if shares <= 0 or value <= 0 or price is None:
                    continue
                previous = previous_holdings.get(symbol)
                previous_shares = float(previous.get("shares") or 0) if previous else 0.0
                existing_cost = avg_cost_by_symbol.get(symbol, price)
                if previous is None or previous_shares <= 0:
                    avg_cost_by_symbol[symbol] = price
                    if previous_holdings:
                        mapped = metadata.get(symbol, {})
                        new_positions.append(
                            {
                                "symbol": symbol,
                                "slug": hugo_data.slugify_symbol(symbol),
                                "company_name": mapped.get("company_name") or holding.get("issuer_name") or symbol,
                                "value_usd": round(value, 2),
                                "report_price": round(price, 4),
                            }
                        )
                elif shares > previous_shares:
                    added_shares = shares - previous_shares
                    avg_cost_by_symbol[symbol] = ((existing_cost * previous_shares) + (added_shares * price)) / shares
                else:
                    avg_cost_by_symbol[symbol] = existing_cost

                market_value += value
                cost_value += avg_cost_by_symbol[symbol] * shares
                holdings_count += 1
                report_holdings[symbol] = {
                    "shares": shares,
                    "report_price": price,
                    "avg_cost": avg_cost_by_symbol[symbol],
                }

            previous_holdings = holdings
            if market_value <= 0 or cost_value <= 0:
                continue
            report_date = parse_date(filing["report_period"])
            if report_date > final_date:
                continue
            raw_points.append(
                {
                    "date": filing["report_period"],
                    "date_value": report_date,
                    "report_period": filing["report_period"],
                    "filing_date": filing["filing_date"],
                    "holdings_count": holdings_count,
                    "holdings": report_holdings,
                    "new_positions": new_positions[:8],
                }
            )

        if not raw_points:
            continue
        points = daily_institution_points(raw_points, price_index, trading_days, start_trade_date, final_date, initial_value)
        if not points:
            continue

        color = CHART_COLORS["institutions"][manager_index % len(CHART_COLORS["institutions"])]
        display_fields = hugo_data.manager_display_fields(manager)
        series = {
            "key": f"institution_{slug}",
            "valueKey": "return_pct",
            "amountKey": "value",
            "label": display_fields["display_name"],
            "label_en": display_fields["display_name_en"],
            "label_zh": display_fields["display_name_zh"],
            "color": color,
            "points": points,
        }
        curves[slug] = {
            "cik": cik,
            "slug": slug,
            **display_fields,
            "series": series,
            "points": points,
            "benchmark_curve": benchmark_curve_from(price_index, trading_days, start_trade_date, final_date, initial_value),
            "chart_series": [series, base_chart_series()[1], base_chart_series()[2]],
            "summary": {
                "start_date": points[0]["date"],
                "end_date": points[-1]["date"],
                "points_count": len(points),
                "latest_value": points[-1]["value"],
                "latest_return_pct": points[-1]["return_pct"],
                "latest_total_value_usd": points[-1]["total_value_usd"],
                **hugo_data.curve_return_performance_summary(points),
            },
        }
    return curves


def point_value_on_or_before(curve: List[Dict[str, Any]], date: dt.date) -> Optional[float]:
    candidates = [point for point in curve if parse_date(point["date"]) <= date]
    return candidates[-1]["value"] if candidates else None


def build_trade(
    reason_keys: List[str],
    symbol: str,
    row: Dict[str, Any],
    value: float,
    price: float,
    shares: float,
    total_value: float,
    action: str = "buy",
) -> Dict[str, Any]:
    trade_weight_pct = abs(value) / total_value * 100 if total_value else 0
    payload = {
        "symbol": symbol,
        "slug": hugo_data.slugify_symbol(symbol),
        "action": action,
        "reason_keys": reason_keys,
        "target_weight_pct": round(float(row.get("target_weight_pct") or 0), 2),
        "trade_weight_pct": round(trade_weight_pct, 2),
        "buy_value_usd": round(max(value, 0.0), 2),
        "buy_price": price,
        "shares": int(abs(shares)),
    }
    payload["sell_value_usd"] = round(abs(min(value, 0.0)), 2)
    return payload


def trade_weight_pct(value: float, total_value: float) -> float:
    return abs(value) / total_value * 100 if total_value else 0.0


def is_material_trade(value: float, total_value: float, min_trade_weight_pct: float) -> bool:
    return trade_weight_pct(value, total_value) >= min_trade_weight_pct


def build_position(
    symbol: str,
    row: Dict[str, Any],
    shares: int,
    avg_cost: float,
    entry_date: str,
    price: float,
    badges: List[Dict[str, str]],
) -> Dict[str, Any]:
    return {
        "shares": shares,
        "avg_cost": avg_cost,
        "entry_date": entry_date,
        "target_weight_pct": row["target_weight_pct"],
        "allocation_score": row["allocation_score"],
        "allocation_components": row["allocation_components"],
        "source_rankings": row["source_rankings"],
        "badges": badges,
        "company_name": row.get("company_name") or symbol,
        "institutional_avg_holding_price": row.get("institutional_avg_holding_price") or 0,
        "latest_institutional_buy_price": row.get("latest_institutional_buy_price") or 0,
        "discount_to_institutional_avg_pct": row.get("discount_to_institutional_avg_pct") or 0,
        "key_institution_bought": bool(row.get("key_institution_bought")),
        "last_price": price,
    }


def run_simulation(
    config: Dict[str, Any],
    filings: List[Dict[str, Any]],
    price_history: Dict[str, Dict[str, float]],
    cusip_map: Dict[str, Dict[str, Any]],
    start_date: dt.date,
    end_date: dt.date,
    rebalance_until: Optional[dt.date] = None,
    existing_simulation: Optional[Dict[str, Any]] = None,
    dirty_from: Optional[dt.date] = None,
) -> Dict[str, Any]:
    initial_value = float(config.get("strategy", {}).get("initial_value", 100000))
    min_trade_weight = 0.0
    filings_by_cik = group_filings_by_cik(filings)
    price_index = build_price_index(price_history)
    all_trading_days = trading_dates(price_index, start_date, end_date)
    if not all_trading_days:
        raise ValueError("no SPY trading days available for backtest range")
    rebalance_end = min(end_date, rebalance_until) if rebalance_until else end_date
    start_trade_date = all_trading_days[0]
    run_start_date = start_trade_date
    spy_start = price_at(price_index, BENCHMARKS["spy"], start_trade_date)
    qqq_start = price_at(price_index, BENCHMARKS["qqq"], start_trade_date)
    cash = initial_value
    positions: Dict[str, Dict[str, Any]] = {}
    history: List[Dict[str, Any]] = []
    last_candidates_count = 0
    last_rebalance_date = None
    last_candidate_symbols: set = set()
    curve: List[Dict[str, Any]] = []
    checkpoints: List[Dict[str, Any]] = []

    if existing_simulation and dirty_from and dirty_from > start_trade_date:
        existing_checkpoints = existing_simulation.get("checkpoints") or []
        eligible_checkpoints = [
            checkpoint
            for checkpoint in existing_checkpoints
            if checkpoint.get("date") and parse_date(checkpoint["date"]) < dirty_from
        ]
        if eligible_checkpoints:
            checkpoint = max(eligible_checkpoints, key=lambda row: parse_date(row["date"]))
            checkpoint_date = parse_date(checkpoint["date"])
            next_run_date = next_trading_day(checkpoint_date + dt.timedelta(days=1), all_trading_days)
            if next_run_date and next_run_date <= end_date:
                run_start_date = next_run_date
                cash = float(checkpoint.get("cash") or 0)
                positions = copy.deepcopy(checkpoint.get("positions") or {})
                last_candidates_count = int(checkpoint.get("last_candidates_count") or 0)
                last_candidate_symbols = set(checkpoint.get("last_candidate_symbols") or [])
                last_rebalance = checkpoint.get("last_rebalance_date")
                last_rebalance_date = parse_date(last_rebalance) if last_rebalance else None
                curve = [
                    point
                    for point in existing_simulation.get("equity_curve") or []
                    if point.get("date") and parse_date(point["date"]) <= checkpoint_date
                ]
                existing_history = [
                    row
                    for row in existing_simulation.get("rebalance_history") or []
                    if row.get("date") and parse_date(row["date"]) <= checkpoint_date
                ]
                history = list(reversed(existing_history))
                checkpoints = [
                    row
                    for row in existing_checkpoints
                    if row.get("date") and parse_date(row["date"]) <= checkpoint_date
                ]
                print(f"resuming simulation from checkpoint {checkpoint_date}; recomputing from {run_start_date}", file=sys.stderr)

    trading_days = [date for date in all_trading_days if run_start_date <= date <= end_date]
    if not trading_days:
        trading_days = [all_trading_days[-1]]
    rebalances = weekly_rebalance_dates(start_date, rebalance_end, all_trading_days) if rebalance_end >= start_date else []
    rebalance_set = {date for date in rebalances if run_start_date <= date <= end_date}

    for date in trading_days:
        if date in rebalance_set:
            total_value = portfolio_value(positions, cash, price_index, date)
            rows = metrics_as_of(config, filings_by_cik, price_index, cusip_map, date)
            ranks = ranks_for_rows(rows)
            selected = candidates_for_date(config, rows, ranks, set(positions))
            last_candidates_count = len(selected)
            last_rebalance_date = date
            last_candidate_symbols = {row["symbol"] for row in selected}
            if selected:
                key_name_set = key_names(config)
                manager_links = hugo_data.manager_links_by_name(config)
                activity_limit = int(config.get("rankings", {}).get("activity_tag_limit", 10))
                target_by_symbol = {row["symbol"]: row for row in selected}
                buys = []
                sells = []
                new_positions: Dict[str, Dict[str, Any]] = {}
                sold_out_symbols = set()

                for symbol, position in positions.items():
                    price = price_at(price_index, symbol, date) or position.get("last_price") or position.get("avg_cost") or 0.0
                    current_shares = int(position["shares"])
                    row = target_by_symbol.get(symbol)
                    target_row = row or {"target_weight_pct": 0}
                    full_target_shares = 0
                    if row and price:
                        target_value = total_value * row["target_weight_pct"] / 100
                        full_target_shares = math.floor(target_value / price)
                    target_shares = hugo_data.stepped_rebalance_target_shares(
                        config,
                        target_row,
                        current_shares,
                        full_target_shares,
                        price,
                        total_value,
                    )
                    if target_shares >= current_shares:
                        if not row:
                            carried = dict(position)
                            carried["target_weight_pct"] = 0
                            carried["last_price"] = price
                            new_positions[symbol] = carried
                        continue
                    sell_shares = current_shares - target_shares
                    sell_value = sell_shares * price
                    if not is_material_trade(sell_value, total_value, min_trade_weight):
                        if row:
                            badges = hugo_data.build_badges(row, ranks, key_name_set, activity_limit, manager_links)
                            new_positions[symbol] = build_position(
                                symbol,
                                row,
                                current_shares,
                                position["avg_cost"],
                                position["entry_date"],
                                price,
                                badges,
                            )
                        else:
                            carried = dict(position)
                            carried["target_weight_pct"] = 0
                            carried["last_price"] = price
                            new_positions[symbol] = carried
                        continue
                    cash += sell_value
                    if row:
                        if target_shares <= 0:
                            sold_out_symbols.add(symbol)
                        sells.append(
                            {
                                "symbol": symbol,
                                "slug": hugo_data.slugify_symbol(symbol),
                                "action": "exit" if target_shares <= 0 else "sell",
                                "reason_keys": ["rebalance_reason_target_zero" if target_shares <= 0 else "rebalance_reason_target_down"],
                                "from_weight_pct": round(current_shares * price / total_value * 100, 2) if total_value else 0,
                                "to_weight_pct": round(row["target_weight_pct"], 2),
                                "trade_weight_pct": round(trade_weight_pct(sell_value, total_value), 2),
                                "sell_value_usd": round(sell_value, 2),
                                "sell_price": price,
                                "shares": sell_shares,
                            }
                        )
                        if target_shares > 0:
                            badges = hugo_data.build_badges(row, ranks, key_name_set, activity_limit, manager_links)
                            new_positions[symbol] = build_position(
                                symbol,
                                row,
                                target_shares,
                                position["avg_cost"],
                                position["entry_date"],
                                price,
                                badges,
                        )
                    else:
                        if target_shares <= 0:
                            sold_out_symbols.add(symbol)
                        sells.append(
                            {
                                "symbol": symbol,
                                "slug": hugo_data.slugify_symbol(symbol),
                                "action": "exit" if target_shares <= 0 else "sell",
                                "reason_keys": ["rebalance_reason_not_target"],
                                "from_weight_pct": round(current_shares * price / total_value * 100, 2) if total_value else 0,
                                "to_weight_pct": 0,
                                "trade_weight_pct": round(trade_weight_pct(sell_value, total_value), 2),
                                "sell_value_usd": round(sell_value, 2),
                                "sell_price": price,
                                "shares": sell_shares,
                            }
                        )
                        if target_shares > 0:
                            carried = dict(position)
                            carried["shares"] = target_shares
                            carried["target_weight_pct"] = 0
                            carried["last_price"] = price
                            new_positions[symbol] = carried

                for symbol, row in target_by_symbol.items():
                    price = price_at(price_index, symbol, date)
                    if not price:
                        continue
                    target_value = total_value * row["target_weight_pct"] / 100
                    full_target_shares = math.floor(target_value / price)
                    existing = new_positions.get(symbol) or positions.get(symbol)
                    if symbol in sold_out_symbols and symbol not in new_positions:
                        previous_shares = 0
                    else:
                        previous_shares = int(existing["shares"]) if existing else 0
                    target_shares = hugo_data.stepped_rebalance_target_shares(
                        config,
                        row,
                        previous_shares,
                        full_target_shares,
                        price,
                        total_value,
                    )
                    delta_shares = target_shares - previous_shares
                    delta_value = delta_shares * price
                    reason_keys = hugo_data.buy_reason_keys(row.get("source_rankings") or [])
                    badges = hugo_data.build_badges(row, ranks, key_name_set, activity_limit, manager_links)
                    if delta_shares <= 0:
                        if previous_shares > 0 and symbol not in new_positions:
                            new_positions[symbol] = build_position(
                                symbol,
                                row,
                                previous_shares,
                                existing["avg_cost"],
                                existing["entry_date"],
                                price,
                                badges,
                            )
                        continue
                    if not is_material_trade(delta_value, total_value, min_trade_weight):
                        if previous_shares > 0 and symbol not in new_positions:
                            new_positions[symbol] = build_position(
                                symbol,
                                row,
                                previous_shares,
                                existing["avg_cost"],
                                existing["entry_date"],
                                price,
                                badges,
                            )
                        continue
                    buy_shares = min(delta_shares, math.floor(cash / price)) if price else 0
                    if buy_shares <= 0:
                        if previous_shares > 0 and symbol not in new_positions:
                            new_positions[symbol] = build_position(
                                symbol,
                                row,
                                previous_shares,
                                existing["avg_cost"],
                                existing["entry_date"],
                                price,
                                badges,
                            )
                        continue
                    buy_value = buy_shares * price
                    rules = hugo_data.rebalance_step_rules(config)
                    if (
                        buy_shares < delta_shares
                        and trade_weight_pct(buy_value, total_value) < rules["min_buy_gap_weight_pct"]
                    ):
                        if previous_shares > 0 and symbol not in new_positions:
                            new_positions[symbol] = build_position(
                                symbol,
                                row,
                                previous_shares,
                                existing["avg_cost"],
                                existing["entry_date"],
                                price,
                                badges,
                            )
                        continue
                    if not is_material_trade(buy_value, total_value, min_trade_weight):
                        if previous_shares > 0 and symbol not in new_positions:
                            new_positions[symbol] = build_position(
                                symbol,
                                row,
                                previous_shares,
                                existing["avg_cost"],
                                existing["entry_date"],
                                price,
                                badges,
                            )
                        continue
                    action = "initial-buy" if previous_shares <= 0 else "buy"
                    buys.append(build_trade(reason_keys, symbol, row, buy_value, price, buy_shares, total_value, action))
                    cash -= buy_value
                    final_shares = previous_shares + buy_shares
                    if previous_shares <= 0:
                        avg_cost = price
                        entry_date = date.isoformat()
                    else:
                        old_cost_value = existing["avg_cost"] * previous_shares
                        avg_cost = (old_cost_value + buy_value) / final_shares if final_shares else price
                        entry_date = existing["entry_date"]
                    new_positions[symbol] = build_position(
                        symbol,
                        row,
                        final_shares,
                        avg_cost,
                        entry_date,
                        price,
                        badges,
                    )

                positions = new_positions
                cash = max(0.0, cash)
                if buys or sells:
                    history.append({"date": date.isoformat(), "buys": buys, "sells": sells})

        value = portfolio_value(positions, cash, price_index, date)
        spy_value = build_benchmark_value(price_index, BENCHMARKS["spy"], date, spy_start, initial_value)
        qqq_value = build_benchmark_value(price_index, BENCHMARKS["qqq"], date, qqq_start, initial_value)
        curve.append(
            {
                "date": date.isoformat(),
                "value": round(value, 2),
                "return_pct": round(percent_change(value, initial_value), 2),
                "spy_value": round(spy_value, 2),
                "spy_return_pct": round(percent_change(spy_value, initial_value), 2),
                "qqq_value": round(qqq_value, 2),
                "qqq_return_pct": round(percent_change(qqq_value, initial_value), 2),
            }
        )
        checkpoints.append(
            {
                "date": date.isoformat(),
                "cash": round(cash, 6),
                "positions": copy.deepcopy(positions),
                "last_candidates_count": last_candidates_count,
                "last_rebalance_date": last_rebalance_date.isoformat() if last_rebalance_date else None,
                "last_candidate_symbols": sorted(last_candidate_symbols),
            }
        )
    current_value = curve[-1]["value"] if curve else initial_value
    final_date = parse_date(curve[-1]["date"]) if curve else end_date
    one_week_value = point_value_on_or_before(curve, final_date - dt.timedelta(days=7))
    spy_return = percent_change(curve[-1]["spy_value"], curve[0]["spy_value"]) if curve else 0
    qqq_return = percent_change(curve[-1]["qqq_value"], curve[0]["qqq_value"]) if curve else 0
    total_return = percent_change(current_value, initial_value)
    performance_summary = hugo_data.simulation_performance_summary(curve)
    key_institution_curves = build_key_institution_curves(
        config,
        filings_by_cik,
        price_index,
        cusip_map,
        all_trading_days,
        start_trade_date,
        final_date,
        initial_value,
    )
    equity_chart_series = [curve_data["series"] for curve_data in key_institution_curves.values()] + base_chart_series()[1:]

    current_positions = []
    for symbol, position in sorted(
        positions.items(),
        key=lambda item: -((price_at(price_index, item[0], final_date) or 0) * item[1]["shares"]),
    ):
        current_price = price_at(price_index, symbol, final_date) or position.get("last_price") or position["avg_cost"]
        market_value = position["shares"] * current_price
        current_positions.append(
            {
                "symbol": symbol,
                "slug": hugo_data.slugify_symbol(symbol),
                "company_name": position.get("company_name") or symbol,
                "target_weight_pct": round(position.get("target_weight_pct") or 0, 2),
                "actual_weight_pct": round(market_value / current_value * 100, 2) if current_value else 0,
                "allocation_score": int(position["allocation_score"])
                if position["allocation_score"] == int(position["allocation_score"])
                else position["allocation_score"],
                "allocation_components": position["allocation_components"],
                "source_rankings": position["source_rankings"],
                "badges": position.get("badges") or [],
                "entry_date": position["entry_date"],
                "entry_price": round(position["avg_cost"], 4),
                "current_price": round(current_price, 4),
                "return_pct": round(percent_change(current_price, position["avg_cost"]), 2),
                "institutional_avg_holding_price": round(position.get("institutional_avg_holding_price") or 0, 4),
                "latest_institutional_buy_price": round(position.get("latest_institutional_buy_price") or 0, 4),
                "discount_to_institutional_avg_pct": round(position.get("discount_to_institutional_avg_pct") or 0, 2),
                "key_institution_bought": bool(position.get("key_institution_bought")),
                "shares": int(position["shares"]),
                "market_value_usd": round(market_value, 2),
            }
        )

    return {
        "meta": {
            "simulation_id": "institutional-signal-weekly-2024-live-v1",
            "strategy": config.get("strategy", {}).get("id", "institutional_signal_weekly"),
            "mode": "historical_weekly_rebalance",
            "start_date": start_date.isoformat(),
            "end_date": final_date.isoformat(),
            "lookback_trading_days": config.get("strategy", {}).get("lookback_trading_days", 21),
            "max_positions": int(config.get("strategy", {}).get("max_positions", 10)),
            "weighting_method": config.get("strategy", {}).get("weighting_method", "key_institution_signal_score"),
            "rebalance_step": hugo_data.rebalance_step_summary(config),
            "last_rebalance_date": last_rebalance_date.isoformat() if last_rebalance_date else None,
            "rebalance_until": rebalance_until.isoformat() if rebalance_until else None,
            "next_rebalance_date": hugo_data.next_monday(final_date.isoformat()),
        },
        "summary": {
            "start_date": start_trade_date.isoformat(),
            "initial_value": initial_value,
            "current_value": round(current_value, 2),
            "cash_value": round(cash, 2),
            "cash_weight_pct": round(cash / current_value * 100, 2) if current_value else 0,
            "total_return_pct": round(total_return, 2),
            "weekly_return_pct": round(percent_change(current_value, one_week_value), 2),
            **performance_summary,
            "spy_return_pct": round(spy_return, 2),
            "qqq_return_pct": round(qqq_return, 2),
            "excess_vs_spy_pct": round(total_return - spy_return, 2),
            "excess_vs_qqq_pct": round(total_return - qqq_return, 2),
            "candidates_count": last_candidates_count,
            "positions_count": len(current_positions),
        },
        "current_positions": current_positions,
        "equity_curve": curve,
        "equity_chart_series": equity_chart_series,
        "key_institution_curves": key_institution_curves,
        "rebalance_history": list(reversed(history)),
        "last_candidate_symbols": sorted(last_candidate_symbols),
        "checkpoints": checkpoints,
    }


def fetch_prices(
    symbols: List[str],
    args: argparse.Namespace,
    warnings: List[str],
) -> Dict[str, Dict[str, float]]:
    client = LongbridgePriceClient(args.cache_dir, args.longbridge_sleep, args.refresh_prices, args.cache_only_prices)
    output = {}
    for index, symbol in enumerate(symbols, start=1):
        print(f"fetching price history {index}/{len(symbols)} {symbol}")
        try:
            output[symbol] = client.history(symbol, args.start_date, args.end_date)
        except Exception as exc:  # noqa: BLE001 - skip symbols with unavailable prices.
            warnings.append(f"price history unavailable for {symbol}: {exc}")
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=pathlib.Path, default=ROOT / "config/stockhunt.yaml")
    parser.add_argument("--cusip-map", type=pathlib.Path, default=ROOT / "config/cusip-symbols.yaml")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=today())
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "raw/generated/historical")
    parser.add_argument("--cache-dir", type=pathlib.Path, default=ROOT / "raw/generated/cache")
    parser.add_argument("--manager-limit", type=int, default=None)
    parser.add_argument("--symbol-limit", type=int, default=None, help="Limit price symbols for smoke tests.")
    parser.add_argument("--sec-user-agent", default="ValueTracker/0.1 contact@example.com")
    parser.add_argument("--sec-sleep", type=float, default=0.1)
    parser.add_argument("--longbridge-sleep", type=float, default=0.0)
    parser.add_argument("--refresh-sec", action="store_true")
    parser.add_argument("--refresh-submissions", action="store_true", help="Refresh SEC submissions index but reuse cached filing XML.")
    parser.add_argument("--refresh-prices", action="store_true")
    parser.add_argument("--disable-auto-map", action="store_true", help="Only use explicit CUSIP mappings.")
    parser.add_argument("--no-persist-auto-map", action="store_true", help="Do not append successful auto-maps to the CUSIP map.")
    parser.add_argument("--cache-only-prices", action="store_true", help="Reuse cached Longbridge K-line data and never fetch missing ranges.")
    parser.add_argument("--rebalance-until", default=None, help="Only create weekly rebalance events through this date.")
    parser.add_argument("--incremental", action="store_true", help="Resume from stored checkpoints when inputs allow it.")
    parser.add_argument("--dirty-from", default=None, help="Recompute the simulation from this date when --incremental is set.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = hugo_data.load_yaml(args.config)
    config_hash_value = stable_hash(config)
    cusip_map = load_cusip_map(args.cusip_map)
    symbol_resolver = LongbridgeSymbolResolver(
        cusip_map,
        mapping_path=args.cusip_map,
        sleep_seconds=args.longbridge_sleep,
        enabled=not args.disable_auto_map,
        persist=not args.no_persist_auto_map,
    )
    warnings: List[str] = []
    existing_payload = historical_store.load_store(args.output) if args.output.exists() else {}
    filings = fetch_historical_filings(config, symbol_resolver, args, warnings)
    if symbol_resolver.auto_resolved_count:
        warnings.append(f"auto-mapped {symbol_resolver.auto_resolved_count} CUSIPs via Longbridge security-list")
    warnings = list(dict.fromkeys(warnings))
    runtime_cusip_map = symbol_resolver.mappings
    cusip_map_hash_value = stable_hash(runtime_cusip_map)
    filing_fingerprint = filing_fingerprint_rows(filings)
    dirty_from = infer_dirty_from(
        args,
        existing_payload,
        config_hash_value,
        cusip_map_hash_value,
        filing_fingerprint,
    )
    symbols = sorted(
        {
            holding["symbol"]
            for filing in filings
            for holding in filing.get("holdings", [])
            if holding.get("symbol")
        }
        | set(BENCHMARKS.values())
    )
    if args.symbol_limit:
        symbols = sorted(set(symbols[: args.symbol_limit]) | set(BENCHMARKS.values()))
    price_history = fetch_prices(symbols, args, warnings)
    normalize_filing_value_units(filings, build_price_index(price_history), warnings)
    warnings = list(dict.fromkeys(warnings))
    simulation = run_simulation(
        config,
        filings,
        price_history,
        runtime_cusip_map,
        parse_date(args.start_date),
        parse_date(args.end_date),
        parse_date(args.rebalance_until) if args.rebalance_until else None,
        existing_payload.get("simulation") if dirty_from else None,
        dirty_from,
    )
    metadata = market_metadata(runtime_cusip_map)
    holding_market_rows = [{"symbol": symbol, **row} for symbol, row in metadata.items()]
    payload = {
        "build": {
            "build_id": f"historical-simulation-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}",
            "built_at": now_iso(),
            "status": "OK",
            "config_hash": config_hash_value,
            "cusip_map_hash": cusip_map_hash_value,
            "filing_fingerprint": filing_fingerprint,
            "incremental": bool(dirty_from),
            "dirty_from": dirty_from.isoformat() if dirty_from else None,
            "warnings": list(dict.fromkeys(warnings)),
        },
        "simulation": simulation,
        "holding_intervals": hugo_data.build_holding_quarter_intervals(config, filings, holding_market_rows),
    }
    historical_store.write_store(args.output, payload)
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
