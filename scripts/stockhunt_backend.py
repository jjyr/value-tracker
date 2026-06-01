#!/usr/bin/env python3
"""Build StockHunt backend artifacts from normalized 13F input."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_yaml(path: pathlib.Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML object")
    return data


def write_yaml(path: pathlib.Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=False, width=120)


def now_iso() -> str:
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def normalize_cik(cik: Any) -> str:
    digits = "".join(ch for ch in str(cik) if ch.isdigit())
    if not digits:
        raise ValueError(f"invalid CIK: {cik!r}")
    return digits.zfill(10)


def slugify_symbol(symbol: str) -> str:
    return symbol.lower().replace(".", "-")


def ticker_from_symbol(symbol: str) -> str:
    return symbol.split(".", 1)[0]


def config_hash(config: Dict[str, Any]) -> str:
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


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


def connect_db(path: pathlib.Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS config_versions (
          config_hash TEXT PRIMARY KEY,
          config_version TEXT,
          whitelist_version TEXT,
          key_institution_version TEXT,
          strategy_version TEXT,
          config_path TEXT,
          created_at TEXT,
          notes TEXT
        );

        CREATE TABLE IF NOT EXISTS institution_managers (
          cik TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          display_name TEXT,
          style TEXT,
          source TEXT NOT NULL DEFAULT 'manual',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS institution_config_members (
          config_hash TEXT NOT NULL,
          cik TEXT NOT NULL,
          enabled INTEGER NOT NULL,
          is_key_institution INTEGER NOT NULL,
          display_name TEXT,
          PRIMARY KEY (config_hash, cik)
        );

        CREATE TABLE IF NOT EXISTS securities (
          symbol TEXT PRIMARY KEY,
          ticker TEXT NOT NULL,
          company_name TEXT NOT NULL,
          exchange TEXT,
          currency TEXT NOT NULL DEFAULT 'USD',
          cusip TEXT,
          sector TEXT,
          industry TEXT,
          status TEXT NOT NULL DEFAULT 'active',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS security_tags (
          symbol TEXT NOT NULL,
          tag TEXT NOT NULL,
          source TEXT NOT NULL,
          as_of_date TEXT NOT NULL,
          PRIMARY KEY (symbol, tag, as_of_date)
        );

        CREATE TABLE IF NOT EXISTS sec_13f_filings (
          accession_number TEXT PRIMARY KEY,
          cik TEXT NOT NULL,
          filing_type TEXT NOT NULL,
          filing_date TEXT NOT NULL,
          report_period TEXT NOT NULL,
          is_amendment INTEGER NOT NULL DEFAULT 0,
          supersedes_accession_number TEXT,
          sec_url TEXT,
          raw_path TEXT,
          parsed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS institution_holdings (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          accession_number TEXT NOT NULL,
          cik TEXT NOT NULL,
          report_period TEXT NOT NULL,
          cusip TEXT,
          symbol TEXT,
          issuer_name TEXT NOT NULL,
          share_type TEXT,
          put_call TEXT,
          shares REAL NOT NULL,
          value_usd REAL NOT NULL,
          portfolio_weight_pct REAL
        );

        CREATE INDEX IF NOT EXISTS idx_holdings_cik_period ON institution_holdings (cik, report_period);
        CREATE INDEX IF NOT EXISTS idx_holdings_symbol_period ON institution_holdings (symbol, report_period);
        CREATE INDEX IF NOT EXISTS idx_holdings_cusip_period ON institution_holdings (cusip, report_period);

        CREATE TABLE IF NOT EXISTS holding_changes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          config_hash TEXT NOT NULL,
          cik TEXT NOT NULL,
          symbol TEXT NOT NULL,
          report_period TEXT NOT NULL,
          previous_report_period TEXT,
          status TEXT NOT NULL,
          previous_shares REAL NOT NULL,
          current_shares REAL NOT NULL,
          change_shares REAL NOT NULL,
          previous_value_usd REAL NOT NULL,
          current_value_usd REAL NOT NULL,
          current_report_price REAL,
          change_value_usd REAL NOT NULL,
          portfolio_weight_pct REAL
        );

        CREATE TABLE IF NOT EXISTS market_snapshots (
          symbol TEXT NOT NULL,
          date TEXT NOT NULL,
          price REAL,
          price_change_pct REAL,
          market_cap_usd REAL,
          pe REAL,
          forward_pe REAL,
          ps REAL,
          source TEXT NOT NULL,
          is_stale INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (symbol, date)
        );

        CREATE TABLE IF NOT EXISTS metric_snapshots (
          symbol TEXT NOT NULL,
          date TEXT NOT NULL,
          config_hash TEXT NOT NULL,
          report_period TEXT NOT NULL,
          manager_count INTEGER NOT NULL,
          buyers_count INTEGER NOT NULL,
          sellers_count INTEGER NOT NULL,
          new_positions_count INTEGER NOT NULL,
          added_count INTEGER NOT NULL,
          reduced_count INTEGER NOT NULL,
          exits_count INTEGER NOT NULL,
          holders_count INTEGER NOT NULL,
          total_bought_value_usd REAL NOT NULL,
          total_sold_value_usd REAL NOT NULL,
          new_position_value_usd REAL NOT NULL,
          exit_value_usd REAL NOT NULL,
          total_tracked_value_usd REAL NOT NULL,
          total_tracked_shares REAL NOT NULL,
          institutional_avg_holding_price REAL,
          key_institution_bought INTEGER NOT NULL,
          key_institution_bought_value_usd REAL NOT NULL,
          allocation_score REAL,
          PRIMARY KEY (symbol, date, config_hash)
        );

        CREATE TABLE IF NOT EXISTS rank_history (
          date TEXT NOT NULL,
          ranking_type TEXT NOT NULL,
          config_hash TEXT NOT NULL,
          symbol TEXT NOT NULL,
          rank INTEGER NOT NULL,
          sort_value REAL,
          price REAL,
          PRIMARY KEY (date, ranking_type, config_hash, symbol)
        );

        CREATE TABLE IF NOT EXISTS data_quality_issues (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          date TEXT NOT NULL,
          severity TEXT NOT NULL,
          entity_type TEXT NOT NULL,
          entity_id TEXT NOT NULL,
          issue_type TEXT NOT NULL,
          message TEXT NOT NULL,
          resolved_at TEXT
        );
        """
    )


def reset_db(conn: sqlite3.Connection) -> None:
    tables = [
        "data_quality_issues",
        "rank_history",
        "metric_snapshots",
        "market_snapshots",
        "holding_changes",
        "institution_holdings",
        "sec_13f_filings",
        "security_tags",
        "securities",
        "institution_config_members",
        "institution_managers",
        "config_versions",
    ]
    for table in tables:
        conn.execute(f"DELETE FROM {table}")


def upsert_config(conn: sqlite3.Connection, config: Dict[str, Any], cfg_hash: str, config_path: pathlib.Path) -> None:
    created_at = now_iso()
    conn.execute(
        """
        INSERT INTO config_versions (
          config_hash, config_version, whitelist_version, key_institution_version,
          strategy_version, config_path, created_at, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(config_hash) DO UPDATE SET
          config_version = excluded.config_version,
          whitelist_version = excluded.whitelist_version,
          key_institution_version = excluded.key_institution_version,
          strategy_version = excluded.strategy_version,
          config_path = excluded.config_path
        """,
        (
            cfg_hash,
            config.get("version"),
            config.get("institutions", {}).get("whitelist_version"),
            config.get("key_institutions", {}).get("version"),
            config.get("strategy", {}).get("version"),
            str(config_path),
            created_at,
            "stockhunt backend run",
        ),
    )

    key_set = key_ciks(config)
    for manager in enabled_managers(config):
        cik = manager["cik"]
        conn.execute(
            """
            INSERT INTO institution_managers (
              cik, name, display_name, style, source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cik) DO UPDATE SET
              name = excluded.name,
              display_name = excluded.display_name,
              style = excluded.style,
              source = excluded.source,
              updated_at = excluded.updated_at
            """,
            (
                cik,
                manager.get("name") or manager.get("display_name"),
                manager.get("display_name"),
                manager.get("style"),
                manager.get("source", "manual"),
                created_at,
                created_at,
            ),
        )
        conn.execute(
            """
            INSERT INTO institution_config_members (
              config_hash, cik, enabled, is_key_institution, display_name
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(config_hash, cik) DO UPDATE SET
              enabled = excluded.enabled,
              is_key_institution = excluded.is_key_institution,
              display_name = excluded.display_name
            """,
            (cfg_hash, cik, 1, int(cik in key_set), manager.get("display_name")),
        )


def market_by_symbol(raw: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {row["symbol"]: row for row in raw.get("market", [])}


def upsert_security(conn: sqlite3.Connection, symbol: str, source: Dict[str, Any], as_of_date: str) -> None:
    timestamp = now_iso()
    conn.execute(
        """
        INSERT INTO securities (
          symbol, ticker, company_name, exchange, currency, cusip, sector, industry,
          status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
          company_name = excluded.company_name,
          exchange = excluded.exchange,
          cusip = COALESCE(excluded.cusip, securities.cusip),
          sector = COALESCE(excluded.sector, securities.sector),
          industry = COALESCE(excluded.industry, securities.industry),
          status = excluded.status,
          updated_at = excluded.updated_at
        """,
        (
            symbol,
            source.get("ticker") or ticker_from_symbol(symbol),
            source.get("company_name") or source.get("issuer_name") or symbol,
            source.get("exchange"),
            source.get("currency", "USD"),
            source.get("cusip"),
            source.get("sector"),
            source.get("industry"),
            source.get("status", "active"),
            timestamp,
            timestamp,
        ),
    )

    tags = []
    tags.extend(source.get("tags") or [])
    tags.extend(tag for tag in source.get("detail_tags") or [] if tag not in tags)
    for tag in tags:
        conn.execute(
            """
            INSERT INTO security_tags (symbol, tag, source, as_of_date)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol, tag, as_of_date) DO UPDATE SET source = excluded.source
            """,
            (symbol, tag, "sample", as_of_date),
        )


def ingest_market(conn: sqlite3.Connection, raw: Dict[str, Any]) -> None:
    as_of_date = raw["market_data_date"]
    for row in raw.get("market", []):
        upsert_security(conn, row["symbol"], row, as_of_date)
        conn.execute(
            """
            INSERT INTO market_snapshots (
              symbol, date, price, price_change_pct, market_cap_usd, pe, forward_pe, ps, source, is_stale
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, date) DO UPDATE SET
              price = excluded.price,
              price_change_pct = excluded.price_change_pct,
              market_cap_usd = excluded.market_cap_usd,
              pe = excluded.pe,
              forward_pe = excluded.forward_pe,
              ps = excluded.ps,
              source = excluded.source,
              is_stale = excluded.is_stale
            """,
            (
                row["symbol"],
                as_of_date,
                row.get("price"),
                row.get("price_change_pct"),
                row.get("market_cap_usd"),
                row.get("pe"),
                row.get("forward_pe"),
                row.get("ps"),
                row.get("source", "sample"),
                int(row.get("is_stale", False)),
            ),
        )


def ingest_filings(conn: sqlite3.Connection, raw: Dict[str, Any]) -> None:
    markets = market_by_symbol(raw)
    for filing in raw.get("filings", []):
        cik = normalize_cik(filing["cik"])
        holdings = filing.get("holdings") or []
        total_value = sum(float(row.get("value_usd") or 0) for row in holdings)
        accession_number = filing["accession_number"]
        conn.execute(
            """
            INSERT INTO sec_13f_filings (
              accession_number, cik, filing_type, filing_date, report_period,
              is_amendment, supersedes_accession_number, sec_url, raw_path, parsed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(accession_number) DO UPDATE SET
              cik = excluded.cik,
              filing_type = excluded.filing_type,
              filing_date = excluded.filing_date,
              report_period = excluded.report_period,
              is_amendment = excluded.is_amendment,
              supersedes_accession_number = excluded.supersedes_accession_number,
              sec_url = excluded.sec_url,
              raw_path = excluded.raw_path,
              parsed_at = excluded.parsed_at
            """,
            (
                accession_number,
                cik,
                filing.get("filing_type", "13F-HR"),
                filing["filing_date"],
                filing["report_period"],
                int(filing.get("is_amendment", False)),
                filing.get("supersedes_accession_number"),
                filing.get("sec_url"),
                filing.get("raw_path"),
                now_iso(),
            ),
        )
        conn.execute("DELETE FROM institution_holdings WHERE accession_number = ?", (accession_number,))
        for holding in holdings:
            symbol = holding["symbol"]
            security_source = {**markets.get(symbol, {}), **holding}
            upsert_security(conn, symbol, security_source, filing["report_period"])
            value_usd = float(holding.get("value_usd") or 0)
            weight = (value_usd / total_value * 100) if total_value else None
            conn.execute(
                """
                INSERT INTO institution_holdings (
                  accession_number, cik, report_period, cusip, symbol, issuer_name,
                  share_type, put_call, shares, value_usd, portfolio_weight_pct
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    accession_number,
                    cik,
                    filing["report_period"],
                    holding.get("cusip"),
                    symbol,
                    holding.get("issuer_name") or symbol,
                    holding.get("share_type"),
                    holding.get("put_call"),
                    float(holding.get("shares") or 0),
                    value_usd,
                    weight,
                ),
            )


HoldingMap = Dict[Tuple[str, str], Dict[str, Any]]


def filing_index(conn: sqlite3.Connection, report_period: str) -> Dict[str, sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM sec_13f_filings WHERE report_period = ?",
        (report_period,),
    ).fetchall()
    return {row["cik"]: row for row in rows}


def holding_map(conn: sqlite3.Connection, report_period: str, allowed_ciks: Iterable[str]) -> HoldingMap:
    allowed = list(allowed_ciks)
    if not allowed:
        return {}
    placeholders = ",".join("?" for _ in allowed)
    query = f"""
        SELECT
          h.cik,
          h.symbol,
          MAX(h.issuer_name) AS issuer_name,
          MAX(h.cusip) AS cusip,
          SUM(h.shares) AS shares,
          SUM(h.value_usd) AS value_usd,
          SUM(h.value_usd * COALESCE(h.portfolio_weight_pct, 0)) / NULLIF(SUM(h.value_usd), 0) AS portfolio_weight_pct
        FROM institution_holdings h
        WHERE h.report_period = ? AND h.cik IN ({placeholders})
        GROUP BY h.cik, h.symbol
    """
    rows = conn.execute(query, [report_period, *allowed]).fetchall()
    return {(row["cik"], row["symbol"]): dict(row) for row in rows}


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
        "key_institution_bought": False,
        "key_institution_bought_value_usd": 0.0,
        "key_institution_holders": [],
        "managers": [],
    }


def compute_metrics(
    conn: sqlite3.Connection,
    config: Dict[str, Any],
    raw: Dict[str, Any],
    cfg_hash: str,
) -> Dict[str, Dict[str, Any]]:
    current_period = raw["latest_13f_report_period"]
    previous_period = raw.get("previous_13f_report_period")
    data_date = raw["data_date"]
    managers = enabled_managers(config)
    manager_count = len(managers)
    manager_by_cik = {manager["cik"]: manager for manager in managers}
    allowed_ciks = list(manager_by_cik)
    key_set = key_ciks(config)
    current = holding_map(conn, current_period, allowed_ciks)
    previous = holding_map(conn, previous_period, allowed_ciks) if previous_period else {}
    current_filings = filing_index(conn, current_period)
    previous_filings = filing_index(conn, previous_period) if previous_period else {}
    markets = market_by_symbol(raw)
    report_prices = current_report_prices(current, markets)
    symbols = sorted({symbol for (_, symbol) in current} | {symbol for (_, symbol) in previous})

    conn.execute(
        "DELETE FROM holding_changes WHERE config_hash = ? AND report_period = ?",
        (cfg_hash, current_period),
    )
    conn.execute(
        "DELETE FROM metric_snapshots WHERE config_hash = ? AND date = ?",
        (cfg_hash, data_date),
    )
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
            previous_value = float(prev.get("value_usd") or 0) if prev else 0.0
            current_value = float(cur.get("value_usd") or 0) if cur else 0.0
            change_shares = current_shares - previous_shares
            change_value = signed_change_value(status, prev, cur, price)
            portfolio_weight = float(cur.get("portfolio_weight_pct") or 0) if cur else 0.0
            manager = manager_by_cik[cik]
            filing = current_filings.get(cik) or previous_filings.get(cik)

            conn.execute(
                """
                INSERT INTO holding_changes (
                  config_hash, cik, symbol, report_period, previous_report_period,
                  status, previous_shares, current_shares, change_shares,
                  previous_value_usd, current_value_usd, current_report_price,
                  change_value_usd, portfolio_weight_pct
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cfg_hash,
                    cik,
                    symbol,
                    current_period,
                    previous_period,
                    status,
                    previous_shares,
                    current_shares,
                    change_shares,
                    previous_value,
                    current_value,
                    price or None,
                    change_value,
                    portfolio_weight or None,
                ),
            )

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
                    "change_shares": change_shares,
                    "change_value_usd": change_value,
                    "current_value_usd": current_value,
                    "portfolio_weight_pct": portfolio_weight,
                    "filing_date": filing["filing_date"] if filing else None,
                    "report_period": current_period,
                }
            )

        if metric["total_tracked_shares"] > 0:
            metric["institutional_avg_holding_price"] = (
                metric["total_tracked_value_usd"] / metric["total_tracked_shares"]
            )
        conn.execute(
            """
            INSERT INTO metric_snapshots (
              symbol, date, config_hash, report_period, manager_count,
              buyers_count, sellers_count, new_positions_count, added_count,
              reduced_count, exits_count, holders_count, total_bought_value_usd,
              total_sold_value_usd, new_position_value_usd, exit_value_usd,
              total_tracked_value_usd, total_tracked_shares,
              institutional_avg_holding_price, key_institution_bought,
              key_institution_bought_value_usd, allocation_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                data_date,
                cfg_hash,
                current_period,
                manager_count,
                metric["buyers_count"],
                metric["sellers_count"],
                metric["new_positions_count"],
                metric["added_count"],
                metric["reduced_count"],
                metric["exits_count"],
                metric["holders_count"],
                metric["total_bought_value_usd"],
                metric["total_sold_value_usd"],
                metric["new_position_value_usd"],
                metric["exit_value_usd"],
                metric["total_tracked_value_usd"],
                metric["total_tracked_shares"],
                metric["institutional_avg_holding_price"] or None,
                int(metric["key_institution_bought"]),
                metric["key_institution_bought_value_usd"],
                None,
            ),
        )
    compute_rank_history(conn, metrics, data_date, cfg_hash, markets)
    return metrics


def rank_rows(metrics: Dict[str, Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    rows = [row for row in metrics.values() if float(row.get(key) or 0) > 0]
    return sorted(rows, key=lambda row: (-(float(row.get(key) or 0)), row["symbol"]))


def compute_rank_history(
    conn: sqlite3.Connection,
    metrics: Dict[str, Dict[str, Any]],
    data_date: str,
    cfg_hash: str,
    markets: Dict[str, Dict[str, Any]],
) -> None:
    conn.execute("DELETE FROM rank_history WHERE config_hash = ? AND date = ?", (cfg_hash, data_date))
    ranking_defs = {
        "institutional_combined": "total_tracked_value_usd",
        "institutional_buying": "total_bought_value_usd",
        "institutional_selling": "total_sold_value_usd",
        "new_positions": "new_position_value_usd",
        "exits": "exit_value_usd",
    }
    for ranking_type, key in ranking_defs.items():
        rows = rank_rows(metrics, key)
        if ranking_type == "institutional_combined":
            rows = sorted(
                metrics.values(),
                key=lambda row: (
                    -(float(row.get("total_tracked_value_usd") or 0)),
                    -(int(row.get("holders_count") or 0)),
                    row["symbol"],
                ),
            )
        for index, row in enumerate(rows, start=1):
            symbol = row["symbol"]
            conn.execute(
                """
                INSERT INTO rank_history (date, ranking_type, config_hash, symbol, rank, sort_value, price)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data_date,
                    ranking_type,
                    cfg_hash,
                    symbol,
                    index,
                    float(row.get(key) or 0),
                    markets.get(symbol, {}).get("price"),
                ),
            )


def snapshot_security(
    symbol: str,
    metric: Dict[str, Any],
    market: Dict[str, Any],
    ranking_history: List[Dict[str, Any]],
) -> Dict[str, Any]:
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
            "key_institution_bought": bool(metric["key_institution_bought"]),
            "key_institution_bought_value_usd": round(metric["key_institution_bought_value_usd"], 2),
            "key_institution_holders": metric["key_institution_holders"],
            "managers": metric["managers"],
        },
        "ranking_history": ranking_history,
    }


def read_ranking_history(conn: sqlite3.Connection, data_date: str, cfg_hash: str) -> Dict[str, List[Dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT date, ranking_type, symbol, rank
        FROM rank_history
        WHERE date = ? AND config_hash = ?
        ORDER BY ranking_type, rank
        """,
        (data_date, cfg_hash),
    ).fetchall()
    output: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        output.setdefault(row["symbol"], []).append(
            {
                "date": row["date"],
                "ranking": row["ranking_type"],
                "rank": row["rank"],
            }
        )
    return output


def build_snapshot(
    conn: sqlite3.Connection,
    config: Dict[str, Any],
    raw: Dict[str, Any],
    cfg_hash: str,
    metrics: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    markets = market_by_symbol(raw)
    histories = read_ranking_history(conn, raw["data_date"], cfg_hash)
    sorted_symbols = sorted(
        metrics,
        key=lambda symbol: (
            -(float(metrics[symbol].get("total_tracked_value_usd") or 0)),
            -(int(metrics[symbol].get("holders_count") or 0)),
            symbol,
        ),
    )
    securities = [
        snapshot_security(symbol, metrics[symbol], markets.get(symbol, {}), histories.get(symbol, []))
        for symbol in sorted_symbols
    ]
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
        "manager_count": len(enabled_managers(config)),
        "build": {
            "build_id": build.get("build_id") or f"backend-{raw['data_date']}",
            "built_at": build.get("built_at") or now_iso(),
            "metrics_version": build.get("metrics_version", "0.1"),
            "status": build.get("status", "OK"),
            "config_hash": cfg_hash,
            "warnings": warnings,
        },
        "securities": securities,
    }


def resolve_sqlite_path(config: Dict[str, Any], explicit_path: Optional[pathlib.Path]) -> pathlib.Path:
    if explicit_path:
        return explicit_path
    configured = config.get("data", {}).get("sqlite_path") or "data/stockhunt.sqlite"
    path = pathlib.Path(configured)
    return path if path.is_absolute() else ROOT / path


def export_hugo_data(
    config_path: pathlib.Path,
    snapshot_path: pathlib.Path,
    output_path: pathlib.Path,
    fallback_data: pathlib.Path,
) -> None:
    import generate_stockhunt_data

    config = generate_stockhunt_data.load_yaml(config_path)
    snapshot = generate_stockhunt_data.load_yaml(snapshot_path)
    data = generate_stockhunt_data.build_hugo_data(config, snapshot, fallback_data)
    generate_stockhunt_data.write_yaml(output_path, data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=pathlib.Path, default=ROOT / "config/stockhunt.yaml")
    parser.add_argument("--raw", type=pathlib.Path, default=ROOT / "raw/sample/13f_holdings.yaml")
    parser.add_argument("--sqlite", type=pathlib.Path, default=None)
    parser.add_argument("--snapshot-output", type=pathlib.Path, default=ROOT / "raw/generated/snapshot.yaml")
    parser.add_argument("--reset-db", action="store_true")
    parser.add_argument("--hugo-output", type=pathlib.Path, default=None)
    parser.add_argument("--fallback-data", type=pathlib.Path, default=ROOT / "data/stockhunt.yaml")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_yaml(args.config)
    raw = load_yaml(args.raw)
    cfg_hash = config_hash(config)
    db_path = resolve_sqlite_path(config, args.sqlite)
    with connect_db(db_path) as conn:
        init_schema(conn)
        if args.reset_db:
            reset_db(conn)
        upsert_config(conn, config, cfg_hash, args.config)
        ingest_market(conn, raw)
        ingest_filings(conn, raw)
        metrics = compute_metrics(conn, config, raw, cfg_hash)
        snapshot = build_snapshot(conn, config, raw, cfg_hash, metrics)
        write_yaml(args.snapshot_output, snapshot)
    if args.hugo_output:
        export_hugo_data(args.config, args.snapshot_output, args.hugo_output, args.fallback_data)
    print(f"wrote {args.snapshot_output}")
    print(f"updated {db_path}")


if __name__ == "__main__":
    main()
