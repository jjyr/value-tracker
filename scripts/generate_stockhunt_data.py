#!/usr/bin/env python3
"""Generate Hugo data for Value Tracker from a normalized backend snapshot."""

from __future__ import annotations

import argparse
import calendar
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


def parse_date(value: Any) -> dt.date:
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def percent_change(current: Optional[float], previous: Optional[float]) -> float:
    if previous in (None, 0) or current is None:
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


def subtract_months(date: dt.date, months: int) -> dt.date:
    month_index = date.month - months - 1
    year = date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(date.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)


def curve_point_on_or_before(curve: List[Dict[str, Any]], date: dt.date) -> Optional[Dict[str, Any]]:
    point = None
    for candidate in curve:
        if parse_date(candidate.get("date")) <= date:
            point = candidate
        else:
            break
    return point


def point_value(point: Optional[Dict[str, Any]], key: str) -> Optional[float]:
    if not point or point.get(key) in (None, ""):
        return None
    return float(point[key])


def simulation_performance_summary(curve: List[Dict[str, Any]]) -> Dict[str, float]:
    if not curve:
        return {}
    current = curve[-1]
    final_date = parse_date(current["date"])
    periods = {
        "daily": final_date - dt.timedelta(days=1),
        "monthly": dt.date(final_date.year, final_date.month, 1),
        "three_month": subtract_months(final_date, 3),
        "six_month": subtract_months(final_date, 6),
        "ytd": dt.date(final_date.year, 1, 1),
    }
    summary: Dict[str, float] = {}
    for key, start_date in periods.items():
        previous = curve_point_on_or_before(curve, start_date)
        portfolio_return = percent_change(point_value(current, "value"), point_value(previous, "value"))
        spy_return = percent_change(point_value(current, "spy_value"), point_value(previous, "spy_value"))
        qqq_return = percent_change(point_value(current, "qqq_value"), point_value(previous, "qqq_value"))
        summary[f"{key}_return_pct"] = round(portfolio_return, 2)
        summary[f"{key}_spy_return_pct"] = round(spy_return, 2)
        summary[f"{key}_qqq_return_pct"] = round(qqq_return, 2)
        summary[f"{key}_excess_vs_spy_pct"] = round(portfolio_return - spy_return, 2)
        summary[f"{key}_excess_vs_qqq_pct"] = round(portfolio_return - qqq_return, 2)

    portfolio_drawdown = max_drawdown_pct([float(point["value"]) for point in curve if point.get("value") is not None])
    spy_drawdown = max_drawdown_pct([float(point["spy_value"]) for point in curve if point.get("spy_value") is not None])
    qqq_drawdown = max_drawdown_pct([float(point["qqq_value"]) for point in curve if point.get("qqq_value") is not None])
    summary["max_drawdown_pct"] = round(portfolio_drawdown, 2)
    summary["spy_max_drawdown_pct"] = round(spy_drawdown, 2)
    summary["qqq_max_drawdown_pct"] = round(qqq_drawdown, 2)
    summary["max_drawdown_excess_vs_spy_pct"] = round(portfolio_drawdown - spy_drawdown, 2)
    summary["max_drawdown_excess_vs_qqq_pct"] = round(portfolio_drawdown - qqq_drawdown, 2)
    return summary


def curve_return_performance_summary(curve: List[Dict[str, Any]], return_key: str = "return_pct") -> Dict[str, float]:
    if not curve:
        return {}
    current = curve[-1]
    final_date = parse_date(current["date"])
    periods = {
        "daily": final_date - dt.timedelta(days=1),
        "monthly": dt.date(final_date.year, final_date.month, 1),
        "three_month": subtract_months(final_date, 3),
        "six_month": subtract_months(final_date, 6),
        "ytd": dt.date(final_date.year, 1, 1),
    }

    def return_index(point: Optional[Dict[str, Any]]) -> Optional[float]:
        value = point_value(point, return_key)
        return None if value is None else 1 + value / 100

    summary: Dict[str, float] = {}
    for key, start_date in periods.items():
        previous = curve_point_on_or_before(curve, start_date)
        summary[f"{key}_return_pct"] = round(percent_change(return_index(current), return_index(previous)), 2)
    values = [1 + float(point[return_key]) / 100 for point in curve if point.get(return_key) is not None]
    summary["max_drawdown_pct"] = round(max_drawdown_pct(values), 2)
    return summary


def enrich_key_institution_curve_summaries(simulation: Dict[str, Any]) -> None:
    for curve in (simulation.get("key_institution_curves") or {}).values():
        points = curve.get("points") or (curve.get("series") or {}).get("points") or []
        if not points:
            continue
        summary = curve.setdefault("summary", {})
        summary.update(curve_return_performance_summary(points))
        summary.update(
            {
                "start_date": points[0]["date"],
                "end_date": points[-1]["date"],
                "points_count": len(points),
                "latest_value": points[-1].get("value"),
                "latest_return_pct": points[-1].get("return_pct"),
                "latest_total_value_usd": points[-1].get("total_value_usd"),
            }
        )


def enrich_simulation_summary(simulation: Dict[str, Any]) -> Dict[str, Any]:
    summary = simulation.setdefault("summary", {})
    summary.update(simulation_performance_summary(simulation.get("equity_curve") or []))
    enrich_key_institution_curve_summaries(simulation)
    return simulation


def localize_institution_curves(config: Dict[str, Any], simulation: Dict[str, Any]) -> None:
    managers = manager_by_cik(config)
    for curve in (simulation.get("key_institution_curves") or {}).values():
        cik = normalize_cik(curve.get("cik"))
        fields = manager_display_fields({**curve, **managers.get(cik, {})})
        curve.update(fields)
        series = curve.get("series") or {}
        series.update({
            "label": fields["display_name"],
            "label_en": fields["display_name_en"],
            "label_zh": fields["display_name_zh"],
        })
        for item in curve.get("chart_series") or []:
            if item.get("key") == series.get("key"):
                item.update({
                    "label": fields["display_name"],
                    "label_en": fields["display_name_en"],
                    "label_zh": fields["display_name_zh"],
                })


def content_title(value: str) -> str:
    return value.replace('"', '\\"')


def write_content_page(path: pathlib.Path, content: str, overwrite: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        return
    path.write_text(content, encoding="utf-8")


def ensure_static_language_pages(content_root: pathlib.Path, language: str) -> None:
    labels = {
        "en": {
            "home": "Value Tracker",
            "holdings": "Position Stats",
            "institutions": "Tracked Institutions",
        },
        "zh": {
            "home": "价值追踪",
            "holdings": "持仓统计",
            "institutions": "追踪机构",
        },
    }[language]
    write_content_page(content_root / "_index.md", f'---\ntitle: "{labels["home"]}"\n---\n')
    write_content_page(content_root / "holdings.md", f'---\ntitle: "{labels["holdings"]}"\ntype: "holdings"\n---\n')
    write_content_page(content_root / "institutions/_index.md", f'---\ntitle: "{labels["institutions"]}"\n---\n')


def ensure_content_pages(content_dir: pathlib.Path, rows: Iterable[Dict[str, Any]]) -> None:
    content_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        slug = row.get("slug") or slugify_symbol(row["symbol"])
        path = content_dir / f"{slug.replace('-us', '')}.md"
        if path.exists():
            continue
        title = content_title(row.get("company_name") or row["symbol"])
        write_content_page(
            path,
            f'---\ntitle: "{title}"\nsymbol: "{row["symbol"]}"\nslug: "{slug}"\n---\n',
        )


def ensure_institution_pages(content_dir: pathlib.Path, managers: Iterable[Dict[str, Any]], language: str = "en") -> None:
    content_dir.mkdir(parents=True, exist_ok=True)
    for manager in managers:
        cik = normalize_cik(manager.get("cik"))
        path = content_dir / f"{manager_slug(cik)}.md"
        if path.exists():
            continue
        display_fields = manager_display_fields(manager)
        title = content_title(display_fields["display_name_zh"] if language == "zh" else display_fields["display_name_en"])
        name = content_title(manager.get("name") or title)
        write_content_page(
            path,
            f'---\ntitle: "{title}"\ncik: "{cik}"\nmanager_name: "{name}"\n---\n',
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


def manager_display_fields(manager: Dict[str, Any]) -> Dict[str, str]:
    fallback = str(manager.get("name") or manager.get("display_name") or normalize_cik(manager.get("cik")))
    display_name_en = manager.get("display_name_en") or manager.get("name") or fallback
    display_name_zh = manager.get("display_name_zh") or manager.get("display_name") or fallback
    return {
        "display_name": display_name_en,
        "display_name_zh": display_name_zh,
        "display_name_en": display_name_en,
    }


def localized_manager_row(manager: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(manager)
    row.update(manager_display_fields(row))
    return row


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


def linked_badge(
    label: str,
    tone: str,
    href: Optional[str] = None,
    tip_key: Optional[str] = None,
    label_en: Optional[str] = None,
    label_zh: Optional[str] = None,
) -> Dict[str, str]:
    item = badge(label, tone)
    if label_en:
        item["label_en"] = label_en
    if label_zh:
        item["label_zh"] = label_zh
    if href:
        item["href"] = href
    if tip_key:
        item["tip_key"] = tip_key
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


def manager_by_cik(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {normalize_cik(manager.get("cik")): manager for manager in enabled_managers(config)}


def institution_badge_tip_key(status: Optional[str], current_shares: float) -> str:
    if status in {"new_position", "unknown_previous"}:
        return "badge_tip_key_new_position"
    if status == "added":
        return "badge_tip_key_added"
    if status == "reduced":
        return "badge_tip_key_reduced"
    if status == "exited":
        return "badge_tip_key_exited"
    if current_shares > 0:
        return "badge_tip_key_holding"
    return "badge_tip_key_related"


def rank_in_limit(ranks: Dict[str, Dict[str, int]], kind: str, symbol: str, limit: int) -> Optional[int]:
    rank = ranks[kind].get(symbol)
    return rank if rank and rank <= limit else None


def build_badges(
    row: Dict[str, Any],
    ranks: Dict[str, Dict[str, int]],
    key_names: set,
    activity_tag_limit: int = 10,
    manager_links: Optional[Dict[str, str]] = None,
    managers_by_cik: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    del ranks, activity_tag_limit
    badges: List[Dict[str, str]] = []
    manager_links = manager_links or {}
    managers_by_cik = managers_by_cik or {}
    institution_tips: Dict[str, Tuple[str, Dict[str, str], str]] = {}
    for manager in row.get("managers") or []:
        cik = normalize_cik(manager.get("cik"))
        config_manager = managers_by_cik.get(cik, {})
        name = manager.get("display_name") or manager.get("name")
        status = manager.get("status")
        current_shares = float(manager.get("current_shares") or 0)
        changed = status in {"new_position", "unknown_previous", "added", "reduced", "exited"}
        if name in key_names and (current_shares > 0 or changed):
            fields = manager_display_fields({**config_manager, **manager})
            institution_tips[cik or name] = (name, fields, institution_badge_tip_key(status, current_shares))
    for name, fields, tip_key in institution_tips.values():
        badges.append(
            linked_badge(
                fields["display_name_en"],
                "key",
                manager_links.get(name),
                tip_key,
                fields.get("display_name_en"),
                fields.get("display_name_zh"),
            )
        )
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


def maybe_limit_rows(rows: List[Dict[str, Any]], row_limit: Optional[int]) -> List[Dict[str, Any]]:
    if row_limit is None:
        return rows
    return rows[:row_limit]


def holding_rankings_for_rows(
    ranking_rows: List[Dict[str, Any]],
    period_label: Optional[str],
    row_limit: Optional[int] = None,
    holding_rows: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    holding_source = holding_rows or ranking_rows
    return [
        {
            "type": "institutional_buying",
            "title_key": "ranking_buying_title",
            "description_key": "ranking_buying_description",
            "sort_label_key": "ranking_buying_sort_label",
            "period_label": period_label,
            "rows": maybe_limit_rows(sort_rows_by_value(ranking_rows, "total_bought_shares"), row_limit),
        },
        {
            "type": "institutional_selling",
            "title_key": "ranking_selling_title",
            "description_key": "ranking_selling_description",
            "sort_label_key": "ranking_selling_sort_label",
            "period_label": period_label,
            "rows": maybe_limit_rows(sort_rows_by_value(ranking_rows, "total_sold_shares"), row_limit),
        },
        {
            "type": "institutional_holding",
            "title_key": "ranking_holding_title",
            "description_key": "ranking_holding_description",
            "sort_label_key": "ranking_holding_sort_label",
            "rows": maybe_limit_rows(sort_rows_by_value(holding_source, "total_tracked_shares"), row_limit),
        },
    ]


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
    managers = [localized_manager_row(manager) for manager in institution.get("managers") or []]
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
            "total_tracked_shares": institution.get("total_tracked_shares", 0),
            "institutional_avg_holding_price": institution.get("institutional_avg_holding_price", 0),
            "latest_institutional_buy_price": latest_buy_price,
            "key_institution_bought": institution.get("key_institution_bought", False),
            "managers": managers,
        }
    )
    row.update(activity_share_metrics(managers))
    return row


def market_rows_from_snapshot(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for security in snapshot.get("securities") or []:
        market = security.get("market") or {}
        rows.append(
            {
                "symbol": security["symbol"],
                "company_name": security.get("company_name") or security["symbol"],
                "exchange": security.get("exchange"),
                "sector": security.get("sector"),
                "industry": security.get("industry"),
                "tags": security.get("tags") or [],
                "detail_tags": security.get("detail_tags") or security.get("tags") or [],
                "risk_tags": security.get("risk_tags") or [],
                **market,
            }
        )
    return rows


def merge_market_rows_with_filings(market_rows: List[Dict[str, Any]], filings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_symbol = {row["symbol"]: dict(row) for row in market_rows if row.get("symbol")}
    for filing in filings:
        for holding in filing.get("holdings") or []:
            symbol = holding.get("symbol")
            if not symbol or symbol in by_symbol:
                continue
            by_symbol[symbol] = {
                "symbol": symbol,
                "company_name": holding.get("issuer_name") or symbol,
            }
    return sorted(by_symbol.values(), key=lambda row: row["symbol"])


def filing_periods(filings: List[Dict[str, Any]]) -> List[str]:
    return sorted({str(filing.get("report_period") or "")[:10] for filing in filings if filing.get("report_period")})


HOLDING_RANGE_PRESETS = [
    ("quarter", "holding_period_quarter", 1),
    ("half_year", "holding_period_half_year", 2),
    ("one_year", "holding_period_one_year", 4),
    ("ytd", "holding_period_ytd", None),
    ("all", "holding_period_all", None),
]

BUY_STATUSES = {"new_position", "unknown_previous", "added"}
SELL_STATUSES = {"reduced", "exited"}


def build_holding_quarter_intervals(
    config: Dict[str, Any],
    filings: List[Dict[str, Any]],
    market_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not filings:
        return []

    from scripts import stockhunt_backend

    periods = filing_periods(filings)
    if len(periods) < 2:
        return []
    markets = merge_market_rows_with_filings(market_rows, filings)
    output = []

    for index in range(len(periods) - 1, 0, -1):
        report_period = periods[index]
        previous_period = periods[index - 1]
        raw = {
            "data_date": report_period,
            "market_data_date": report_period,
            "latest_13f_report_period": report_period,
            "previous_13f_report_period": previous_period,
            "market": markets,
            "filings": filings,
        }
        metrics = stockhunt_backend.compute_metrics(config, raw)
        if not metrics:
            continue
        snapshot = stockhunt_backend.build_snapshot(config, raw, "historical-holdings", metrics)
        rows = [ranking_row(row, []) for row in snapshot.get("securities") or []]
        period_label = activity_period_label(previous_period, report_period)
        output.append(
            {
                "key": report_period,
                "label_key": "holding_period_quarter",
                "report_period": report_period,
                "previous_report_period": previous_period,
                "rows": rows,
            }
        )
    return output


def action_shares(manager: Dict[str, Any], action: str) -> Tuple[float, float, float]:
    status = manager.get("status") or ""
    previous_shares = float(manager.get("previous_shares") or 0)
    current_shares = float(manager.get("current_shares") or 0)
    change_shares = float(manager.get("change_shares") or 0)
    change_value = float(manager.get("change_value_usd") or 0)
    current_value = float(manager.get("current_value_usd") or 0)
    if action == "buy":
        shares = current_shares if status in {"new_position", "unknown_previous"} else max(change_shares, 0.0)
        reference = current_shares if status in {"new_position", "unknown_previous"} else previous_shares
        value = current_value if status in {"new_position", "unknown_previous"} else max(change_value, 0.0)
    else:
        shares = abs(change_shares) or max(previous_shares - current_shares, 0.0)
        reference = previous_shares if previous_shares > 0 else shares
        value = abs(change_value)
    return shares, reference if reference > 0 else shares, value


def aggregate_manager_action(manager: Dict[str, Any], action: str, shares: float, value: float) -> Dict[str, Any]:
    change_shares = shares if action == "buy" else -shares
    change_value = value if action == "buy" else -value
    return {
        "cik": manager.get("cik"),
        "name": manager.get("name"),
        **manager_display_fields(manager),
        "status": "added" if action == "buy" else "reduced",
        "previous_shares": 0,
        "current_shares": shares if action == "buy" else 0,
        "change_shares": change_shares,
        "change_value_usd": change_value,
        "current_value_usd": value if action == "buy" else 0,
        "portfolio_weight_pct": 0,
        "filing_date": manager.get("filing_date"),
        "report_period": manager.get("report_period"),
    }


def empty_activity_row(row: Dict[str, Any], manager_count: int) -> Dict[str, Any]:
    return {
        "symbol": row["symbol"],
        "slug": row.get("slug") or slugify_symbol(row["symbol"]),
        "company_name": row.get("company_name") or row["symbol"],
        "tags": row.get("tags") or [],
        "badges": [],
        "price": row.get("price"),
        "price_change_pct": row.get("price_change_pct"),
        "market_cap_usd": row.get("market_cap_usd"),
        "pe": row.get("pe"),
        "forward_pe": row.get("forward_pe"),
        "ps": row.get("ps"),
        "manager_count": manager_count,
        "buyers_count": 0,
        "sellers_count": 0,
        "holders_count": row.get("holders_count", 0),
        "total_bought_value_usd": 0.0,
        "total_sold_value_usd": 0.0,
        "new_position_value_usd": 0.0,
        "exit_value_usd": 0.0,
        "new_positions_count": 0,
        "added_count": 0,
        "reduced_count": 0,
        "exits_count": 0,
        "total_tracked_value_usd": row.get("total_tracked_value_usd", 0),
        "total_tracked_shares": row.get("total_tracked_shares", 0),
        "institutional_avg_holding_price": row.get("institutional_avg_holding_price", 0),
        "latest_institutional_buy_price": 0.0,
        "key_institution_bought": False,
        "managers": [],
        "_buy_reference_shares": 0.0,
        "_sell_reference_shares": 0.0,
    }


def aggregate_activity_rows(intervals: List[Dict[str, Any]], manager_count: int) -> List[Dict[str, Any]]:
    rows_by_symbol: Dict[str, Dict[str, Any]] = {}
    managers_by_key: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for interval in intervals:
        for row in interval.get("rows") or []:
            bucket = rows_by_symbol.setdefault(row["symbol"], empty_activity_row(row, manager_count))
            bucket["total_bought_value_usd"] += float(row.get("total_bought_value_usd") or 0)
            bucket["total_sold_value_usd"] += float(row.get("total_sold_value_usd") or 0)
            bucket["new_position_value_usd"] += float(row.get("new_position_value_usd") or 0)
            bucket["exit_value_usd"] += float(row.get("exit_value_usd") or 0)
            bucket["new_positions_count"] += int(row.get("new_positions_count") or 0)
            bucket["added_count"] += int(row.get("added_count") or 0)
            bucket["reduced_count"] += int(row.get("reduced_count") or 0)
            bucket["exits_count"] += int(row.get("exits_count") or 0)
            bucket["key_institution_bought"] = bucket["key_institution_bought"] or bool(row.get("key_institution_bought"))

            for manager in row.get("managers") or []:
                status = manager.get("status") or ""
                if status in BUY_STATUSES:
                    shares, reference, value = action_shares(manager, "buy")
                    if shares <= 0:
                        continue
                    bucket["total_bought_shares"] = bucket.get("total_bought_shares", 0.0) + shares
                    bucket["_buy_reference_shares"] += reference
                    key = (row["symbol"], normalize_cik(manager.get("cik")), "buy")
                    manager_bucket = managers_by_key.setdefault(key, aggregate_manager_action(manager, "buy", 0.0, 0.0))
                    manager_bucket["current_shares"] += shares
                    manager_bucket["change_shares"] += shares
                    manager_bucket["change_value_usd"] += value
                    manager_bucket["current_value_usd"] += value
                elif status in SELL_STATUSES:
                    shares, reference, value = action_shares(manager, "sell")
                    if shares <= 0:
                        continue
                    bucket["total_sold_shares"] = bucket.get("total_sold_shares", 0.0) + shares
                    bucket["_sell_reference_shares"] += reference
                    key = (row["symbol"], normalize_cik(manager.get("cik")), "sell")
                    manager_bucket = managers_by_key.setdefault(key, aggregate_manager_action(manager, "sell", 0.0, 0.0))
                    manager_bucket["change_shares"] -= shares
                    manager_bucket["change_value_usd"] -= value

    for (symbol, _, _), manager in managers_by_key.items():
        rows_by_symbol[symbol]["managers"].append(manager)

    output = []
    for row in rows_by_symbol.values():
        buy_ciks = {
            normalize_cik(manager.get("cik"))
            for manager in row["managers"]
            if manager.get("status") in BUY_STATUSES
        }
        sell_ciks = {
            normalize_cik(manager.get("cik"))
            for manager in row["managers"]
            if manager.get("status") in SELL_STATUSES
        }
        row["buyers_count"] = len(buy_ciks)
        row["sellers_count"] = len(sell_ciks)
        row["total_bought_shares"] = round(float(row.get("total_bought_shares") or 0), 4)
        row["total_sold_shares"] = round(float(row.get("total_sold_shares") or 0), 4)
        buy_reference = float(row.pop("_buy_reference_shares") or 0)
        sell_reference = float(row.pop("_sell_reference_shares") or 0)
        row["total_bought_shares_pct"] = round(row["total_bought_shares"] / buy_reference * 100, 4) if buy_reference > 0 else 0.0
        row["total_sold_shares_pct"] = round(row["total_sold_shares"] / sell_reference * 100, 4) if sell_reference > 0 else 0.0
        row["latest_institutional_buy_price"] = (
            round(row["total_bought_value_usd"] / row["total_bought_shares"], 4)
            if row["total_bought_shares"] > 0
            else 0.0
        )
        row["managers"].sort(key=lambda manager: ((manager.get("display_name") or manager.get("name") or ""), manager.get("status") or ""))
        output.append(row)
    return output


def intervals_for_preset(intervals: List[Dict[str, Any]], key: str, count: Optional[int]) -> List[Dict[str, Any]]:
    if key == "all":
        return intervals
    if key == "ytd":
        latest_year = str(intervals[0]["report_period"])[:4]
        selected = [interval for interval in intervals if str(interval.get("report_period") or "").startswith(latest_year)]
        return selected or intervals[:1]
    return intervals[:count]


def build_holding_periods_from_intervals(
    config: Dict[str, Any],
    intervals: List[Dict[str, Any]],
    current_interval: Optional[Dict[str, Any]] = None,
    row_limit: int = 10,
) -> List[Dict[str, Any]]:
    if current_interval:
        current_report_period = current_interval.get("report_period")
        intervals = [current_interval] + [
            interval for interval in intervals if interval.get("report_period") != current_report_period
        ]
    if not intervals:
        return []
    manager_count = enabled_manager_count(config)
    holding_rows = intervals[0].get("rows") or []
    output = []
    for key, label_key, count in HOLDING_RANGE_PRESETS:
        selected = intervals_for_preset(intervals, key, count)
        if not selected:
            continue
        period_label = activity_period_label(selected[-1].get("previous_report_period"), selected[0].get("report_period"))
        activity_rows = aggregate_activity_rows(selected, manager_count)
        output.append(
            {
                "key": key,
                "label_key": label_key,
                "report_period": selected[0].get("report_period"),
                "previous_report_period": selected[-1].get("previous_report_period"),
                "period_label": period_label,
                "rankings": holding_rankings_for_rows(activity_rows, period_label, row_limit=row_limit, holding_rows=holding_rows),
            }
        )
    return output


def build_holding_periods_from_filings(
    config: Dict[str, Any],
    filings: List[Dict[str, Any]],
    market_rows: List[Dict[str, Any]],
    row_limit: int = 10,
) -> List[Dict[str, Any]]:
    return build_holding_periods_from_intervals(
        config,
        build_holding_quarter_intervals(config, filings, market_rows),
        row_limit=row_limit,
    )


def merge_holding_periods(current_period: Dict[str, Any], historical_periods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if historical_periods:
        return historical_periods
    return [current_period]


def stock_entry(raw: Dict[str, Any], key_names: set) -> Dict[str, Any]:
    institution = raw.get("institution") or {}
    key_holders = []
    managers = []
    for manager in institution.get("managers") or []:
        manager_row = localized_manager_row(manager)
        manager_row["href"] = manager_href(manager_row.get("cik"))
        managers.append(manager_row)
        name = manager_row.get("display_name") or manager_row.get("name")
        if name in key_names and float(manager_row.get("current_shares") or 0) > 0:
            key_holders.append(manager_display_fields(manager_row))
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


def status_label_key(status: str) -> str:
    labels = {
        "new_position": "status_new_position",
        "unknown_previous": "status_new_position",
        "added": "status_added",
        "reduced": "status_reduced",
        "exited": "status_exited",
        "unchanged": "status_unchanged",
    }
    return labels.get(status, "status_unknown")


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
        "status_key": status_label_key(status),
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
            **manager_display_fields(manager),
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
    snapshot_built_at = build.get("built_at") or now
    return {
        "build_id": build.get("build_id") or re.sub(r"[-:+T]", "", now)[:14],
        "built_at": now,
        "generated_at": now,
        "snapshot_built_at": snapshot_built_at,
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


def previous_quarter_end(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        current = dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
    quarter_start_month = ((current.month - 1) // 3) * 3 + 1
    quarter_start = dt.date(current.year, quarter_start_month, 1)
    return (quarter_start - dt.timedelta(days=1)).isoformat()


def recent_activity_period_label(latest_report_period: Optional[str]) -> Optional[str]:
    latest = str(latest_report_period or "")[:10]
    previous = previous_quarter_end(latest)
    if not latest or not previous:
        return None
    return f"{previous} → {latest}"


def activity_period_label(previous_report_period: Optional[str], latest_report_period: Optional[str]) -> Optional[str]:
    latest = str(latest_report_period or "")[:10]
    previous = str(previous_report_period or "")[:10] or previous_quarter_end(latest)
    if not latest or not previous:
        return None
    return f"{previous} → {latest}"


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
    holders_by_key: Dict[str, Dict[str, str]] = {}
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
        fields = manager_display_fields(manager)
        name = manager.get("display_name_zh") or manager.get("display_name") or manager.get("name")
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
                holders_by_key[cik or name] = fields
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
    events["holders"] = list(holders_by_key.values())
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


def source_ranking_label_key(source: str) -> str:
    labels = {
        "institutional_buying": "source_institutional_buying",
        "institutional_holding": "source_institutional_holding",
        "institutional_selling": "source_institutional_selling",
        "key_new_position": "source_key_new_position",
        "key_added": "source_key_added",
        "key_holding": "source_key_holding",
        "key_buy_intensity": "source_key_buy_intensity",
        "below_key_latest_buy": "source_below_key_latest_buy",
        "key_reduced": "source_key_reduced",
        "key_exit": "source_key_exit",
    }
    return labels.get(source, source)


def buy_reason_keys(source_rankings: List[str]) -> List[str]:
    return [source_ranking_label_key(source) for source in source_rankings] or ["source_key_signal"]


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
                "reason_keys": buy_reason_keys(row["source_rankings"]),
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
    period_label = activity_period_label(
        snapshot.get("previous_13f_report_period"),
        snapshot.get("latest_13f_report_period"),
    )
    tracked_institution_order = [
        {
            "cik": normalize_cik(manager.get("cik")),
            **manager_display_fields(manager),
        }
        for manager in enabled_managers(config)
        if normalize_cik(manager.get("cik")) in institutions
    ]
    simulation = enrich_simulation_summary(
        snapshot.get("simulation") or build_snapshot_simulation(config, snapshot, combined_rows, ranks, badge_by_symbol)
    )
    localize_institution_curves(config, simulation)
    current_rankings = holding_rankings_for_rows(ranking_rows, period_label)
    current_period = {
        "key": "quarter",
        "label_key": "holding_period_quarter",
        "report_period": snapshot.get("latest_13f_report_period"),
        "previous_report_period": snapshot.get("previous_13f_report_period"),
        "period_label": period_label,
        "rankings": holding_rankings_for_rows(ranking_rows, period_label, row_limit=10),
    }
    current_interval = {
        "key": snapshot.get("latest_13f_report_period"),
        "report_period": snapshot.get("latest_13f_report_period"),
        "previous_report_period": snapshot.get("previous_13f_report_period"),
        "rows": ranking_rows,
    }
    holding_periods = (
        build_holding_periods_from_intervals(config, snapshot.get("_holding_intervals") or [], current_interval)
        or merge_holding_periods(current_period, snapshot.get("_holding_periods") or [])
    )
    payload = {
        "build": build_metadata(config, snapshot),
        "institution_curves": simulation.get("key_institution_curves") or {},
        "rankings": current_rankings,
        "holding_periods": holding_periods,
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
    parser.add_argument("--historical-holdings", type=pathlib.Path, default=ROOT / "raw/generated/historical_13f_holdings.yaml")
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "data/stockhunt.yaml")
    parser.add_argument("--content-dir", type=pathlib.Path, default=ROOT / "content/en/stocks")
    parser.add_argument("--institution-content-dir", type=pathlib.Path, default=ROOT / "content/en/institutions")
    parser.add_argument("--en-content-root", type=pathlib.Path, default=ROOT / "content/en")
    parser.add_argument("--zh-content-root", type=pathlib.Path, default=ROOT / "content/zh")
    parser.add_argument("--skip-content", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_yaml(args.config)
    snapshot = load_yaml(args.snapshot)
    if args.simulation and args.simulation.exists():
        simulation_payload = load_yaml(args.simulation)
        snapshot["simulation"] = simulation_payload.get("simulation") or simulation_payload
        snapshot["_holding_periods"] = simulation_payload.get("holding_periods") or []
        snapshot["_holding_intervals"] = simulation_payload.get("holding_intervals") or []
    if not snapshot.get("_holding_intervals") and args.historical_holdings and args.historical_holdings.exists():
        historical_payload = load_yaml(args.historical_holdings)
        snapshot["_holding_intervals"] = build_holding_quarter_intervals(
            config,
            historical_payload.get("filings") or [],
            market_rows_from_snapshot(snapshot),
        )
    data = build_hugo_data(config, snapshot)
    write_yaml(args.output, data)
    if not args.skip_content:
        ensure_static_language_pages(args.en_content_root, "en")
        ensure_static_language_pages(args.zh_content_root, "zh")
        ensure_content_pages(args.content_dir, snapshot.get("securities") or [])
        ensure_institution_pages(args.institution_content_dir, enabled_managers(config), language="en")
        ensure_content_pages(args.zh_content_root / "stocks", snapshot.get("securities") or [])
        ensure_institution_pages(args.zh_content_root / "institutions", enabled_managers(config), language="zh")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
