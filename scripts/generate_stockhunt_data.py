#!/usr/bin/env python3
"""Generate Hugo data for StockHunt from a normalized backend snapshot."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import pathlib
import re
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Tuple

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
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        yaml.dump(data, handle, Dumper=NoAliasDumper, allow_unicode=True, sort_keys=False, width=120)
        tmp_path = pathlib.Path(handle.name)
    tmp_path.replace(path)


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


def rank_holding(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    candidates = [row for row in rows if (row.get("total_tracked_value_usd") or 0) > 0]
    candidates.sort(
        key=lambda row: (
            -(row.get("total_tracked_value_usd") or 0),
            -(row.get("holders_count") or 0),
            row["symbol"],
        )
    )
    return {row["symbol"]: index + 1 for index, row in enumerate(candidates)}


def badge(label: str, tone: str) -> Dict[str, str]:
    return {"label": label, "tone": tone}


def rank_in_limit(ranks: Dict[str, Dict[str, int]], kind: str, symbol: str, limit: int) -> Optional[int]:
    rank = ranks[kind].get(symbol)
    return rank if rank and rank <= limit else None


def build_badges(
    row: Dict[str, Any],
    ranks: Dict[str, Dict[str, int]],
    key_names: set,
    activity_tag_limit: int = 10,
) -> List[Dict[str, str]]:
    symbol = row["symbol"]
    badges: List[Dict[str, str]] = []
    if rank := rank_in_limit(ranks, "buying", symbol, activity_tag_limit):
        badges.append(badge(f"买入 #{rank}", "buying"))
    if rank := rank_in_limit(ranks, "selling", symbol, activity_tag_limit):
        badges.append(badge(f"卖出 #{rank}", "selling"))
    if rank := rank_in_limit(ranks, "new", symbol, activity_tag_limit):
        badges.append(badge(f"新建仓 #{rank}", "new"))
    if rank := rank_in_limit(ranks, "exit", symbol, activity_tag_limit):
        badges.append(badge(f"清仓 #{rank}", "exit"))

    if (row.get("holders_count") or 0) == 0 and (row.get("total_tracked_value_usd") or 0) == 0 and ranks["exit"].get(symbol):
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


def next_monday(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        current = dt.date.fromisoformat(value)
    except ValueError:
        return None
    days = (7 - current.weekday()) % 7
    if days == 0:
        days = 7
    return (current + dt.timedelta(days=days)).isoformat()


def date_offset(value: Optional[str], days: int) -> Optional[str]:
    if not value:
        return None
    try:
        current = dt.date.fromisoformat(value)
    except ValueError:
        return value
    return (current + dt.timedelta(days=days)).isoformat()


def allocation_rules(config: Dict[str, Any]) -> Dict[str, float]:
    rules = config.get("strategy", {}).get("allocation_score", {})
    return {
        "buying_top10_score": rules.get("buying_top10_score", 10),
        "holding_top10_score": rules.get("holding_top10_score", 10),
        "below_institution_avg_score": rules.get("below_institution_avg_score", 20),
        "buying_top10_key_institution_bonus": rules.get("buying_top10_key_institution_bonus", 10),
        "holding_top10_key_institution_bonus": rules.get("holding_top10_key_institution_bonus", 10),
        "selling_top10_penalty": rules.get("selling_top10_penalty", -50),
    }


def discount_to_institutional_avg(row: Dict[str, Any]) -> float:
    price = row.get("price") or 0
    avg = row.get("institutional_avg_holding_price") or 0
    if not price or not avg:
        return 0.0
    return (avg - price) / avg * 100


def score_components(
    row: Dict[str, Any],
    ranks: Dict[str, Dict[str, int]],
    rules: Dict[str, float],
    top_n: int,
) -> Tuple[float, Dict[str, float], List[str]]:
    symbol = row["symbol"]
    buying_top = ranks["buying"].get(symbol, 10**9) <= top_n
    holding_top = ranks["holding"].get(symbol, 10**9) <= top_n
    selling_top = ranks["selling"].get(symbol, 10**9) <= top_n
    below_avg = (buying_top or holding_top) and (row.get("price") or 0) < (row.get("institutional_avg_holding_price") or 0)
    key_bought = bool(row.get("key_institution_bought"))

    components = {
        "buying_top10_score": rules["buying_top10_score"] if buying_top else 0,
        "holding_top10_score": rules["holding_top10_score"] if holding_top else 0,
        "below_institution_avg_score": rules["below_institution_avg_score"] if below_avg else 0,
        "buying_top10_key_institution_bonus": rules["buying_top10_key_institution_bonus"] if buying_top and key_bought else 0,
        "holding_top10_key_institution_bonus": rules["holding_top10_key_institution_bonus"] if holding_top and key_bought else 0,
        "selling_top10_penalty": rules["selling_top10_penalty"] if selling_top else 0,
    }
    source_rankings: List[str] = []
    if buying_top:
        source_rankings.append("institutional_buying")
    if holding_top:
        source_rankings.append("institutional_holding")
    if selling_top:
        source_rankings.append("institutional_selling")
    return sum(components.values()), components, source_rankings


def source_ranking_label(source: str) -> str:
    labels = {
        "institutional_buying": "买入榜 Top 10",
        "institutional_holding": "持有榜 Top 10",
        "institutional_selling": "卖出榜 Top 10",
    }
    return labels.get(source, source)


def buy_reason(source_rankings: List[str]) -> str:
    return " + ".join(source_ranking_label(source) for source in source_rankings) or "评分入选"


def normalize_weights(rows: List[Dict[str, Any]], config: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not rows:
        return rows
    min_weight = float(config.get("strategy", {}).get("min_position_weight_pct", 5))
    max_weight = float(config.get("strategy", {}).get("max_position_weight_pct", 50))
    total_score = sum(max(row["allocation_score"], 0) for row in rows)
    if total_score <= 0:
        return []
    for row in rows:
        raw_weight = row["allocation_score"] / total_score * 100
        row["target_weight_pct"] = min(max(raw_weight, min_weight), max_weight)
    total_weight = sum(row["target_weight_pct"] for row in rows)
    if total_weight > 100:
        scale = 100 / total_weight
        for row in rows:
            row["target_weight_pct"] *= scale
    return rows


def build_snapshot_simulation(
    config: Dict[str, Any],
    snapshot: Dict[str, Any],
    rows: List[Dict[str, Any]],
    ranks: Dict[str, Dict[str, int]],
    badge_by_symbol: Dict[str, List[Dict[str, str]]],
) -> Dict[str, Any]:
    strategy = config.get("strategy", {})
    top_n = int(config.get("rankings", {}).get("top_n_for_simulation", 10))
    max_positions = int(strategy.get("max_positions", 10))
    initial_value = 100000.0
    rules = allocation_rules(config)
    candidates = []

    for row in rows:
        score, components, source_rankings = score_components(row, ranks, rules, top_n)
        if score <= 0 or not (row.get("price") or 0):
            continue
        row_copy = dict(row)
        row_copy["allocation_score"] = score
        row_copy["allocation_components"] = components
        row_copy["source_rankings"] = source_rankings
        row_copy["discount_to_institutional_avg_pct"] = discount_to_institutional_avg(row)
        candidates.append(row_copy)

    candidates.sort(
        key=lambda row: (
            -row["allocation_score"],
            -int(bool(row.get("key_institution_bought"))),
            -row.get("discount_to_institutional_avg_pct", 0),
            -(row.get("total_bought_value_usd") or 0),
            row["symbol"],
        )
    )
    selected = normalize_weights(candidates[:max_positions], config)
    total_weight = sum(row["target_weight_pct"] for row in selected)
    cash_weight = max(0.0, 100 - total_weight)
    data_date = snapshot.get("data_date")
    baseline_date = date_offset(data_date, -7)
    next_rebalance = next_monday(data_date)

    positions = []
    buys = []
    for row in selected:
        target_weight = row["target_weight_pct"]
        buy_value = initial_value * target_weight / 100
        price = row.get("price") or 0
        shares = buy_value / price if price else 0
        position = {
            "symbol": row["symbol"],
            "slug": row.get("slug") or slugify_symbol(row["symbol"]),
            "company_name": row.get("company_name") or row["symbol"],
            "target_weight_pct": round(target_weight, 2),
            "actual_weight_pct": round(target_weight, 2),
            "allocation_score": int(row["allocation_score"]) if row["allocation_score"] == int(row["allocation_score"]) else row["allocation_score"],
            "allocation_components": row["allocation_components"],
            "source_rankings": row["source_rankings"],
            "badges": badge_by_symbol.get(row["symbol"], []),
            "entry_date": data_date,
            "entry_price": price,
            "current_price": price,
            "return_pct": 0,
            "institutional_avg_holding_price": row.get("institutional_avg_holding_price") or 0,
            "discount_to_institutional_avg_pct": round(row.get("discount_to_institutional_avg_pct", 0), 2),
            "key_institution_bought": bool(row.get("key_institution_bought")),
            "selling_top10": ranks["selling"].get(row["symbol"], 10**9) <= top_n,
        }
        positions.append(position)
        buys.append(
            {
                "symbol": row["symbol"],
                "slug": position["slug"],
                "reason": buy_reason(row["source_rankings"]),
                "target_weight_pct": round(target_weight, 2),
                "buy_value_usd": round(buy_value, 2),
                "buy_price": price,
                "shares": round(shares, 4),
            }
        )

    return {
        "meta": {
            "simulation_id": "institutional-signal-weekly-live-v0.1",
            "strategy": strategy.get("id", "institutional_signal_weekly"),
            "mode": "current_snapshot_rebalance",
            "lookback_trading_days": strategy.get("lookback_trading_days", 21),
            "max_positions": max_positions,
            "weighting_method": "allocation_score_clamped_5_50",
            "last_rebalance_date": data_date,
            "next_rebalance_date": next_rebalance,
        },
        "summary": {
            "start_date": baseline_date or data_date,
            "initial_value": initial_value,
            "current_value": initial_value,
            "cash_value": round(initial_value * cash_weight / 100, 2),
            "cash_weight_pct": round(cash_weight, 2),
            "total_return_pct": 0,
            "weekly_return_pct": 0,
            "ytd_return_pct": 0,
            "max_drawdown_pct": 0,
            "spy_return_pct": 0,
            "qqq_return_pct": 0,
            "excess_vs_spy_pct": 0,
            "excess_vs_qqq_pct": 0,
            "candidates_count": len(candidates),
            "positions_count": len(positions),
        },
        "current_positions": positions,
        "equity_curve": [
            {
                "date": baseline_date or data_date,
                "value": initial_value,
                "spy_value": initial_value,
                "qqq_value": initial_value,
            },
            {
                "date": data_date,
                "value": initial_value,
                "spy_value": initial_value,
                "qqq_value": initial_value,
            }
        ],
        "rebalance_history": [
            {
                "date": data_date,
                "buys": buys,
                "sells": [],
                "resizes": [],
            }
        ],
    }


def build_hugo_data(config: Dict[str, Any], snapshot: Dict[str, Any]) -> Dict[str, Any]:
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
        "holding": rank_holding(metric_rows),
    }
    key_names = key_institution_names(config)
    activity_tag_limit = int(config.get("rankings", {}).get("activity_tag_limit", 10))
    badge_by_symbol = {
        row["symbol"]: build_badges({**row, **(row.get("institution") or {})}, ranks, key_names, activity_tag_limit)
        for row in rows
    }

    combined_rows = sort_combined_rows([ranking_row(row, badge_by_symbol[row["symbol"]]) for row in rows])
    stocks = {row["symbol"]: stock_entry(row, key_names) for row in rows}
    simulation = snapshot.get("simulation") or build_snapshot_simulation(config, snapshot, combined_rows, ranks, badge_by_symbol)
    return {
        "build": build_metadata(config, snapshot),
        "simulation": simulation,
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
    parser.add_argument("--simulation", type=pathlib.Path, default=None)
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "data/stockhunt.yaml")
    parser.add_argument("--content-dir", type=pathlib.Path, default=ROOT / "content/stocks")
    parser.add_argument("--skip-content", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_yaml(args.config)
    snapshot = load_yaml(args.snapshot)
    if args.simulation and args.simulation.exists():
        simulation_payload = load_yaml(args.simulation)
        snapshot["simulation"] = simulation_payload.get("simulation") or simulation_payload
    data = build_hugo_data(config, snapshot)
    write_yaml(args.output, data)
    if not args.skip_content:
        ensure_content_pages(args.content_dir, snapshot.get("securities") or [])
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
