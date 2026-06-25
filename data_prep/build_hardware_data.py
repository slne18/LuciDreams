#!/usr/bin/env python3
"""
Build hardware_data.xlsx from exported CSVs in data_prep/output/.

Filters:
  - session duration >= 4 hours
  - at least one REM episode in rem_episodes.csv
  - all values in native_api_status_json are true (no false)
  - deduplicate sessions like plot/plot_motion_rem.py (time window + motion signature)

Run export_night_summary.py first to refresh the CSV inputs in data_prep/output/.

Default output: data_prep/output/analysis_data/hardware_data.xlsx
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT_DIR = os.path.join(BASE_DIR, "output")
DEFAULT_ANALYSIS_DATA_DIR = os.path.join(DEFAULT_INPUT_DIR, "analysis_data")
DEFAULT_OUTPUT_XLSX = os.path.join(DEFAULT_ANALYSIS_DATA_DIR, "hardware_data.xlsx")

FOUR_HOURS_SECONDS = 4 * 60 * 60

HARDWARE_COLUMNS = [
    "pid",
    "night_number",
    "condition",
    "device_time_start",
    "device_time_end",
    "rem_minutes",
    "rem_motion_avg",
    "induction_arousal_any",
    "induction_arousal_volume",
    "induction_arousal_count",
    "induction_highest_volume",
    "total_trains_delivered",
    "disruptive_arousal_any",
    "disruptive_arousal_count",
    "disruptive_arousal_volume",
    "total_induction_cues",
    "mean_train_duration_sec",
]


def read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def session_duration_seconds(start_iso: Optional[str], end_iso: Optional[str]) -> Optional[int]:
    start_dt = parse_iso(start_iso)
    end_dt = parse_iso(end_iso)
    if start_dt is None or end_dt is None:
        return None
    return int((end_dt - start_dt).total_seconds())


def as_int(value: Any) -> Optional[int]:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def as_float(value: Any) -> Optional[float]:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_bool(value: Any) -> Optional[bool]:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in {"true", "1", "yes"}:
        return True
    if s in {"false", "0", "no"}:
        return False
    return None


def bool_to_int(value: Any) -> Optional[int]:
    b = parse_bool(value)
    if b is None:
        return None
    return 1 if b else 0


def safe_slug(value: Optional[str]) -> str:
    s = "" if value is None else str(value)
    s = s.replace(":", "")
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    return s or "unknown"


def native_api_status_ok(row: Dict[str, str]) -> bool:
    raw = (row.get("native_api_status_json") or "").strip()
    if not raw:
        return False
    try:
        status = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(status, dict) or not status:
        return False
    for value in status.values():
        if value is False:
            return False
        if isinstance(value, str) and value.strip().lower() == "false":
            return False
    return True


def hms_to_seconds(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        dt = datetime.strptime(str(value), "%H:%M:%S")
        return dt.hour * 3600 + dt.minute * 60 + dt.second
    except ValueError:
        return None


def build_motion_index(
    motion_rows: List[Dict[str, str]],
) -> Tuple[
    Dict[Tuple[str, Optional[int]], Tuple[str, str]],
    Dict[Tuple[str, str, Optional[int]], str],
    Dict[Tuple[str, str, Optional[int]], int],
]:
    """One pass over motion rows: session clocks, signatures, and point counts."""
    clocks: Dict[Tuple[str, Optional[int]], Tuple[str, str]] = {}
    points_by_session: Dict[Tuple[str, str, Optional[int]], List[Tuple[int, str]]] = defaultdict(list)

    for row in motion_rows:
        pid = (row.get("pid") or "").strip()
        night = as_int(row.get("night_number"))
        start = (row.get("session_start_boston") or "").strip()
        end = (row.get("session_end_boston") or "").strip()
        if not pid or not start:
            continue

        clock_key = (pid, night)
        if clock_key not in clocks:
            clocks[clock_key] = (start, end)

        sec = as_int(row.get("second_index"))
        if sec is None:
            continue
        raw_val = row.get("motion_smoothed")
        if raw_val is None or str(raw_val).strip() == "":
            raw_val = row.get("motion_per_second", "")
        points_by_session[(pid, start, night)].append((sec, str(raw_val)))

    signatures: Dict[Tuple[str, str, Optional[int]], str] = {}
    point_counts: Dict[Tuple[str, str, Optional[int]], int] = {}
    for (pid, start, night), points in points_by_session.items():
        points.sort(key=lambda x: x[0])
        point_counts[(pid, start, night)] = len(points)
        h = hashlib.sha1()
        h.update(f"{pid}|{len(points)}".encode("utf-8"))
        for sec, val in points:
            h.update(f"{sec}:{val}|".encode("utf-8"))
        signatures[(pid, start, night)] = h.hexdigest()

    return clocks, signatures, point_counts


def infer_session_duration_from_motion(
    start_boston: str,
    end_boston: str,
    motion_point_count: int,
) -> Optional[int]:
    """Fallback duration estimate when ISO timestamps are unavailable."""
    start_s = hms_to_seconds(start_boston)
    end_s = hms_to_seconds(end_boston)
    if start_s is not None and end_s is not None:
        if end_s < start_s:
            end_s += 24 * 3600
        return int(end_s - start_s + 1)
    if motion_point_count > 0:
        return motion_point_count
    return None


def count_rem_episodes(rem_rows: List[Dict[str, str]]) -> Dict[Tuple[str, Optional[int]], int]:
    counts: Dict[Tuple[str, Optional[int]], int] = defaultdict(int)
    for row in rem_rows:
        pid = (row.get("pid") or "").strip()
        night = as_int(row.get("night_number"))
        if pid:
            counts[(pid, night)] += 1
    return counts


def aggregate_train_stats(
    train_rows: List[Dict[str, str]],
) -> Dict[Tuple[str, Optional[int]], Dict[str, Any]]:
    grouped: Dict[Tuple[str, Optional[int]], List[Dict[str, str]]] = defaultdict(list)
    for row in train_rows:
        pid = (row.get("pid") or "").strip()
        night = as_int(row.get("night_number"))
        if pid:
            grouped[(pid, night)].append(row)

    out: Dict[Tuple[str, Optional[int]], Dict[str, Any]] = {}
    for key, rows in grouped.items():
        rows.sort(key=lambda r: (as_int(r.get("episode_index")) or 0, as_int(r.get("train_index")) or 0))
        durations: List[float] = []
        total_cues = 0
        for row in rows:
            dur = as_float(row.get("train_duration_sec"))
            if dur is not None:
                durations.append(dur)
            cue_n = as_int(row.get("induction_cues_count"))
            if cue_n is not None:
                total_cues += cue_n
        mean_dur = sum(durations) / len(durations) if durations else None
        out[key] = {
            "total_induction_cues": total_cues,
            "mean_train_duration_sec": mean_dur,
        }
    return out


def build_hardware_row(
    summary_row: Dict[str, str],
    train_stats: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "pid": summary_row.get("pid"),
        "night_number": as_int(summary_row.get("night_number")),
        "condition": as_int(summary_row.get("condition")),
        "device_time_start": summary_row.get("device_time_start"),
        "device_time_end": summary_row.get("device_time_end"),
        "rem_minutes": as_float(summary_row.get("rem_minutes")),
        "rem_motion_avg": as_float(summary_row.get("rem_motion_avg")),
        "induction_arousal_any": bool_to_int(summary_row.get("induction_arousal_any")),
        "induction_arousal_volume": as_float(summary_row.get("induction_arousal_volume")),
        "induction_arousal_count": as_int(summary_row.get("induction_arousal_count")),
        "induction_highest_volume": as_float(summary_row.get("induction_highest_volume")),
        "total_trains_delivered": as_int(summary_row.get("total_trains_delivered")),
        "disruptive_arousal_any": bool_to_int(summary_row.get("disruptive_arousal_any")),
        "disruptive_arousal_count": as_int(summary_row.get("disruptive_arousal_count")),
        "disruptive_arousal_volume": as_float(summary_row.get("disruptive_arousal_volume")),
        "total_induction_cues": train_stats.get("total_induction_cues", 0),
        "mean_train_duration_sec": train_stats.get("mean_train_duration_sec"),
    }


def select_hardware_rows(
    summary_rows: List[Dict[str, str]],
    motion_rows: List[Dict[str, str]],
    rem_rows: List[Dict[str, str]],
    train_rows: List[Dict[str, str]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    clocks, signatures, point_counts = build_motion_index(motion_rows)
    rem_counts = count_rem_episodes(rem_rows)
    train_stats_by_key = aggregate_train_stats(train_rows)

    stats = {
        "input_nights": len(summary_rows),
        "excluded_short": 0,
        "excluded_no_rem": 0,
        "excluded_native_api": 0,
        "excluded_duplicate_time_window": 0,
        "excluded_duplicate_signature": 0,
        "included": 0,
    }

    candidates: List[Tuple[Dict[str, str], str, str, Optional[int]]] = []
    for row in summary_rows:
        pid = (row.get("pid") or "").strip()
        night = as_int(row.get("night_number"))
        if not pid:
            continue

        if not native_api_status_ok(row):
            stats["excluded_native_api"] += 1
            continue

        rem_n = rem_counts.get((pid, night), 0)
        if rem_n <= 0:
            stats["excluded_no_rem"] += 1
            continue

        dur = session_duration_seconds(row.get("device_time_start"), row.get("device_time_end"))
        start_boston, end_boston = clocks.get((pid, night), ("", ""))
        if dur is None and start_boston:
            motion_n = point_counts.get((pid, start_boston, night), 0)
            dur = infer_session_duration_from_motion(start_boston, end_boston, motion_n)
        if dur is None or dur < FOUR_HOURS_SECONDS:
            stats["excluded_short"] += 1
            continue

        candidates.append((row, start_boston, end_boston, night))

    candidates.sort(
        key=lambda item: (
            item[0].get("pid") or "",
            parse_iso(item[0].get("device_time_start")) or datetime.min.replace(tzinfo=timezone.utc),
            as_int(item[0].get("night_number")) or 0,
            item[0].get("session_doc_id") or "",
        )
    )

    seen_time_windows: set = set()
    seen_signatures: set = set()
    selected: List[Dict[str, Any]] = []

    for row, start_boston, end_boston, night in candidates:
        pid = row.get("pid") or ""
        time_key = (pid, safe_slug(start_boston), safe_slug(end_boston))
        if start_boston and end_boston and time_key in seen_time_windows:
            stats["excluded_duplicate_time_window"] += 1
            continue
        if start_boston and end_boston:
            seen_time_windows.add(time_key)

        if start_boston:
            sig = signatures.get((pid, start_boston, night), "")
            if sig:
                sig_key = (pid, sig)
                if sig_key in seen_signatures:
                    stats["excluded_duplicate_signature"] += 1
                    continue
                seen_signatures.add(sig_key)

        train_stats = train_stats_by_key.get((pid, night), {"total_induction_cues": 0, "mean_train_duration_sec": None})
        selected.append(build_hardware_row(row, train_stats))
        stats["included"] += 1

    selected.sort(key=lambda r: (str(r["pid"]), int(r["night_number"] or 0)))
    return selected, stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Build filtered hardware_data.xlsx from exported CSVs.")
    parser.add_argument(
        "--input-dir",
        default=DEFAULT_INPUT_DIR,
        help=f"Directory with night_summary.csv etc. (default: {DEFAULT_INPUT_DIR})",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_XLSX,
        help=f"Output Excel path (default: {DEFAULT_OUTPUT_XLSX})",
    )
    args = parser.parse_args()

    input_dir = os.path.abspath(args.input_dir)
    summary_path = os.path.join(input_dir, "night_summary.csv")
    motion_path = os.path.join(input_dir, "motion_smoothed_series.csv")
    rem_path = os.path.join(input_dir, "rem_episodes.csv")
    train_path = os.path.join(input_dir, "train_events.csv")

    for path in (summary_path, motion_path, rem_path, train_path):
        if not os.path.exists(path):
            raise FileNotFoundError(f"Missing required input: {path}. Run export_night_summary.py first.")

    summary_rows = read_csv(summary_path)
    motion_rows = read_csv(motion_path)
    rem_rows = read_csv(rem_path)
    train_rows = read_csv(train_path)

    rows, stats = select_hardware_rows(summary_rows, motion_rows, rem_rows, train_rows)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    df = pd.DataFrame(rows).reindex(columns=HARDWARE_COLUMNS)
    df.to_excel(args.output, index=False, sheet_name="hardware_data")

    print(f"Wrote {args.output} ({len(rows)} rows)")
    print(
        "Filter summary: "
        f"input={stats['input_nights']}, "
        f"included={stats['included']}, "
        f"short={stats['excluded_short']}, "
        f"no_rem={stats['excluded_no_rem']}, "
        f"native_api={stats['excluded_native_api']}, "
        f"dup_time_window={stats['excluded_duplicate_time_window']}, "
        f"dup_signature={stats['excluded_duplicate_signature']}"
    )


if __name__ == "__main__":
    main()
