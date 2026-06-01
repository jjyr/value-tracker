#!/usr/bin/env python3
"""Build a weekly historical portfolio simulation from SEC 13F filings."""

from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import math
import pathlib
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterable, List, Optional, Tuple

from scripts import generate_stockhunt_data as hugo_data
from scripts.build_live_input import extract_json, load_cusip_map, normalize_cik


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_START_DATE = "2024-01-01"
BENCHMARKS = {"spy": "SPY.US", "qqq": "QQQ.US"}


def parse_date(value: Any) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def today() -> str:
    return dt.date.today().isoformat()


def now_iso() -> str:
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


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


def parse_info_table(xml_text: str, cusip_map: Dict[str, Dict[str, Any]], warnings: List[str], manager_name: str) -> List[Dict[str, Any]]:
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
        mapped = cusip_map.get(cusip)
        if not mapped:
            issuer = child_text(info, "nameOfIssuer") or "unknown issuer"
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
    cusip_map: Dict[str, Dict[str, Any]],
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
                filing["holdings"] = parse_info_table(xml_text, cusip_map, warnings, display_name)
                filings.append(filing)
            except Exception as exc:  # noqa: BLE001 - keep backtest partial.
                warnings.append(f"failed to parse {display_name} {filing['accession_number']}: {exc}")
    filings.sort(key=lambda row: (row["filing_date"], row["cik"], row["report_period"], row["accession_number"]))
    return filings


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
            report_price = current_value / current_shares if current_shares > 0 and current_value > 0 else None
            if report_price is None:
                report_price = price_at(price_index, symbol, as_of) or 0.0
            change_value = signed_change_value(status, prev, cur, report_price)

            if current_shares > 0:
                metric["holders_count"] += 1
                metric["total_tracked_value_usd"] += current_value
                metric["total_tracked_shares"] += current_shares
                if cik in key_set:
                    metric["key_institution_holders"].append(manager.get("display_name") or manager["name"])
            if status in {"new_position", "added"}:
                metric["buyers_count"] += 1
                if status == "new_position":
                    metric["new_positions_count"] += 1
                    metric["new_position_value_usd"] += current_value
                    bought_value = current_value
                else:
                    metric["added_count"] += 1
                    bought_value = max(change_value, 0)
                metric["total_bought_value_usd"] += bought_value
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
                    "display_name": manager.get("display_name") or manager.get("name"),
                    "status": status,
                    "previous_shares": previous_shares,
                    "current_shares": current_shares,
                    "change_shares": current_shares - previous_shares,
                    "change_value_usd": change_value,
                    "current_value_usd": current_value,
                    "filing_date": current_filing["filing_date"],
                    "report_period": current_filing["report_period"],
                }
            )
        if metric["total_tracked_shares"] > 0:
            metric["institutional_avg_holding_price"] = metric["total_tracked_value_usd"] / metric["total_tracked_shares"]

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


def candidates_for_date(config: Dict[str, Any], rows: List[Dict[str, Any]], ranks: Dict[str, Dict[str, int]]) -> List[Dict[str, Any]]:
    rules = hugo_data.allocation_rules(config)
    top_n = int(config.get("rankings", {}).get("top_n_for_simulation", 10))
    candidates = []
    for row in rows:
        score, components, source_rankings = hugo_data.score_components(row, ranks, rules, top_n)
        if score <= 0 or not row.get("price"):
            continue
        item = dict(row)
        item["allocation_score"] = score
        item["allocation_components"] = components
        item["source_rankings"] = source_rankings
        item["discount_to_institutional_avg_pct"] = hugo_data.discount_to_institutional_avg(row)
        candidates.append(item)
    candidates.sort(
        key=lambda row: (
            -row["allocation_score"],
            -int(bool(row.get("key_institution_bought"))),
            -row.get("discount_to_institutional_avg_pct", 0),
            -(row.get("total_bought_value_usd") or 0),
            row["symbol"],
        )
    )
    max_positions = int(config.get("strategy", {}).get("max_positions", 10))
    return hugo_data.normalize_weights(candidates[:max_positions], config)


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


def point_value_on_or_before(curve: List[Dict[str, Any]], date: dt.date) -> Optional[float]:
    candidates = [point for point in curve if parse_date(point["date"]) <= date]
    return candidates[-1]["value"] if candidates else None


def build_trade(label: str, symbol: str, row: Dict[str, Any], value: float, price: float, shares: float) -> Dict[str, Any]:
    payload = {
        "symbol": symbol,
        "slug": hugo_data.slugify_symbol(symbol),
        "reason": label,
        "target_weight_pct": round(float(row.get("target_weight_pct") or 0), 2),
        "buy_value_usd": round(max(value, 0.0), 2),
        "buy_price": price,
        "shares": int(abs(shares)),
    }
    payload["sell_value_usd"] = round(abs(min(value, 0.0)), 2)
    return payload


def run_simulation(
    config: Dict[str, Any],
    filings: List[Dict[str, Any]],
    price_history: Dict[str, Dict[str, float]],
    cusip_map: Dict[str, Dict[str, Any]],
    start_date: dt.date,
    end_date: dt.date,
    rebalance_until: Optional[dt.date] = None,
) -> Dict[str, Any]:
    initial_value = float(config.get("strategy", {}).get("initial_value", 100000))
    filings_by_cik = group_filings_by_cik(filings)
    price_index = build_price_index(price_history)
    trading_days = trading_dates(price_index, start_date, end_date)
    if not trading_days:
        raise ValueError("no SPY trading days available for backtest range")
    rebalance_end = min(end_date, rebalance_until) if rebalance_until else end_date
    rebalances = weekly_rebalance_dates(start_date, rebalance_end, trading_days) if rebalance_end >= start_date else []
    rebalance_set = set(rebalances)
    start_trade_date = trading_days[0]
    spy_start = price_at(price_index, BENCHMARKS["spy"], start_trade_date)
    qqq_start = price_at(price_index, BENCHMARKS["qqq"], start_trade_date)
    cash = initial_value
    positions: Dict[str, Dict[str, Any]] = {}
    history: List[Dict[str, Any]] = []
    last_candidates_count = 0
    last_rebalance_date = None
    last_candidate_rows: Dict[str, Dict[str, Any]] = {}
    curve = []

    for date in trading_days:
        if date in rebalance_set:
            total_value = portfolio_value(positions, cash, price_index, date)
            rows = metrics_as_of(config, filings_by_cik, price_index, cusip_map, date)
            ranks = ranks_for_rows(rows)
            selected = candidates_for_date(config, rows, ranks)
            last_candidates_count = len(selected)
            last_rebalance_date = date
            if selected:
                key_name_set = key_names(config)
                activity_limit = int(config.get("rankings", {}).get("activity_tag_limit", 10))
                target_by_symbol = {row["symbol"]: row for row in selected}
                last_candidate_rows = target_by_symbol
                buys = []
                sells = []
                resizes = []
                new_positions: Dict[str, Dict[str, Any]] = {}

                for symbol, position in positions.items():
                    if symbol in target_by_symbol:
                        continue
                    price = price_at(price_index, symbol, date) or position.get("last_price") or position.get("avg_cost") or 0.0
                    value = position["shares"] * price
                    sells.append(
                        {
                            "symbol": symbol,
                            "slug": hugo_data.slugify_symbol(symbol),
                            "reason": "不在本期目标持仓",
                            "from_weight_pct": round(value / total_value * 100, 2) if total_value else 0,
                            "sell_value_usd": round(value, 2),
                            "sell_price": price,
                            "shares": int(position["shares"]),
                        }
                    )

                for symbol, row in target_by_symbol.items():
                    price = price_at(price_index, symbol, date)
                    if not price:
                        continue
                    target_value = total_value * row["target_weight_pct"] / 100
                    target_shares = math.floor(target_value / price)
                    existing = positions.get(symbol)
                    previous_shares = int(existing["shares"]) if existing else 0
                    previous_value = previous_shares * price
                    delta_shares = target_shares - previous_shares
                    delta_value = delta_shares * price
                    reason = hugo_data.buy_reason(row.get("source_rankings") or [])
                    badges = hugo_data.build_badges(row, ranks, key_name_set, activity_limit)
                    if delta_shares:
                        if previous_shares <= 0 and delta_value > 0:
                            buys.append(build_trade(reason, symbol, row, delta_value, price, delta_shares))
                            avg_cost = price
                            entry_date = date.isoformat()
                        elif delta_value > 0:
                            buys.append(build_trade(reason, symbol, row, delta_value, price, delta_shares))
                            old_cost_value = existing["avg_cost"] * previous_shares
                            avg_cost = (old_cost_value + delta_value) / target_shares if target_shares else price
                            entry_date = existing["entry_date"]
                        else:
                            resizes.append(
                                {
                                    "symbol": symbol,
                                    "slug": hugo_data.slugify_symbol(symbol),
                                    "reason": "目标权重下降",
                                    "from_weight_pct": round(previous_value / total_value * 100, 2) if total_value else 0,
                                    "to_weight_pct": round(row["target_weight_pct"], 2),
                                    "sell_value_usd": round(abs(delta_value), 2),
                                    "sell_price": price,
                                    "shares": int(abs(delta_shares)),
                                }
                            )
                            avg_cost = existing["avg_cost"] if existing else price
                            entry_date = existing["entry_date"] if existing else date.isoformat()
                    else:
                        avg_cost = existing["avg_cost"] if existing else price
                        entry_date = existing["entry_date"] if existing else date.isoformat()
                    if target_shares <= 0:
                        continue
                    new_positions[symbol] = {
                        "shares": target_shares,
                        "avg_cost": avg_cost,
                        "entry_date": entry_date,
                        "target_weight_pct": row["target_weight_pct"],
                        "allocation_score": row["allocation_score"],
                        "allocation_components": row["allocation_components"],
                        "source_rankings": row["source_rankings"],
                        "badges": badges,
                        "company_name": row.get("company_name") or symbol,
                        "institutional_avg_holding_price": row.get("institutional_avg_holding_price") or 0,
                        "discount_to_institutional_avg_pct": row.get("discount_to_institutional_avg_pct") or 0,
                        "key_institution_bought": bool(row.get("key_institution_bought")),
                        "last_price": price,
                    }

                positions = new_positions
                invested_value = sum((price_at(price_index, symbol, date) or 0) * row["shares"] for symbol, row in positions.items())
                cash = max(0.0, total_value - invested_value)
                if buys or sells or resizes:
                    history.append({"date": date.isoformat(), "buys": buys, "sells": sells, "resizes": resizes})

        value = portfolio_value(positions, cash, price_index, date)
        curve.append(
            {
                "date": date.isoformat(),
                "value": round(value, 2),
                "spy_value": round(build_benchmark_value(price_index, BENCHMARKS["spy"], date, spy_start, initial_value), 2),
                "qqq_value": round(build_benchmark_value(price_index, BENCHMARKS["qqq"], date, qqq_start, initial_value), 2),
            }
        )
    current_value = curve[-1]["value"] if curve else initial_value
    final_date = parse_date(curve[-1]["date"]) if curve else end_date
    previous_value = curve[-2]["value"] if len(curve) > 1 else current_value
    one_week_value = point_value_on_or_before(curve, final_date - dt.timedelta(days=7))
    ytd_value = point_value_on_or_before(curve, dt.date(final_date.year, 1, 1))
    spy_return = percent_change(curve[-1]["spy_value"], curve[0]["spy_value"]) if curve else 0
    qqq_return = percent_change(curve[-1]["qqq_value"], curve[0]["qqq_value"]) if curve else 0
    total_return = percent_change(current_value, initial_value)

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
            "weighting_method": "allocation_score_clamped_5_50",
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
            "daily_return_pct": round(percent_change(current_value, previous_value), 2),
            "weekly_return_pct": round(percent_change(current_value, one_week_value), 2),
            "ytd_return_pct": round(percent_change(current_value, ytd_value), 2),
            "max_drawdown_pct": round(max_drawdown_pct([point["value"] for point in curve]), 2),
            "spy_return_pct": round(spy_return, 2),
            "qqq_return_pct": round(qqq_return, 2),
            "excess_vs_spy_pct": round(total_return - spy_return, 2),
            "excess_vs_qqq_pct": round(total_return - qqq_return, 2),
            "candidates_count": last_candidates_count,
            "positions_count": len(current_positions),
        },
        "current_positions": current_positions,
        "equity_curve": curve,
        "rebalance_history": list(reversed(history)),
        "last_candidate_symbols": sorted(last_candidate_rows),
    }


def write_historical_raw(path: pathlib.Path, args: argparse.Namespace, filings: List[Dict[str, Any]], warnings: List[str]) -> None:
    hugo_data.write_yaml(
        path,
        {
            "start_date": args.start_date,
            "end_date": args.end_date,
            "build": {
                "build_id": f"historical-13f-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}",
                "built_at": now_iso(),
                "status": "OK" if filings else "partial",
                "warnings": warnings,
            },
            "filings": filings,
        },
    )


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
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "raw/generated/historical_simulation.yaml")
    parser.add_argument("--raw-output", type=pathlib.Path, default=ROOT / "raw/generated/historical_13f_holdings.yaml")
    parser.add_argument("--cache-dir", type=pathlib.Path, default=ROOT / "raw/generated/cache")
    parser.add_argument("--manager-limit", type=int, default=None)
    parser.add_argument("--symbol-limit", type=int, default=None, help="Limit price symbols for smoke tests.")
    parser.add_argument("--sec-user-agent", default="StockHunt/0.1 contact@example.com")
    parser.add_argument("--sec-sleep", type=float, default=0.1)
    parser.add_argument("--longbridge-sleep", type=float, default=0.0)
    parser.add_argument("--refresh-sec", action="store_true")
    parser.add_argument("--refresh-submissions", action="store_true", help="Refresh SEC submissions index but reuse cached filing XML.")
    parser.add_argument("--refresh-prices", action="store_true")
    parser.add_argument("--cache-only-prices", action="store_true", help="Reuse cached Longbridge K-line data and never fetch missing ranges.")
    parser.add_argument("--rebalance-until", default=None, help="Only create weekly rebalance events through this date.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = hugo_data.load_yaml(args.config)
    cusip_map = load_cusip_map(args.cusip_map)
    warnings: List[str] = []
    filings = fetch_historical_filings(config, cusip_map, args, warnings)
    warnings = list(dict.fromkeys(warnings))
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
    write_historical_raw(args.raw_output, args, filings, warnings)
    simulation = run_simulation(
        config,
        filings,
        price_history,
        cusip_map,
        parse_date(args.start_date),
        parse_date(args.end_date),
        parse_date(args.rebalance_until) if args.rebalance_until else None,
    )
    payload = {
        "build": {
            "build_id": f"historical-simulation-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}",
            "built_at": now_iso(),
            "status": "OK",
            "warnings": list(dict.fromkeys(warnings)),
        },
        "simulation": simulation,
    }
    hugo_data.write_yaml(args.output, payload)
    print(f"wrote {args.output}")
    print(f"wrote {args.raw_output}")


if __name__ == "__main__":
    main()
