#!/usr/bin/env python3
"""Generate Hugo data for Value Tracker from a normalized backend snapshot."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import math
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


def ensure_institution_pages(content_dir: pathlib.Path, managers: Iterable[Dict[str, Any]]) -> None:
    content_dir.mkdir(parents=True, exist_ok=True)
    for manager in managers:
        cik = normalize_cik(manager.get("cik"))
        path = content_dir / f"{manager_slug(cik)}.md"
        if path.exists():
            continue
        title = content_title(manager.get("display_name") or manager.get("name") or cik)
        name = content_title(manager.get("name") or title)
        path.write_text(
            f'---\ntitle: "{title}"\ncik: "{cik}"\nmanager_name: "{name}"\n---\n',
            encoding="utf-8",
        )


def enabled_manager_count(config: Dict[str, Any]) -> int:
    managers = config.get("institutions", {}).get("managers", [])
    return sum(1 for manager in managers if manager.get("enabled", True))


def normalize_cik(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits.zfill(10)


def enabled_manager_map(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    managers = config.get("institutions", {}).get("managers", [])
    return {normalize_cik(manager.get("cik")): manager for manager in managers if manager.get("enabled", True)}


def enabled_managers(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [manager for manager in config.get("institutions", {}).get("managers", []) if manager.get("enabled", True)]


def key_institution_ciks(config: Dict[str, Any]) -> set:
    members = config.get("key_institutions", {}).get("members", [])
    return {normalize_cik(member.get("cik")) for member in members if member.get("enabled", True)}


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


def linked_badge(label: str, tone: str, href: Optional[str] = None, tip: Optional[str] = None) -> Dict[str, str]:
    item = badge(label, tone)
    if href:
        item["href"] = href
    if tip:
        item["tip"] = tip
    return item


def manager_slug(cik: Any) -> str:
    return normalize_cik(cik)


def manager_href(cik: Any) -> str:
    return f"institutions/{manager_slug(cik)}/"


def normalize_internal_hrefs(value: Any) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "href" and isinstance(item, str):
                value[key] = item.lstrip("/")
            else:
                normalize_internal_hrefs(item)
    elif isinstance(value, list):
        for item in value:
            normalize_internal_hrefs(item)
    return value


def manager_links_by_name(config: Dict[str, Any]) -> Dict[str, str]:
    links: Dict[str, str] = {}
    for manager in enabled_managers(config):
        display_name = manager.get("display_name") or manager.get("name")
        if display_name:
            links[display_name] = manager_href(manager.get("cik"))
    return links


def institution_badge_tip(name: str, status: Optional[str], current_shares: float) -> str:
    if status in {"new_position", "unknown_previous"}:
        return f"{name}：重点机构本期新建仓该股票。"
    if status == "added":
        return f"{name}：重点机构本期增持该股票。"
    if status == "reduced":
        return f"{name}：重点机构本期减持该股票。"
    if status == "exited":
        return f"{name}：重点机构本期清仓该股票。"
    if current_shares > 0:
        return f"{name}：重点机构当前持有该股票。"
    return f"{name}：重点机构与该股票有关。"


def rank_in_limit(ranks: Dict[str, Dict[str, int]], kind: str, symbol: str, limit: int) -> Optional[int]:
    rank = ranks[kind].get(symbol)
    return rank if rank and rank <= limit else None


def build_badges(
    row: Dict[str, Any],
    ranks: Dict[str, Dict[str, int]],
    key_names: set,
    activity_tag_limit: int = 10,
    manager_links: Optional[Dict[str, str]] = None,
) -> List[Dict[str, str]]:
    del ranks, activity_tag_limit
    badges: List[Dict[str, str]] = []
    manager_links = manager_links or {}
    institution_tips: Dict[str, str] = {}
    for holder in row.get("key_institution_holders") or []:
        if holder in key_names:
            institution_tips.setdefault(holder, institution_badge_tip(holder, None, 1.0))
    for manager in row.get("managers") or []:
        name = manager.get("display_name") or manager.get("name")
        status = manager.get("status")
        current_shares = float(manager.get("current_shares") or 0)
        changed = status in {"new_position", "unknown_previous", "added", "reduced", "exited"}
        if name in key_names and (current_shares > 0 or changed):
            institution_tips[name] = institution_badge_tip(name, status, current_shares)
    for name, tip in institution_tips.items():
        badges.append(linked_badge(name, "key", manager_links.get(name), tip))
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


def sort_rows_by_value(rows: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    return sorted(
        [row for row in rows if (row.get(key) or 0) > 0],
        key=lambda row: (-(row.get(key) or 0), row["symbol"]),
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


def activity_share_metrics(managers: List[Dict[str, Any]]) -> Dict[str, float]:
    bought_shares = 0.0
    bought_reference_shares = 0.0
    sold_shares = 0.0
    sold_reference_shares = 0.0
    for manager in managers:
        status = manager.get("status")
        previous_shares = float(manager.get("previous_shares") or 0)
        current_shares = float(manager.get("current_shares") or 0)
        change_shares = float(manager.get("change_shares") or 0)
        if status in {"new_position", "unknown_previous", "added"}:
            shares = current_shares if status in {"new_position", "unknown_previous"} else max(change_shares, 0.0)
            reference = current_shares if status in {"new_position", "unknown_previous"} else previous_shares
            if shares > 0:
                bought_shares += shares
                bought_reference_shares += reference if reference > 0 else shares
        elif status in {"reduced", "exited"}:
            shares = abs(change_shares) or max(previous_shares - current_shares, 0.0)
            if shares > 0:
                sold_shares += shares
                sold_reference_shares += previous_shares if previous_shares > 0 else shares
    return {
        "total_bought_shares": round(bought_shares, 4),
        "total_sold_shares": round(sold_shares, 4),
        "total_bought_shares_pct": round(bought_shares / bought_reference_shares * 100, 4)
        if bought_reference_shares > 0
        else 0.0,
        "total_sold_shares_pct": round(sold_shares / sold_reference_shares * 100, 4)
        if sold_reference_shares > 0
        else 0.0,
    }


def ranking_row(raw: Dict[str, Any], badges: List[Dict[str, str]]) -> Dict[str, Any]:
    institution = raw.get("institution") or {}
    managers = institution.get("managers") or []
    latest_buy_price = latest_institutional_buy_price(raw)
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
            "latest_institutional_buy_price": latest_buy_price,
            "key_institution_bought": institution.get("key_institution_bought", False),
            "managers": managers,
        }
    )
    row.update(activity_share_metrics(managers))
    return row


def stock_entry(raw: Dict[str, Any], key_names: set) -> Dict[str, Any]:
    institution = raw.get("institution") or {}
    key_holders = [name for name in institution.get("key_institution_holders", []) if name in key_names]
    managers = []
    for manager in institution.get("managers") or []:
        manager_row = dict(manager)
        manager_row["href"] = manager_href(manager_row.get("cik"))
        managers.append(manager_row)
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
            "latest_institutional_buy_price": institution.get("latest_institutional_buy_price", 0)
            or latest_institutional_buy_price(raw),
            "key_institution_bought": institution.get("key_institution_bought", False),
            "key_institution_bought_value_usd": institution.get("key_institution_bought_value_usd", 0),
            "key_institution_holders": key_holders,
        },
        "managers": managers,
    }


def status_label(status: str) -> str:
    labels = {
        "new_position": "新建仓",
        "unknown_previous": "新建仓",
        "added": "增持",
        "reduced": "减持",
        "exited": "清仓",
        "unchanged": "持有",
    }
    return labels.get(status, status or "--")


def status_tone(status: str) -> str:
    if status in {"new_position", "unknown_previous", "added"}:
        return "buy"
    if status in {"reduced", "exited"}:
        return "sell"
    return "hold"


def trade_report_price(manager: Dict[str, Any]) -> float:
    status = manager.get("status") or ""
    current_shares = float(manager.get("current_shares") or 0)
    current_value = float(manager.get("current_value_usd") or 0)
    change_shares = abs(float(manager.get("change_shares") or 0))
    change_value = abs(float(manager.get("change_value_usd") or 0))
    if status in {"new_position", "unknown_previous"} and current_shares > 0 and current_value > 0:
        return current_value / current_shares
    if status in {"added", "reduced", "exited"} and change_shares > 0 and change_value > 0:
        return change_value / change_shares
    if current_shares > 0 and current_value > 0:
        return current_value / current_shares
    return 0.0


def manager_stock_row(raw: Dict[str, Any], manager: Dict[str, Any]) -> Dict[str, Any]:
    status = manager.get("status") or ""
    current_shares = float(manager.get("current_shares") or 0)
    current_value = float(manager.get("current_value_usd") or 0)
    row = {
        "symbol": raw["symbol"],
        "slug": raw.get("slug") or slugify_symbol(raw["symbol"]),
        "company_name": raw.get("company_name") or raw["symbol"],
        "status": status,
        "status_label": status_label(status),
        "status_tone": status_tone(status),
        "previous_shares": manager.get("previous_shares") or 0,
        "current_shares": current_shares,
        "change_shares": manager.get("change_shares") or 0,
        "change_value_usd": manager.get("change_value_usd") or 0,
        "trade_report_price": round(trade_report_price(manager), 4),
        "current_value_usd": current_value,
        "avg_holding_price": round(current_value / current_shares, 4) if current_shares > 0 else 0,
        "portfolio_weight_pct": manager.get("portfolio_weight_pct") or 0,
        "filing_date": manager.get("filing_date"),
        "report_period": manager.get("report_period"),
    }
    row.update(market_row(raw))
    return row


def build_institutions(config: Dict[str, Any], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    output: Dict[str, Any] = {}
    rows_by_symbol = {row["symbol"]: row for row in rows}
    for manager in enabled_managers(config):
        cik = normalize_cik(manager.get("cik"))
        slug = manager_slug(cik)
        holdings = []
        changes = []
        for raw in rows_by_symbol.values():
            institution = raw.get("institution") or {}
            for manager_row in institution.get("managers") or []:
                if normalize_cik(manager_row.get("cik")) != cik:
                    continue
                stock_row = manager_stock_row(raw, manager_row)
                if (stock_row.get("current_shares") or 0) > 0:
                    holdings.append(stock_row)
                if stock_row["status"] not in {"", "unchanged", "not_held"} and abs(float(stock_row.get("change_value_usd") or 0)) > 0:
                    changes.append(stock_row)
        holdings.sort(
            key=lambda row: (
                -(row.get("current_value_usd") or 0),
                -(row.get("portfolio_weight_pct") or 0),
                row["symbol"],
            )
        )
        changes.sort(
            key=lambda row: (
                -abs(float(row.get("change_value_usd") or 0)),
                row["symbol"],
            )
        )
        total_value = sum(float(row.get("current_value_usd") or 0) for row in holdings)
        bought_value = sum(
            max(float(row.get("change_value_usd") or 0), 0.0)
            for row in changes
            if row.get("status") in {"new_position", "unknown_previous", "added"}
        )
        sold_value = sum(
            abs(min(float(row.get("change_value_usd") or 0), 0.0))
            for row in changes
            if row.get("status") in {"reduced", "exited"}
        )
        output[slug] = {
            "cik": cik,
            "slug": slug,
            "href": manager_href(cik),
            "name": manager.get("name"),
            "display_name": manager.get("display_name") or manager.get("name"),
            "style": manager.get("style"),
            "summary": {
                "holdings_count": len(holdings),
                "changes_count": len(changes),
                "total_value_usd": total_value,
                "bought_value_usd": bought_value,
                "sold_value_usd": sold_value,
            },
            "holdings": holdings,
            "recent_changes": changes,
        }
    return output


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
    rules = config.get("strategy", {}).get("allocation_signal", {})
    return {
        "key_new_position_score": rules.get("key_new_position_score", 30),
        "key_added_score": rules.get("key_added_score", 20),
        "key_holding_score": rules.get("key_holding_score", 8),
        "key_buy_intensity_score_per_pct": rules.get("key_buy_intensity_score_per_pct", 8),
        "key_buy_intensity_max_score": rules.get("key_buy_intensity_max_score", 40),
        "below_key_latest_buy_price_bonus": rules.get("below_key_latest_buy_price_bonus", 15),
        "multiple_key_institution_bonus": rules.get("multiple_key_institution_bonus", 8),
        "key_reduced_penalty": rules.get("key_reduced_penalty", -15),
        "key_exit_penalty": rules.get("key_exit_penalty", -50),
    }


def discount_to_institutional_avg(row: Dict[str, Any]) -> float:
    price = row.get("price") or 0
    avg = row.get("institutional_avg_holding_price") or 0
    if not price or not avg:
        return 0.0
    return (avg - price) / avg * 100


def key_manager_events(row: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    key_ciks = key_institution_ciks(config)
    key_names = key_institution_names(config)
    events: Dict[str, Any] = {
        "new_count": 0,
        "added_count": 0,
        "holding_count": 0,
        "reduced_count": 0,
        "exit_count": 0,
        "bought_value_usd": 0.0,
        "sold_value_usd": 0.0,
        "holding_value_usd": 0.0,
        "latest_buy_value_usd": 0.0,
        "latest_buy_shares": 0.0,
        "max_buy_portfolio_weight_pct": 0.0,
        "buy_intensity_score": 0.0,
        "buy_intensity_manager": None,
        "holders": [],
    }
    rules = allocation_rules(config)
    institution = row.get("institution") or row
    for manager in institution.get("managers") or row.get("managers") or []:
        cik = normalize_cik(manager.get("cik"))
        name = manager.get("display_name") or manager.get("name")
        if cik not in key_ciks and name not in key_names:
            continue
        status = manager.get("status")
        current_shares = float(manager.get("current_shares") or 0)
        current_value = float(manager.get("current_value_usd") or 0)
        change_value = float(manager.get("change_value_usd") or 0)
        change_shares = float(manager.get("change_shares") or 0)
        portfolio_weight_pct = float(manager.get("portfolio_weight_pct") or 0)
        if current_shares > 0:
            events["holding_count"] += 1
            events["holding_value_usd"] += current_value
            if name:
                events["holders"].append(name)
        if status in {"new_position", "unknown_previous", "added"}:
            bought_value = current_value if status in {"new_position", "unknown_previous"} else max(change_value, 0.0)
            bought_shares = current_shares if status in {"new_position", "unknown_previous"} else max(change_shares, 0.0)
            events["bought_value_usd"] += bought_value
            if status in {"new_position", "unknown_previous"}:
                events["new_count"] += 1
            else:
                events["added_count"] += 1
            if bought_value > 0 and bought_shares > 0:
                events["latest_buy_value_usd"] += bought_value
                events["latest_buy_shares"] += bought_shares
            if bought_value > 0 and current_value > 0 and portfolio_weight_pct > 0:
                buy_portfolio_weight_pct = bought_value / current_value * portfolio_weight_pct
                if buy_portfolio_weight_pct > events["max_buy_portfolio_weight_pct"]:
                    events["max_buy_portfolio_weight_pct"] = buy_portfolio_weight_pct
                    events["buy_intensity_manager"] = name
        elif status in {"reduced", "exited"}:
            sold_value = abs(change_value)
            events["sold_value_usd"] += sold_value
            if status == "reduced":
                events["reduced_count"] += 1
            else:
                events["exit_count"] += 1
    events["holders"] = list(dict.fromkeys(events["holders"]))
    events["latest_buy_price"] = (
        events["latest_buy_value_usd"] / events["latest_buy_shares"] if events["latest_buy_shares"] > 0 else 0.0
    )
    events["buy_intensity_score"] = min(
        float(rules["key_buy_intensity_max_score"]),
        events["max_buy_portfolio_weight_pct"] * float(rules["key_buy_intensity_score_per_pct"]),
    )
    return events


def score_components(
    row: Dict[str, Any],
    ranks: Dict[str, Dict[str, int]],
    rules: Dict[str, float],
    top_n: int,
    config: Dict[str, Any],
) -> Tuple[float, Dict[str, float], List[str], Dict[str, Any]]:
    del ranks, top_n
    events = key_manager_events(row, config)
    price = float(row.get("price") or 0)
    latest_buy_price = float(events.get("latest_buy_price") or 0)
    below_latest_buy = price > 0 and latest_buy_price > 0 and price < latest_buy_price
    active_key_count = events["new_count"] + events["added_count"] + events["holding_count"]
    buy_intensity_score = round(float(events["buy_intensity_score"]), 4)

    components = {
        "key_new_position_score": rules["key_new_position_score"] * events["new_count"],
        "key_added_score": rules["key_added_score"] * events["added_count"],
        "key_holding_score": rules["key_holding_score"] * events["holding_count"],
        "key_buy_intensity_score": buy_intensity_score,
        "below_key_latest_buy_price_bonus": rules["below_key_latest_buy_price_bonus"] if below_latest_buy else 0,
        "multiple_key_institution_bonus": rules["multiple_key_institution_bonus"] if active_key_count >= 2 else 0,
        "key_reduced_penalty": rules["key_reduced_penalty"] * events["reduced_count"],
        "key_exit_penalty": rules["key_exit_penalty"] * events["exit_count"],
    }
    source_rankings: List[str] = []
    if events["new_count"]:
        source_rankings.append("key_new_position")
    if events["added_count"]:
        source_rankings.append("key_added")
    if events["holding_count"]:
        source_rankings.append("key_holding")
    if buy_intensity_score > 0:
        source_rankings.append("key_buy_intensity")
    if below_latest_buy:
        source_rankings.append("below_key_latest_buy")
    if events["reduced_count"]:
        source_rankings.append("key_reduced")
    if events["exit_count"]:
        source_rankings.append("key_exit")
    return sum(components.values()), components, source_rankings, events


def source_ranking_label(source: str) -> str:
    labels = {
        "institutional_buying": "买入榜 Top 10",
        "institutional_holding": "持有榜 Top 10",
        "institutional_selling": "卖出榜 Top 10",
        "key_new_position": "重点机构新建仓",
        "key_added": "重点机构增持",
        "key_holding": "重点机构持有",
        "key_buy_intensity": "重点机构买入力度",
        "below_key_latest_buy": "低于重点机构最近买入价",
        "key_reduced": "重点机构减持",
        "key_exit": "重点机构清仓",
    }
    return labels.get(source, source)


def buy_reason(source_rankings: List[str]) -> str:
    return " + ".join(source_ranking_label(source) for source in source_rankings) or "重点机构信号"


def latest_institutional_buy_price(row: Dict[str, Any]) -> float:
    institution = row.get("institution") or row
    direct = institution.get("latest_institutional_buy_price") or row.get("latest_institutional_buy_price")
    if direct:
        return float(direct)
    value = 0.0
    shares = 0.0
    for manager in institution.get("managers") or row.get("managers") or []:
        status = manager.get("status")
        if status not in {"new_position", "unknown_previous", "added"}:
            continue
        change_shares = float(manager.get("change_shares") or 0)
        if status in {"new_position", "unknown_previous"}:
            change_shares = float(manager.get("current_shares") or change_shares or 0)
            change_value = float(manager.get("current_value_usd") or manager.get("change_value_usd") or 0)
        else:
            change_value = max(float(manager.get("change_value_usd") or 0), 0.0)
        if change_shares > 0 and change_value > 0:
            shares += change_shares
            value += change_value
    return value / shares if shares > 0 else 0.0


def latest_key_institutional_buy_price(row: Dict[str, Any], config: Dict[str, Any]) -> float:
    return float(key_manager_events(row, config).get("latest_buy_price") or 0.0)


def price_factor_to_latest_buy(row: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> float:
    price = float(row.get("price") or 0)
    latest_buy_price = latest_key_institutional_buy_price(row, config) if config else latest_institutional_buy_price(row)
    if price <= 0 or latest_buy_price <= 0:
        return 1.0
    return max(0.5, min(latest_buy_price / price, 1.5))


def allocation_signal_value(row: Dict[str, Any], config: Dict[str, Any]) -> float:
    score = max(float(row.get("allocation_score") or 0), 0.0)
    if score <= 0:
        return 0.0
    exponent = float(config.get("strategy", {}).get("score_weight_exponent", 0.5))
    return (score**exponent) * price_factor_to_latest_buy(row, config)


def normalize_weights(rows: List[Dict[str, Any]], config: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not rows:
        return rows
    max_weight = float(config.get("strategy", {}).get("max_position_weight_pct", 50))
    for row in rows:
        row["latest_institutional_buy_price"] = latest_key_institutional_buy_price(row, config) or latest_institutional_buy_price(row)
        row["price_factor"] = round(price_factor_to_latest_buy(row, config), 4)
        row["allocation_value"] = allocation_signal_value(row, config)
    total_value = sum(row["allocation_value"] for row in rows)
    for row in rows:
        raw_weight = row["allocation_value"] / total_value * 100 if total_value > 0 else 0.0
        row["target_weight_pct"] = min(raw_weight, max_weight)
    return rows


def simulation_candidates(
    config: Dict[str, Any],
    rows: List[Dict[str, Any]],
    ranks: Dict[str, Dict[str, int]],
    existing_symbols: Optional[set] = None,
) -> List[Dict[str, Any]]:
    rules = allocation_rules(config)
    top_n = int(config.get("rankings", {}).get("top_n_for_simulation", 10))
    existing_symbols = existing_symbols or set()
    positive_candidates = []
    exit_candidates = []
    for row in rows:
        symbol = row["symbol"]
        is_existing = symbol in existing_symbols
        if not (row.get("price") or 0):
            continue
        score, components, source_rankings, events = score_components(row, ranks, rules, top_n, config)
        key_bought = events["bought_value_usd"] > 0
        if not (is_existing or key_bought):
            continue
        item = dict(row)
        item["allocation_score"] = score
        item["allocation_components"] = components
        item["source_rankings"] = source_rankings
        item["key_signal"] = events
        item["key_institution_bought"] = key_bought
        item["key_institution_bought_value_usd"] = events["bought_value_usd"]
        item["key_institution_holders"] = events["holders"]
        item["discount_to_institutional_avg_pct"] = discount_to_institutional_avg(row)
        item["latest_institutional_buy_price"] = latest_key_institutional_buy_price(row, config) or latest_institutional_buy_price(row)
        if score > 0:
            positive_candidates.append(item)
        elif is_existing and (events["sold_value_usd"] > 0 or events["exit_count"] > 0):
            item["target_weight_pct"] = 0.0
            item["price_factor"] = round(price_factor_to_latest_buy(item, config), 4)
            item["allocation_value"] = 0.0
            exit_candidates.append(item)
    positive_candidates.sort(
        key=lambda row: (
            -allocation_signal_value(row, config),
            -int(row["symbol"] in existing_symbols),
            -(row.get("key_institution_bought_value_usd") or 0),
            row["symbol"],
        )
    )
    max_positions = int(config.get("strategy", {}).get("max_positions", 10))
    selected = normalize_weights(positive_candidates[:max_positions], config)
    selected_symbols = {row["symbol"] for row in selected}
    return selected + [row for row in exit_candidates if row["symbol"] not in selected_symbols]


def bounded_percentage(value: Any, default: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def rebalance_step_rules(config: Dict[str, Any]) -> Dict[str, float]:
    strategy = config.get("strategy", {})
    step_weight_pct = bounded_percentage(
        strategy.get("rebalance_step_weight_pct"),
        20.0,
    )
    min_gap_weight_pct = bounded_percentage(
        strategy.get("min_buy_gap_weight_pct"),
        5.0,
    )
    return {
        "rebalance_step_weight_pct": step_weight_pct,
        "min_buy_gap_weight_pct": min_gap_weight_pct,
    }


def rebalance_step_summary(config: Dict[str, Any]) -> Dict[str, float]:
    rules = rebalance_step_rules(config)
    return {
        "rebalance_step_weight_pct": round(rules["rebalance_step_weight_pct"], 4),
        "min_buy_gap_weight_pct": round(rules["min_buy_gap_weight_pct"], 4),
    }


def stepped_rebalance_target_shares(
    config: Dict[str, Any],
    row: Dict[str, Any],
    current_shares: int,
    full_target_shares: int,
    price: float,
    total_value: float = 0.0,
) -> int:
    full_target_shares = max(full_target_shares, 0)
    if full_target_shares == current_shares:
        return current_shares
    if total_value <= 0 or price <= 0:
        return full_target_shares

    rules = rebalance_step_rules(config)
    target_weight_pct = float(row.get("target_weight_pct") or 0)
    current_weight_pct = current_shares * price / total_value * 100
    gap_weight_pct = abs(target_weight_pct - current_weight_pct)
    is_buy = full_target_shares > current_shares
    if is_buy and gap_weight_pct < rules["min_buy_gap_weight_pct"]:
        return current_shares
    if gap_weight_pct <= rules["rebalance_step_weight_pct"]:
        return full_target_shares

    trade_value = total_value * rules["rebalance_step_weight_pct"] / 100
    step_shares = math.floor(trade_value / price)
    if step_shares <= 0 and (price <= trade_value or not is_buy):
        step_shares = 1
    if is_buy:
        return min(full_target_shares, current_shares + step_shares)
    return max(full_target_shares, current_shares - step_shares)


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
    selected = simulation_candidates(config, rows, ranks)
    data_date = snapshot.get("data_date")
    baseline_date = date_offset(data_date, -7)
    next_rebalance = next_monday(data_date)

    positions = []
    buys = []
    invested_value = 0.0
    for row in selected:
        target_weight = row["target_weight_pct"]
        target_value = initial_value * target_weight / 100
        price = row.get("price") or 0
        full_target_shares = math.floor(target_value / price) if price else 0
        shares = stepped_rebalance_target_shares(
            config,
            row,
            0,
            full_target_shares,
            price,
            initial_value,
        )
        if shares <= 0:
            continue
        buy_value = shares * price
        invested_value += buy_value
        position = {
            "symbol": row["symbol"],
            "slug": row.get("slug") or slugify_symbol(row["symbol"]),
            "company_name": row.get("company_name") or row["symbol"],
            "target_weight_pct": round(target_weight, 2),
            "actual_weight_pct": round(buy_value / initial_value * 100, 2),
            "allocation_score": int(row["allocation_score"]) if row["allocation_score"] == int(row["allocation_score"]) else row["allocation_score"],
            "allocation_components": row["allocation_components"],
            "source_rankings": row["source_rankings"],
            "badges": badge_by_symbol.get(row["symbol"], []),
            "entry_date": data_date,
            "entry_price": price,
            "current_price": price,
            "return_pct": 0,
            "institutional_avg_holding_price": row.get("institutional_avg_holding_price") or 0,
            "latest_institutional_buy_price": row.get("latest_institutional_buy_price") or 0,
            "discount_to_institutional_avg_pct": round(row.get("discount_to_institutional_avg_pct", 0), 2),
            "key_institution_bought": bool(row.get("key_institution_bought")),
            "shares": int(shares),
            "market_value_usd": round(buy_value, 2),
            "selling_top10": ranks["selling"].get(row["symbol"], 10**9) <= top_n,
        }
        positions.append(position)
        buys.append(
            {
                "symbol": row["symbol"],
                "slug": position["slug"],
                "action": "initial-buy",
                "reason": buy_reason(row["source_rankings"]),
                "target_weight_pct": round(target_weight, 2),
                "trade_weight_pct": round(buy_value / initial_value * 100, 2) if initial_value else 0,
                "buy_value_usd": round(buy_value, 2),
                "buy_price": price,
                "shares": int(shares),
            }
        )
    cash_value = max(0.0, initial_value - invested_value)

    return {
        "meta": {
            "simulation_id": "institutional-signal-weekly-live-v0.1",
            "strategy": strategy.get("id", "institutional_signal_weekly"),
            "mode": "current_snapshot_rebalance",
            "lookback_trading_days": strategy.get("lookback_trading_days", 21),
            "max_positions": max_positions,
            "weighting_method": strategy.get("weighting_method", "key_institution_signal_score"),
            "rebalance_step": rebalance_step_summary(config),
            "last_rebalance_date": data_date,
            "next_rebalance_date": next_rebalance,
        },
        "summary": {
            "start_date": baseline_date or data_date,
            "initial_value": initial_value,
            "current_value": initial_value,
            "cash_value": round(cash_value, 2),
            "cash_weight_pct": round(cash_value / initial_value * 100, 2) if initial_value else 0,
            "total_return_pct": 0,
            "daily_return_pct": 0,
            "weekly_return_pct": 0,
            "ytd_return_pct": 0,
            "max_drawdown_pct": 0,
            "spy_return_pct": 0,
            "qqq_return_pct": 0,
            "excess_vs_spy_pct": 0,
            "excess_vs_qqq_pct": 0,
            "candidates_count": len(selected),
            "positions_count": len(positions),
        },
        "current_positions": positions,
        "equity_curve": [
            {
                "date": baseline_date or data_date,
                "value": initial_value,
                "return_pct": 0,
                "spy_value": initial_value,
                "spy_return_pct": 0,
                "qqq_value": initial_value,
                "qqq_return_pct": 0,
            },
            {
                "date": data_date,
                "value": initial_value,
                "return_pct": 0,
                "spy_value": initial_value,
                "spy_return_pct": 0,
                "qqq_value": initial_value,
                "qqq_return_pct": 0,
            }
        ],
        "rebalance_history": [
            {
                "date": data_date,
                "buys": buys,
                "sells": [],
            }
        ],
    }


def build_hugo_data(
    config: Dict[str, Any],
    snapshot: Dict[str, Any],
) -> Dict[str, Any]:
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
    manager_links = manager_links_by_name(config)
    activity_tag_limit = int(config.get("rankings", {}).get("activity_tag_limit", 10))
    badge_by_symbol = {
        row["symbol"]: build_badges({**row, **(row.get("institution") or {})}, ranks, key_names, activity_tag_limit, manager_links)
        for row in rows
    }

    ranking_rows = [ranking_row(row, badge_by_symbol[row["symbol"]]) for row in rows]
    combined_rows = sort_combined_rows(ranking_rows)
    stocks = {row["symbol"]: stock_entry(row, key_names) for row in rows}
    institutions = build_institutions(config, rows)
    tracked_institution_order = [
        {
            "cik": normalize_cik(manager.get("cik")),
            "display_name": manager.get("display_name") or manager.get("name") or normalize_cik(manager.get("cik")),
        }
        for manager in enabled_managers(config)
        if normalize_cik(manager.get("cik")) in institutions
    ]
    simulation = snapshot.get("simulation") or build_snapshot_simulation(config, snapshot, combined_rows, ranks, badge_by_symbol)
    payload = {
        "build": build_metadata(config, snapshot),
        "simulation": simulation,
        "rankings": [
            {
                "type": "institutional_buying",
                "title": "最近买入最多",
                "description": "按本期白名单机构新建仓与增持的美元金额排序",
                "sort_label": "买入金额",
                "rows": sort_rows_by_value(ranking_rows, "total_bought_value_usd"),
            },
            {
                "type": "institutional_selling",
                "title": "最近卖出最多",
                "description": "按本期白名单机构减持与清仓的美元金额排序",
                "sort_label": "卖出金额",
                "rows": sort_rows_by_value(ranking_rows, "total_sold_value_usd"),
            },
            {
                "type": "institutional_holding",
                "title": "持有最多",
                "description": "按白名单机构当前持有市值排序",
                "sort_label": "持有市值",
                "rows": sort_rows_by_value(ranking_rows, "total_tracked_value_usd"),
            },
        ],
        "stocks": stocks,
        "institutions": institutions,
        "tracked_institution_order": tracked_institution_order,
    }
    return normalize_internal_hrefs(payload)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=pathlib.Path, default=ROOT / "config/stockhunt.yaml")
    parser.add_argument("--snapshot", type=pathlib.Path, default=ROOT / "raw/sample/snapshot.yaml")
    parser.add_argument("--simulation", type=pathlib.Path, default=None)
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "data/stockhunt.yaml")
    parser.add_argument("--content-dir", type=pathlib.Path, default=ROOT / "content/stocks")
    parser.add_argument("--institution-content-dir", type=pathlib.Path, default=ROOT / "content/institutions")
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
        ensure_institution_pages(args.institution_content_dir, enabled_managers(config))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
