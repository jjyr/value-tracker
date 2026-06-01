#!/usr/bin/env python3
"""Generate Hugo data for StockHunt from a normalized backend snapshot."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import pathlib
import re
from typing import Any, Dict, Iterable, List, Optional

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


def slugify_symbol(symbol: str) -> str:
    return symbol.lower().replace(".", "-")


def content_title(value: str) -> str:
    return value.replace('"', '\\"')


def ensure_content_pages(content_dir: pathlib.Path, rows: Iterable[Dict[str, Any]]) -> None:
    content_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        slug = row.get("slug") or slugify_symbol(row["symbol"])
        path = content_dir / f"{slug.replace('-us', '')}.md"
        if path.exists():
            continue
        title = content_title(row.get("company_name") or row["symbol"])
        path.write_text(
            f'---\ntitle: "{title}"\nsymbol: "{row["symbol"]}"\nslug: "{slug}"\n---\n',
            encoding="utf-8",
        )


def enabled_manager_count(config: Dict[str, Any]) -> int:
    managers = config.get("institutions", {}).get("managers", [])
    return sum(1 for manager in managers if manager.get("enabled", True))


def key_institution_names(config: Dict[str, Any]) -> set:
    members = config.get("key_institutions", {}).get("members", [])
    return {member.get("display_name") for member in members if member.get("enabled", True)}


def rank_by_value(rows: List[Dict[str, Any]], key: str) -> Dict[str, int]:
    candidates = [row for row in rows if (row.get(key) or 0) > 0]
    candidates.sort(key=lambda row: (-(row.get(key) or 0), row["symbol"]))
    return {row["symbol"]: index + 1 for index, row in enumerate(candidates)}


def badge(label: str, tone: str) -> Dict[str, str]:
    return {"label": label, "tone": tone}


def build_badges(row: Dict[str, Any], ranks: Dict[str, Dict[str, int]], key_names: set) -> List[Dict[str, str]]:
    symbol = row["symbol"]
    badges: List[Dict[str, str]] = []
    if symbol in ranks["buying"]:
        badges.append(badge(f"买入 #{ranks['buying'][symbol]}", "buying"))
    if symbol in ranks["selling"]:
        badges.append(badge(f"卖出 #{ranks['selling'][symbol]}", "selling"))
    if symbol in ranks["new"]:
        badges.append(badge(f"新建仓 #{ranks['new'][symbol]}", "new"))
    if symbol in ranks["exit"]:
        badges.append(badge(f"清仓 #{ranks['exit'][symbol]}", "exit"))

    if (row.get("holders_count") or 0) == 0 and (row.get("total_tracked_value_usd") or 0) == 0 and symbol in ranks["exit"]:
        badges.append(badge("已清仓", "cleared"))

    row_key_holders = row.get("key_institution_holders") or []
    for holder in row_key_holders:
        if holder in key_names:
            badges.append(badge(holder, "key"))
    return badges


def sort_combined_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            -(row.get("total_tracked_value_usd") or 0),
            -(row.get("holders_count") or 0),
            row["symbol"],
        ),
    )


def market_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    market = raw.get("market") or {}
    return {
        "price": market.get("price"),
        "price_change_pct": market.get("price_change_pct"),
        "market_cap_usd": market.get("market_cap_usd"),
        "pe": market.get("pe"),
        "forward_pe": market.get("forward_pe"),
        "ps": market.get("ps"),
    }


def ranking_row(raw: Dict[str, Any], badges: List[Dict[str, str]]) -> Dict[str, Any]:
    institution = raw.get("institution") or {}
    row = {
        "symbol": raw["symbol"],
        "slug": raw.get("slug") or slugify_symbol(raw["symbol"]),
        "company_name": raw.get("company_name") or raw["symbol"],
        "tags": raw.get("tags") or [],
        "badges": badges,
    }
    row.update(market_row(raw))
    row.update(
        {
            "manager_count": institution.get("manager_count"),
            "buyers_count": institution.get("buyers_count", 0),
            "sellers_count": institution.get("sellers_count", 0),
            "holders_count": institution.get("holders_count", 0),
            "total_bought_value_usd": institution.get("total_bought_value_usd", 0),
            "total_sold_value_usd": institution.get("total_sold_value_usd", 0),
            "new_position_value_usd": institution.get("new_position_value_usd", 0),
            "exit_value_usd": institution.get("exit_value_usd", 0),
            "new_positions_count": institution.get("new_positions_count", 0),
            "added_count": institution.get("added_count", 0),
            "reduced_count": institution.get("reduced_count", 0),
            "exits_count": institution.get("exits_count", 0),
            "total_tracked_value_usd": institution.get("total_tracked_value_usd", 0),
            "institutional_avg_holding_price": institution.get("institutional_avg_holding_price", 0),
            "key_institution_bought": institution.get("key_institution_bought", False),
        }
    )
    return row


def stock_entry(raw: Dict[str, Any], key_names: set) -> Dict[str, Any]:
    institution = raw.get("institution") or {}
    key_holders = [name for name in institution.get("key_institution_holders", []) if name in key_names]
    return {
        "symbol": raw["symbol"],
        "slug": raw.get("slug") or slugify_symbol(raw["symbol"]),
        "company_name": raw.get("company_name") or raw["symbol"],
        "exchange": raw.get("exchange", "NASDAQ"),
        "sector": raw.get("sector"),
        "industry": raw.get("industry"),
        "tags": raw.get("detail_tags") or raw.get("tags") or [],
        "risk_tags": raw.get("risk_tags") or [],
        "market": market_row(raw),
        "metrics": {
            "manager_count": institution.get("manager_count"),
            "buyers_count": institution.get("buyers_count", 0),
            "sellers_count": institution.get("sellers_count", 0),
            "holders_count": institution.get("holders_count", 0),
            "new_positions_count": institution.get("new_positions_count", 0),
            "added_count": institution.get("added_count", 0),
            "reduced_count": institution.get("reduced_count", 0),
            "exits_count": institution.get("exits_count", 0),
            "total_bought_value_usd": institution.get("total_bought_value_usd", 0),
            "total_sold_value_usd": institution.get("total_sold_value_usd", 0),
            "total_tracked_value_usd": institution.get("total_tracked_value_usd", 0),
            "institutional_avg_holding_price": institution.get("institutional_avg_holding_price", 0),
            "key_institution_bought": institution.get("key_institution_bought", False),
            "key_institution_bought_value_usd": institution.get("key_institution_bought_value_usd", 0),
            "key_institution_holders": key_holders,
        },
        "managers": institution.get("managers") or [],
        "ranking_history": raw.get("ranking_history") or [],
    }


def build_metadata(config: Dict[str, Any], snapshot: Dict[str, Any]) -> Dict[str, Any]:
    build = snapshot.get("build") or {}
    now = dt.datetime.now().astimezone().replace(microsecond=0).isoformat()
    return {
        "build_id": build.get("build_id") or re.sub(r"[-:+T]", "", now)[:14],
        "built_at": build.get("built_at") or now,
        "data_date": snapshot.get("data_date"),
        "market_data_date": snapshot.get("market_data_date") or snapshot.get("data_date"),
        "latest_13f_report_period": snapshot.get("latest_13f_report_period"),
        "metrics_version": build.get("metrics_version", "0.1"),
        "whitelist_version": config.get("institutions", {}).get("whitelist_version"),
        "key_institution_version": config.get("key_institutions", {}).get("version"),
        "strategy_version": config.get("strategy", {}).get("version"),
        "status": build.get("status", "OK"),
        "warnings": build.get("warnings", []),
    }


def load_simulation(snapshot: Dict[str, Any], fallback_path: Optional[pathlib.Path]) -> Dict[str, Any]:
    if "simulation" in snapshot:
        return snapshot["simulation"]
    if fallback_path and fallback_path.exists():
        fallback = load_yaml(fallback_path)
        if "simulation" in fallback:
            return fallback["simulation"]
    return {
        "meta": {},
        "summary": {},
        "current_positions": [],
        "equity_curve": [],
        "rebalance_history": [],
    }


def build_hugo_data(config: Dict[str, Any], snapshot: Dict[str, Any], fallback_data: Optional[pathlib.Path]) -> Dict[str, Any]:
    rows = copy.deepcopy(snapshot.get("securities") or [])
    if not rows:
        raise ValueError("snapshot must include at least one security")

    default_manager_count = snapshot.get("manager_count") or enabled_manager_count(config)
    for row in rows:
        institution = row.setdefault("institution", {})
        institution.setdefault("manager_count", default_manager_count)

    metric_rows = []
    for row in rows:
        institution = row.get("institution") or {}
        metric_rows.append({"symbol": row["symbol"], **institution})

    ranks = {
        "buying": rank_by_value(metric_rows, "total_bought_value_usd"),
        "selling": rank_by_value(metric_rows, "total_sold_value_usd"),
        "new": rank_by_value(metric_rows, "new_position_value_usd"),
        "exit": rank_by_value(metric_rows, "exit_value_usd"),
    }
    key_names = key_institution_names(config)
    badge_by_symbol = {row["symbol"]: build_badges({**row, **(row.get("institution") or {})}, ranks, key_names) for row in rows}

    combined_rows = sort_combined_rows([ranking_row(row, badge_by_symbol[row["symbol"]]) for row in rows])
    stocks = {row["symbol"]: stock_entry(row, key_names) for row in rows}
    return {
        "build": build_metadata(config, snapshot),
        "simulation": load_simulation(snapshot, fallback_data),
        "rankings": [
            {
                "type": "institutional_combined",
                "title": "机构综合榜",
                "description": "按白名单机构当前持有市值排序，并用标签标记买入、卖出、新建仓、清仓等异动",
                "sort_label": "持有市值",
                "rows": combined_rows,
            }
        ],
        "stocks": stocks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=pathlib.Path, default=ROOT / "config/stockhunt.yaml")
    parser.add_argument("--snapshot", type=pathlib.Path, default=ROOT / "raw/sample/snapshot.yaml")
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "data/stockhunt.yaml")
    parser.add_argument(
        "--fallback-data",
        type=pathlib.Path,
        default=ROOT / "data/stockhunt.yaml",
        help="Existing Hugo data file used to keep simulation data until backtest generation is implemented.",
    )
    parser.add_argument("--content-dir", type=pathlib.Path, default=ROOT / "content/stocks")
    parser.add_argument("--skip-content", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_yaml(args.config)
    snapshot = load_yaml(args.snapshot)
    data = build_hugo_data(config, snapshot, args.fallback_data)
    write_yaml(args.output, data)
    if not args.skip_content:
        ensure_content_pages(args.content_dir, snapshot.get("securities") or [])
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
