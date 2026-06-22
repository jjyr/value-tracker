#!/usr/bin/env python3
"""Normalize live 13F input into the Value Tracker snapshot."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        yaml.dump(data, handle, Dumper=NoAliasDumper, allow_unicode=True, sort_keys=False, width=120)
        tmp_path = pathlib.Path(handle.name)
    tmp_path.replace(path)


def now_iso() -> str:
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def normalize_cik(cik: Any) -> str:
    digits = "".join(ch for ch in str(cik) if ch.isdigit())
    if not digits:
        raise ValueError(f"invalid CIK: {cik!r}")
    return digits.zfill(10)


def slugify_symbol(symbol: str) -> str:
    return symbol.lower().replace(".", "-")


def config_hash(config: Dict[str, Any]) -> str:
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def snapshot_input_hash(config: Dict[str, Any], raw: Dict[str, Any]) -> str:
    return config_hash(
        {
            "config": config,
            "cash_disclosures": raw.get("cash_disclosures") or [],
        }
    )


def enabled_managers(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    managers = []
    for manager in config.get("institutions", {}).get("managers", []):
        if manager.get("enabled", True):
            row = dict(manager)
            row["cik"] = normalize_cik(row["cik"])
            row.setdefault("display_name", row.get("name"))
            row.setdefault("source", "manual")
            managers.append(row)
    return managers


def key_ciks(config: Dict[str, Any]) -> set:
    members = config.get("key_institutions", {}).get("members", [])
    return {normalize_cik(member["cik"]) for member in members if member.get("enabled", True)}


def market_by_symbol(raw: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {row["symbol"]: row for row in raw.get("market", [])}


def filing_sort_key(filing: Dict[str, Any]) -> Tuple[str, str, int, str]:
    return (
        str(filing.get("report_period") or ""),
        str(filing.get("filing_date") or ""),
        int(bool(filing.get("is_amendment"))),
        str(filing.get("accession_number") or ""),
    )


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


def holdings_by_period_and_cik(raw: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, Dict[str, Any]]]]:
    output: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}
    for filing in sorted(raw.get("filings", []), key=filing_sort_key):
        period = filing["report_period"]
        cik = normalize_cik(filing["cik"])
        output.setdefault(period, {})[cik] = aggregate_holdings(filing.get("holdings") or [])
    return output


def filing_index(raw: Dict[str, Any], report_period: Optional[str]) -> Dict[str, Dict[str, Any]]:
    if not report_period:
        return {}
    output: Dict[str, Dict[str, Any]] = {}
    for filing in sorted(raw.get("filings", []), key=filing_sort_key):
        if filing.get("report_period") == report_period:
            output[normalize_cik(filing["cik"])] = filing
    return output


HoldingMap = Dict[Tuple[str, str], Dict[str, Any]]
CashDisclosureMap = Dict[str, Dict[str, Any]]


def holding_map(
    grouped: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]],
    report_period: Optional[str],
    allowed_ciks: Iterable[str],
) -> HoldingMap:
    if not report_period:
        return {}
    period_rows = grouped.get(report_period) or {}
    output: HoldingMap = {}
    for cik in allowed_ciks:
        for symbol, holding in (period_rows.get(cik) or {}).items():
            output[(cik, symbol)] = dict(holding)
    return output


def current_report_prices(current: HoldingMap, markets: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
    totals: Dict[str, Dict[str, float]] = {}
    for (_, symbol), row in current.items():
        bucket = totals.setdefault(symbol, {"shares": 0.0, "value": 0.0})
        bucket["shares"] += float(row.get("shares") or 0)
        bucket["value"] += float(row.get("value_usd") or 0)
    prices = {}
    for symbol, total in totals.items():
        if total["shares"] > 0 and total["value"] > 0:
            prices[symbol] = total["value"] / total["shares"]
    for symbol, market in markets.items():
        if symbol not in prices and market.get("price") is not None:
            prices[symbol] = float(market["price"])
    return prices


def cash_disclosure_sort_key(disclosure: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(disclosure.get("report_period") or ""),
        str(disclosure.get("filing_date") or disclosure.get("as_of_date") or ""),
        str(disclosure.get("source_url") or ""),
    )


def cash_disclosure_index(
    raw: Dict[str, Any],
    report_period: Optional[str],
    allowed_ciks: Iterable[str],
) -> CashDisclosureMap:
    if not report_period:
        return {}
    allowed = set(allowed_ciks)
    output: CashDisclosureMap = {}
    for disclosure in sorted(raw.get("cash_disclosures") or [], key=cash_disclosure_sort_key):
        if disclosure.get("report_period") != report_period:
            continue
        cik = normalize_cik(disclosure.get("cik"))
        if cik not in allowed:
            continue
        cash_value = float(disclosure.get("cash_value_usd") or disclosure.get("value_usd") or 0)
        row = dict(disclosure)
        row.update(
            {
                "cik": cik,
                "report_period": report_period,
                "cash_value_usd": max(cash_value, 0.0),
                "cash_label": disclosure.get("cash_label") or disclosure.get("label") or "Cash",
                "cash_disclosure_available": True,
            }
        )
        output[cik] = row
    return output


def security_totals_by_cik(current: HoldingMap) -> Dict[str, float]:
    totals: Dict[str, float] = {}
    for (cik, _), row in current.items():
        totals[cik] = totals.get(cik, 0.0) + float(row.get("value_usd") or 0)
    return totals


def portfolio_weights(current: HoldingMap, cash_disclosures: Optional[CashDisclosureMap] = None) -> Dict[Tuple[str, str], float]:
    totals = security_totals_by_cik(current)
    cash_disclosures = cash_disclosures or {}
    weights = {}
    for key, row in current.items():
        securities_total = totals.get(key[0]) or 0.0
        cash_value = float((cash_disclosures.get(key[0]) or {}).get("cash_value_usd") or 0)
        total = securities_total + cash_value if key[0] in cash_disclosures else securities_total
        value = float(row.get("value_usd") or 0)
        weights[key] = value / total * 100 if total > 0 else 0.0
    return weights


def snapshot_cash_disclosures(current: HoldingMap, cash_disclosures: CashDisclosureMap) -> List[Dict[str, Any]]:
    security_totals = security_totals_by_cik(current)
    rows = []
    for cik, disclosure in sorted(cash_disclosures.items()):
        securities_value = float(security_totals.get(cik) or 0)
        cash_value = float(disclosure.get("cash_value_usd") or 0)
        total_value = securities_value + cash_value
        row = dict(disclosure)
        row.update(
            {
                "cik": cik,
                "securities_value_usd": round(securities_value, 2),
                "portfolio_total_value_usd": round(total_value, 2),
                "cash_weight_pct": round(cash_value / total_value * 100, 4) if total_value > 0 else 0.0,
            }
        )
        rows.append(row)
    return rows


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
        "new_positions_count": 0,
        "added_count": 0,
        "reduced_count": 0,
        "exits_count": 0,
        "holders_count": 0,
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


def compute_metrics(config: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    current_period = raw["latest_13f_report_period"]
    previous_period = raw.get("previous_13f_report_period")
    managers = enabled_managers(config)
    manager_count = len(managers)
    manager_by_cik = {manager["cik"]: manager for manager in managers}
    allowed_ciks = list(manager_by_cik)
    key_set = key_ciks(config)
    grouped = holdings_by_period_and_cik(raw)
    current = holding_map(grouped, current_period, allowed_ciks)
    previous = holding_map(grouped, previous_period, allowed_ciks)
    current_filings = filing_index(raw, current_period)
    previous_filings = filing_index(raw, previous_period)
    markets = market_by_symbol(raw)
    report_prices = current_report_prices(current, markets)
    cash_disclosures = cash_disclosure_index(raw, current_period, allowed_ciks)
    weights = portfolio_weights(current, cash_disclosures)
    symbols = sorted({symbol for (_, symbol) in current} | {symbol for (_, symbol) in previous})
    metrics = {symbol: metric_seed(symbol, manager_count) for symbol in symbols}

    for symbol in symbols:
        price = float(report_prices.get(symbol) or markets.get(symbol, {}).get("price") or 0)
        metric = metrics[symbol]
        for cik in allowed_ciks:
            prev = previous.get((cik, symbol))
            cur = current.get((cik, symbol))
            if not prev and not cur:
                continue
            previous_known = cik in previous_filings
            status = status_for_change(prev, cur, previous_known)
            if status == "not_held":
                continue

            previous_shares = float(prev.get("shares") or 0) if prev else 0.0
            current_shares = float(cur.get("shares") or 0) if cur else 0.0
            current_value = float(cur.get("value_usd") or 0) if cur else 0.0
            change_shares = current_shares - previous_shares
            change_value = signed_change_value(status, prev, cur, price)
            portfolio_weight = weights.get((cik, symbol), 0.0)
            manager = manager_by_cik[cik]
            filing = current_filings.get(cik) or previous_filings.get(cik)

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
                    bought_shares = current_shares
                else:
                    metric["added_count"] += 1
                    bought_value = max(change_value, 0)
                    bought_shares = max(change_shares, 0)
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
                    "display_name": manager.get("display_name") or manager.get("name"),
                    "status": status,
                    "previous_shares": previous_shares,
                    "current_shares": current_shares,
                    "change_shares": change_shares,
                    "change_value_usd": change_value,
                    "current_value_usd": current_value,
                    "portfolio_weight_pct": portfolio_weight,
                    "filing_date": filing.get("filing_date") if filing else None,
                    "report_period": current_period,
                }
            )

        if metric["total_tracked_shares"] > 0:
            metric["institutional_avg_holding_price"] = metric["total_tracked_value_usd"] / metric["total_tracked_shares"]
        if metric["_latest_buy_shares"] > 0:
            metric["latest_institutional_buy_price"] = metric["_latest_buy_value_usd"] / metric["_latest_buy_shares"]
    return metrics


def snapshot_security(symbol: str, metric: Dict[str, Any], market: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "slug": slugify_symbol(symbol),
        "company_name": market.get("company_name") or symbol,
        "exchange": market.get("exchange"),
        "sector": market.get("sector"),
        "industry": market.get("industry"),
        "tags": market.get("tags") or [],
        "detail_tags": market.get("detail_tags") or market.get("tags") or [],
        "risk_tags": market.get("risk_tags") or [],
        "market": {
            "price": market.get("price"),
            "price_change_pct": market.get("price_change_pct"),
            "market_cap_usd": market.get("market_cap_usd"),
            "pe": market.get("pe"),
            "forward_pe": market.get("forward_pe"),
            "ps": market.get("ps"),
        },
        "institution": {
            "manager_count": metric["manager_count"],
            "buyers_count": metric["buyers_count"],
            "sellers_count": metric["sellers_count"],
            "holders_count": metric["holders_count"],
            "new_positions_count": metric["new_positions_count"],
            "added_count": metric["added_count"],
            "reduced_count": metric["reduced_count"],
            "exits_count": metric["exits_count"],
            "total_bought_value_usd": round(metric["total_bought_value_usd"], 2),
            "total_sold_value_usd": round(metric["total_sold_value_usd"], 2),
            "new_position_value_usd": round(metric["new_position_value_usd"], 2),
            "exit_value_usd": round(metric["exit_value_usd"], 2),
            "total_tracked_value_usd": round(metric["total_tracked_value_usd"], 2),
            "total_tracked_shares": round(metric["total_tracked_shares"], 4),
            "institutional_avg_holding_price": round(metric["institutional_avg_holding_price"], 4)
            if metric["institutional_avg_holding_price"]
            else 0,
            "latest_institutional_buy_price": round(metric["latest_institutional_buy_price"], 4)
            if metric["latest_institutional_buy_price"]
            else 0,
            "key_institution_bought": bool(metric["key_institution_bought"]),
            "key_institution_bought_value_usd": round(metric["key_institution_bought_value_usd"], 2),
            "key_institution_holders": metric["key_institution_holders"],
            "managers": metric["managers"],
        },
    }


def build_snapshot(config: Dict[str, Any], raw: Dict[str, Any], cfg_hash: str, metrics: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    markets = market_by_symbol(raw)
    managers = enabled_managers(config)
    allowed_ciks = [manager["cik"] for manager in managers]
    grouped = holdings_by_period_and_cik(raw)
    current = holding_map(grouped, raw["latest_13f_report_period"], allowed_ciks)
    cash_disclosures = cash_disclosure_index(raw, raw["latest_13f_report_period"], allowed_ciks)
    sorted_symbols = sorted(
        metrics,
        key=lambda symbol: (
            -(float(metrics[symbol].get("total_tracked_value_usd") or 0)),
            -(int(metrics[symbol].get("holders_count") or 0)),
            symbol,
        ),
    )
    securities = [snapshot_security(symbol, metrics[symbol], markets.get(symbol, {})) for symbol in sorted_symbols]
    company_filings = raw.get("company_filings") or []
    company_symbols = sorted(
        {
            holding.get("symbol")
            for filing in company_filings
            for holding in filing.get("holdings", [])
            if holding.get("symbol")
        }
    )
    build = raw.get("build") or {}
    warnings = list(build.get("warnings") or [])
    missing_market = [symbol for symbol in sorted_symbols if symbol not in markets]
    if missing_market:
        warnings.append(f"{len(missing_market)} symbols missing market data")
    return {
        "data_date": raw["data_date"],
        "market_data_date": raw.get("market_data_date") or raw["data_date"],
        "latest_13f_report_period": raw["latest_13f_report_period"],
        "previous_13f_report_period": raw.get("previous_13f_report_period"),
        "latest_13f_fingerprint": raw.get("latest_13f_fingerprint") or [],
        "company_13f_fingerprint": raw.get("company_13f_fingerprint") or [],
        "manager_count": len(managers),
        "build": {
            "build_id": build.get("build_id") or f"snapshot-{raw['data_date']}",
            "built_at": build.get("built_at") or now_iso(),
            "metrics_version": build.get("metrics_version", "0.1"),
            "status": build.get("status", "OK"),
            "config_hash": cfg_hash,
            "warnings": warnings,
        },
        "cash_disclosures": snapshot_cash_disclosures(current, cash_disclosures),
        "company_filings": company_filings,
        "company_market": [markets[symbol] for symbol in company_symbols if symbol in markets],
        "top_shareholders": raw.get("top_shareholders") or [],
        "securities": securities,
    }


def export_hugo_data(config_path: pathlib.Path, snapshot_path: pathlib.Path, output_path: pathlib.Path) -> None:
    from scripts import generate_stockhunt_data

    config = generate_stockhunt_data.load_yaml(config_path)
    snapshot = generate_stockhunt_data.load_yaml(snapshot_path)
    data = generate_stockhunt_data.build_hugo_data(config, snapshot)
    generate_stockhunt_data.write_yaml(output_path, data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=pathlib.Path, default=ROOT / "config/stockhunt.yaml")
    parser.add_argument("--raw", type=pathlib.Path, default=ROOT / "raw/sample/13f_holdings.yaml")
    parser.add_argument("--snapshot-output", type=pathlib.Path, default=ROOT / "raw/generated/snapshot.yaml")
    parser.add_argument("--hugo-output", type=pathlib.Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_yaml(args.config)
    raw = load_yaml(args.raw)
    cfg_hash = snapshot_input_hash(config, raw)
    metrics = compute_metrics(config, raw)
    snapshot = build_snapshot(config, raw, cfg_hash, metrics)
    write_yaml(args.snapshot_output, snapshot)
    if args.hugo_output:
        export_hugo_data(args.config, args.snapshot_output, args.hugo_output)
    print(f"wrote {args.snapshot_output}")


if __name__ == "__main__":
    main()
