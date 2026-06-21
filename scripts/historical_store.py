"""Read and write historical simulation stores.

The store format is a directory with JSON metadata and JSONL row files. Legacy
YAML files remain readable so older caches can be migrated without a special
step.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import tempfile
from typing import Any, Dict, Iterable, List, Optional

import yaml


STORE_VERSION = "historical-jsonl-v1"


def read_json(path: pathlib.Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: pathlib.Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, sort_keys=False, separators=(",", ":"))


def read_jsonl(path: pathlib.Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: pathlib.Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=False, separators=(",", ":")))
            handle.write("\n")


def load_store(path: pathlib.Path) -> Dict[str, Any]:
    """Load either a JSONL directory store or a legacy YAML/JSON payload."""
    if not path.exists():
        return {}
    if path.is_file():
        with path.open("r", encoding="utf-8") as handle:
            if path.suffix.lower() == ".json":
                payload = json.load(handle)
            else:
                payload = yaml.safe_load(handle) or {}
        if not isinstance(payload, dict):
            raise ValueError(f"{path} must contain an object")
        return payload

    simulation_dir = path / "simulation"
    key_institution_curves = _read_key_institution_curves(simulation_dir / "key_institution_curves")
    simulation = {
        "meta": read_json(simulation_dir / "meta.json", {}) or {},
        "summary": read_json(simulation_dir / "summary.json", {}) or {},
        "current_positions": read_jsonl(simulation_dir / "current_positions.jsonl"),
        "equity_curve": read_jsonl(simulation_dir / "equity_curve.jsonl"),
        "equity_chart_series": _hydrate_equity_chart_series(
            read_json(simulation_dir / "equity_chart_series.json", []) or [],
            key_institution_curves,
        ),
        "key_institution_curves": key_institution_curves,
        "rebalance_history": read_jsonl(simulation_dir / "rebalance_history.jsonl"),
        "last_candidate_symbols": read_json(simulation_dir / "last_candidate_symbols.json", []) or [],
        "checkpoints": read_jsonl(simulation_dir / "checkpoints.jsonl"),
    }
    return {
        "build": _read_build(path),
        "simulation": simulation,
        "holding_intervals": read_jsonl(path / "holding_intervals.jsonl"),
    }


def write_store(path: pathlib.Path, payload: Dict[str, Any]) -> None:
    """Write payload as JSONL directory, or legacy YAML when path is a YAML file."""
    if path.suffix.lower() in {".yaml", ".yml"}:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False, width=120)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_root = pathlib.Path(tempfile.mkdtemp(prefix=f".{path.name}-", dir=path.parent))
    try:
        _write_store_dir(temp_root, payload)
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        temp_root.replace(path)
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise


def last_rebalance_date(path: pathlib.Path) -> Optional[str]:
    payload = load_store(path)
    return (
        payload.get("simulation", {})
        .get("meta", {})
        .get("last_rebalance_date")
    )


def last_equity_date(payload: Dict[str, Any]) -> Optional[str]:
    curve = payload.get("simulation", {}).get("equity_curve") or []
    if not curve:
        return None
    return str(curve[-1].get("date") or "") or None


def _read_build(path: pathlib.Path) -> Dict[str, Any]:
    build = read_json(path / "build.json", {}) or {}
    fingerprint = read_jsonl(path / "filing_fingerprint.jsonl")
    if fingerprint:
        build["filing_fingerprint"] = fingerprint
    return build


def _write_store_dir(path: pathlib.Path, payload: Dict[str, Any]) -> None:
    build = dict(payload.get("build") or {})
    build["store_version"] = STORE_VERSION
    fingerprint = build.pop("filing_fingerprint", None) or []
    write_json(path / "build.json", build)
    write_jsonl(path / "filing_fingerprint.jsonl", fingerprint)
    write_jsonl(path / "holding_intervals.jsonl", payload.get("holding_intervals") or [])

    simulation = payload.get("simulation") or {}
    simulation_dir = path / "simulation"
    write_json(simulation_dir / "meta.json", simulation.get("meta") or {})
    write_json(simulation_dir / "summary.json", simulation.get("summary") or {})
    write_jsonl(simulation_dir / "current_positions.jsonl", simulation.get("current_positions") or [])
    write_jsonl(simulation_dir / "equity_curve.jsonl", simulation.get("equity_curve") or [])
    write_json(simulation_dir / "equity_chart_series.json", _dehydrate_chart_series(simulation.get("equity_chart_series") or []))
    write_jsonl(simulation_dir / "rebalance_history.jsonl", simulation.get("rebalance_history") or [])
    write_json(simulation_dir / "last_candidate_symbols.json", simulation.get("last_candidate_symbols") or [])
    write_jsonl(simulation_dir / "checkpoints.jsonl", simulation.get("checkpoints") or [])
    _write_key_institution_curves(simulation_dir / "key_institution_curves", simulation.get("key_institution_curves") or {})


def _read_key_institution_curves(path: pathlib.Path) -> Dict[str, Dict[str, Any]]:
    curves: Dict[str, Dict[str, Any]] = {}
    if not path.exists():
        return curves
    for curve_dir in sorted(child for child in path.iterdir() if child.is_dir()):
        meta = read_json(curve_dir / "meta.json", {}) or {}
        series = read_json(curve_dir / "series.json", {}) or {}
        points = read_jsonl(curve_dir / "points.jsonl")
        benchmark_curve = read_jsonl(curve_dir / "benchmark_curve.jsonl")
        chart_series = read_json(curve_dir / "chart_series.json", []) or []
        series["points"] = points
        curve = {
            **meta,
            "series": series,
            "points": points,
            "benchmark_curve": benchmark_curve,
            "chart_series": _hydrate_chart_series(chart_series, series),
        }
        curves[curve_dir.name] = curve
    return curves


def _write_key_institution_curves(path: pathlib.Path, curves: Dict[str, Dict[str, Any]]) -> None:
    for slug, curve in sorted(curves.items()):
        curve_dir = path / slug
        series = dict(curve.get("series") or {})
        series.pop("points", None)
        meta = {
            key: value
            for key, value in curve.items()
            if key not in {"series", "points", "benchmark_curve", "chart_series"}
        }
        write_json(curve_dir / "meta.json", meta)
        write_json(curve_dir / "series.json", series)
        write_jsonl(curve_dir / "points.jsonl", curve.get("points") or curve.get("series", {}).get("points") or [])
        write_jsonl(curve_dir / "benchmark_curve.jsonl", curve.get("benchmark_curve") or [])
        write_json(curve_dir / "chart_series.json", _dehydrate_chart_series(curve.get("chart_series") or []))


def _dehydrate_chart_series(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    output = []
    for row in rows:
        item = dict(row)
        item.pop("points", None)
        output.append(item)
    return output


def _hydrate_chart_series(rows: List[Dict[str, Any]], series: Dict[str, Any]) -> List[Dict[str, Any]]:
    output = []
    institution_key = series.get("key")
    for row in rows:
        item = dict(row)
        if item.get("key") == institution_key:
            item["points"] = series.get("points") or []
        output.append(item)
    return output


def _hydrate_equity_chart_series(
    rows: List[Dict[str, Any]],
    curves: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not rows:
        return [curve.get("series") or {} for curve in curves.values() if curve.get("series")]
    series_by_key = {
        curve.get("series", {}).get("key"): curve.get("series") or {}
        for curve in curves.values()
        if curve.get("series", {}).get("key")
    }
    output = []
    for row in rows:
        full_series = series_by_key.get(row.get("key"))
        output.append(dict(full_series) if full_series else dict(row))
    return output
