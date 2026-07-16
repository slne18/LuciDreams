#!/usr/bin/env python3
"""
Merge hardware_data.xlsx with dream_report_clean.csv.

Each output row corresponds to one dream-report row. The matching hardware session is
looked up as follows:
  1. Same pid and device_time_start, with matching rem_minutes
  2. If several hardware rows share that start time, pick the closest night_number
  3. Otherwise same pid and rem_minutes (unique match, or night_number tie-break)
  4. If no reliable match is found, keep the dream row without hardware fields
     and record the reason in merged_data_log.csv
  5. Left-join onboarding baseline data by pid (repeated on each night row)
  6. Drop night_number from the final output (kept only for matching/logging)

Default inputs:
  data_prep/output/analysis_data/hardware_data.xlsx
  data_prep/output/analysis_data/dream_report_clean.csv
  data_prep/output/analysis_data/onboarding_clean.csv
  data_prep/output/night_summary.csv (optional, for richer unmatched reasons)

Default outputs:
  data_prep/output/analysis_data/merged_data.xlsx
  data_prep/output/analysis_data/merged_data_log.csv
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from typing import Optional, Sequence

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_DIR = os.path.join(BASE_DIR, "output")
DEFAULT_ANALYSIS_DATA_DIR = os.path.join(DEFAULT_OUTPUT_DIR, "analysis_data")
DEFAULT_HARDWARE_XLSX = os.path.join(DEFAULT_ANALYSIS_DATA_DIR, "hardware_data.xlsx")
DEFAULT_DREAM_CSV = os.path.join(DEFAULT_ANALYSIS_DATA_DIR, "dream_report_clean.csv")
DEFAULT_ONBOARDING_CSV = os.path.join(DEFAULT_ANALYSIS_DATA_DIR, "onboarding_clean.csv")
DEFAULT_NIGHT_SUMMARY_CSV = os.path.join(DEFAULT_OUTPUT_DIR, "night_summary.csv")
DEFAULT_OUTPUT_XLSX = os.path.join(DEFAULT_ANALYSIS_DATA_DIR, "merged_data.xlsx")
DEFAULT_MERGE_LOG_CSV = os.path.join(DEFAULT_ANALYSIS_DATA_DIR, "merged_data_log.csv")

FOUR_HOURS_SECONDS = 4 * 60 * 60

# Dream report repeats some hardware/session fields embedded from the app export.
DREAM_DUPLICATE_COLUMNS = (
    "condition",
    "induction_highest_volume",
    "rem_minutes",
    "rem_motion_avg",
    "total_trains_delivered",
)
DREAM_REDUNDANT_COLUMNS = (
    "arousal_threshold",
    "arousal_reached_count",
    "wake_time",
)
OUTPUT_COLUMNS_TO_DROP = ("night_number", "device_time_start")

HARDWARE_KEY_COLUMNS = ("pid", "night_number", "device_time_start")
REM_MINUTES_COLUMN = "rem_minutes"
ONBOARDING_PID_COLUMN = "PID"
ONBOARDING_BASELINE_COLUMNS = (
    "Age",
    "Gender",
    "Gender - Other - Text",
    "baseline_LD_freq",
    "baseline_LD_freq_ord",
    "baseline_sleep_qual",
)
GENDER_OTHER_TEXT_COLUMN = "Gender - Other - Text"


def normalize_pid(value: object) -> str:
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def normalize_night_number(value: object) -> int:
    return int(float(str(value).strip()))


def parse_iso(value: object) -> Optional[datetime]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def normalize_rem_minutes(value: object) -> Optional[int]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    try:
        return int(round(float(text)))
    except ValueError:
        return None


def normalize_device_time_start(value: object) -> str:
    dt = parse_iso(value)
    if dt is None:
        return ""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def prepare_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    prepared = df.copy()
    prepared["pid"] = prepared["pid"].map(normalize_pid)
    prepared["night_number"] = prepared["night_number"].map(normalize_night_number)
    prepared["device_time_start"] = prepared["device_time_start"].map(normalize_device_time_start)
    prepared["start_key"] = prepared["device_time_start"]
    prepared["rem_key"] = prepared[REM_MINUTES_COLUMN].map(normalize_rem_minutes)
    return prepared


def prepare_onboarding(df: pd.DataFrame) -> pd.DataFrame:
    if ONBOARDING_PID_COLUMN not in df.columns:
        raise ValueError(f"Missing {ONBOARDING_PID_COLUMN} column in onboarding data")

    prepared = df.copy()
    prepared["pid"] = prepared[ONBOARDING_PID_COLUMN].map(normalize_pid)
    return prepared.drop_duplicates("pid")


def drop_empty_columns(df: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    trimmed = df.copy()
    for column in columns:
        if column not in trimmed.columns:
            continue
        if trimmed[column].fillna("").astype(str).str.strip().eq("").all():
            trimmed = trimmed.drop(columns=[column])
    return trimmed


def merge_onboarding_baseline(
    merged: pd.DataFrame,
    onboarding_path: Optional[str],
) -> tuple[pd.DataFrame, int]:
    if not onboarding_path or not os.path.isfile(onboarding_path):
        return merged, 0

    onboarding = prepare_onboarding(pd.read_csv(onboarding_path))
    baseline_columns = [col for col in ONBOARDING_BASELINE_COLUMNS if col in onboarding.columns]
    onboarding = onboarding[["pid", *baseline_columns]]

    merged = merged.copy()
    merged["pid"] = merged["pid"].map(normalize_pid)
    merged = merged.merge(onboarding, on="pid", how="left", validate="m:1")
    merged = drop_empty_columns(merged, (GENDER_OTHER_TEXT_COLUMN,))
    baseline_columns = [col for col in baseline_columns if col in merged.columns]

    if baseline_columns:
        without_onboarding = merged[baseline_columns[0]].isna().sum()
    else:
        without_onboarding = 0

    if "pid" in merged.columns and baseline_columns:
        other_columns = [col for col in merged.columns if col not in baseline_columns]
        pid_index = other_columns.index("pid") + 1
        ordered_columns = other_columns[:pid_index] + baseline_columns + other_columns[pid_index:]
        merged = merged[ordered_columns]

    return merged, without_onboarding


def pick_closest_night_number(candidates: pd.DataFrame, dream_row: pd.Series) -> pd.Series:
    ranked = candidates.assign(
        night_delta=(candidates["night_number"] - dream_row["night_number"]).abs()
    ).sort_values(["night_delta", "night_number"])
    return ranked.iloc[0]


def match_hardware_row(dream_row: pd.Series, hardware: pd.DataFrame) -> tuple[Optional[pd.Series], Optional[str]]:
    candidates = hardware[hardware["pid"] == dream_row["pid"]]
    if candidates.empty or dream_row["rem_key"] is None:
        return None, None

    exact_start = candidates[candidates["start_key"] == dream_row["start_key"]]
    if not exact_start.empty:
        exact_start = exact_start[exact_start["rem_key"] == dream_row["rem_key"]]
        if exact_start.empty:
            return None, None
        if len(exact_start) == 1:
            return exact_start.iloc[0], "exact_start"
        return pick_closest_night_number(exact_start, dream_row), "exact_start_night_tiebreak"

    rem_matches = candidates[candidates["rem_key"] == dream_row["rem_key"]]
    if rem_matches.empty:
        return None, None
    if len(rem_matches) == 1:
        return rem_matches.iloc[0], "rem_minutes"
    return pick_closest_night_number(rem_matches, dream_row), "rem_minutes_night_tiebreak"


def native_api_failure_fields(status_json: str) -> list[str]:
    raw = (status_json or "").strip()
    if not raw:
        return ["missing_native_api_status"]
    try:
        status = json.loads(raw)
    except json.JSONDecodeError:
        return ["invalid_native_api_status_json"]
    if not isinstance(status, dict) or not status:
        return ["empty_native_api_status"]

    failed: list[str] = []
    for key, value in status.items():
        if value is False or (isinstance(value, str) and value.strip().lower() == "false"):
            failed.append(key)
    return failed


def night_summary_exclusion_reasons(summary_row: pd.Series) -> list[str]:
    reasons: list[str] = []

    failed_api = native_api_failure_fields(str(summary_row.get("native_api_status_json", "")))
    if failed_api:
        reasons.append(f"excluded_from_hardware_data: native_api false ({', '.join(failed_api)})")

    rem_minutes = normalize_rem_minutes(summary_row.get(REM_MINUTES_COLUMN))
    if rem_minutes in (None, 0):
        reasons.append("excluded_from_hardware_data: no REM (rem_minutes = 0)")

    duration = session_duration_seconds(
        summary_row.get("device_time_start"),
        summary_row.get("device_time_end"),
    )
    if duration is None:
        reasons.append("excluded_from_hardware_data: could not compute session duration")
    elif duration < FOUR_HOURS_SECONDS:
        hours = duration / 3600
        reasons.append(f"excluded_from_hardware_data: session duration {hours:.2f} h < 4 h")

    return reasons


def session_duration_seconds(device_time_start: object, device_time_end: object) -> Optional[int]:
    start_dt = parse_iso(device_time_start)
    end_dt = parse_iso(device_time_end)
    if start_dt is None or end_dt is None:
        return None
    return int((end_dt - start_dt).total_seconds())


def explain_unmatched(
    dream_row: pd.Series,
    hardware: pd.DataFrame,
    night_summary: Optional[pd.DataFrame],
) -> dict[str, object]:
    reasons: list[str] = []

    if dream_row["rem_key"] is None:
        reasons.append("invalid_or_missing_rem_minutes_in_dream_report")

    pid_hardware = hardware[hardware["pid"] == dream_row["pid"]]
    if pid_hardware.empty:
        reasons.append("pid_not_in_hardware_data")
    else:
        start_matches = pid_hardware[pid_hardware["start_key"] == dream_row["start_key"]]
        if not start_matches.empty:
            rem_values = sorted({value for value in start_matches["rem_key"].dropna().unique()})
            reasons.append(
                "pid_and_device_time_start_match_hardware_but_rem_minutes_differ "
                f"(dream={dream_row['rem_key']}, hardware={rem_values})"
            )
        else:
            rem_matches = pid_hardware[pid_hardware["rem_key"] == dream_row["rem_key"]]
            if rem_matches.empty:
                hardware_rem = sorted({value for value in pid_hardware["rem_key"].dropna().unique()})
                reasons.append(
                    "no_hardware_row_with_matching_device_time_start_or_rem_minutes "
                    f"(dream rem={dream_row['rem_key']}, hardware rem values={hardware_rem})"
                )
            else:
                reasons.append(
                    "multiple_hardware_rows_share_pid_and_rem_minutes_but_no_matching_device_time_start"
                )

    night_summary_night_number = ""
    night_summary_session_doc_id = ""
    if night_summary is None:
        reasons.append("night_summary_not_available_for_additional_checks")
    else:
        pid_summary = night_summary[night_summary["pid"] == dream_row["pid"]]
        if pid_summary.empty:
            reasons.append("pid_not_in_night_summary")
        else:
            start_match = pid_summary[pid_summary["start_key"] == dream_row["start_key"]]
            if start_match.empty:
                reasons.append("pid_in_night_summary_but_no_row_with_matching_device_time_start")
            else:
                summary_row = start_match.iloc[0]
                night_summary_night_number = summary_row.get("night_number", "")
                night_summary_session_doc_id = summary_row.get("session_doc_id", "")
                reasons.extend(night_summary_exclusion_reasons(summary_row))

    return {
        "pid": dream_row["pid"],
        "night_number": dream_row["night_number"],
        "device_time_start": dream_row["device_time_start"],
        "rem_minutes": dream_row.get(REM_MINUTES_COLUMN, ""),
        "night_summary_night_number": night_summary_night_number,
        "night_summary_session_doc_id": night_summary_session_doc_id,
        "reason": " | ".join(reasons),
    }


def enrich_hardware_for_matching(
    hardware: pd.DataFrame,
    night_summary: Optional[pd.DataFrame],
) -> pd.DataFrame:
    """Add device_time_start from night_summary for dream-report matching."""
    if "device_time_start" in hardware.columns:
        return hardware

    if night_summary is None or "device_time_start" not in night_summary.columns:
        raise ValueError(
            "hardware_data.xlsx has no device_time_start column; "
            "provide night_summary.csv to match dream reports."
        )

    summary = night_summary.copy()
    enriched = hardware.copy()
    enriched["pid"] = enriched["pid"].map(normalize_pid)
    enriched["night_number"] = enriched["night_number"].map(normalize_night_number)
    summary["pid"] = summary["pid"].map(normalize_pid)
    summary["night_number"] = summary["night_number"].map(normalize_night_number)
    summary_keys = summary[["pid", "night_number", "device_time_start"]].drop_duplicates(
        subset=["pid", "night_number"]
    )
    return enriched.merge(summary_keys, on=["pid", "night_number"], how="left")


def merge_hardware_and_dream(
    hardware_path: str,
    dream_path: str,
    output_xlsx: str,
    merge_log_csv: str,
    night_summary_path: Optional[str] = None,
    onboarding_path: Optional[str] = None,
) -> dict[str, int]:
    hardware_raw = pd.read_excel(hardware_path)
    night_summary: Optional[pd.DataFrame] = None
    if night_summary_path and os.path.isfile(night_summary_path):
        night_summary = pd.read_csv(night_summary_path)

    hardware = prepare_dataframe(enrich_hardware_for_matching(hardware_raw, night_summary))
    dream = prepare_dataframe(pd.read_csv(dream_path))

    if night_summary is not None:
        night_summary = prepare_dataframe(night_summary)

    missing_dream_keys = [col for col in HARDWARE_KEY_COLUMNS if col not in dream.columns]
    if missing_dream_keys:
        raise ValueError(f"Missing key columns in dream report: {missing_dream_keys}")

    dream_columns_to_drop = [
        col
        for col in (*DREAM_DUPLICATE_COLUMNS, *DREAM_REDUNDANT_COLUMNS)
        if col in dream.columns
    ]
    dream_for_merge = dream.drop(columns=dream_columns_to_drop + ["start_key", "rem_key"])

    hardware_value_columns = [
        col
        for col in hardware.columns
        if col not in HARDWARE_KEY_COLUMNS and col not in ("start_key", "rem_key")
    ]

    merged_rows: list[dict[str, object]] = []
    unmatched_log_rows: list[dict[str, object]] = []
    matched_exact_start = 0
    matched_exact_start_night_tiebreak = 0
    matched_rem_minutes = 0
    matched_rem_minutes_night_tiebreak = 0
    unmatched = 0

    for index, _dream_row in dream.iterrows():
        dream_row = dream.loc[index]
        output_row = dream_for_merge.loc[index].to_dict()
        hardware_row, match_kind = match_hardware_row(dream_row, hardware)

        if hardware_row is None:
            unmatched += 1
            unmatched_log_rows.append(
                explain_unmatched(dream_row, hardware, night_summary)
            )
            merged_rows.append(output_row)
            continue

        if match_kind == "exact_start":
            matched_exact_start += 1
        elif match_kind == "exact_start_night_tiebreak":
            matched_exact_start_night_tiebreak += 1
        elif match_kind == "rem_minutes":
            matched_rem_minutes += 1
        elif match_kind == "rem_minutes_night_tiebreak":
            matched_rem_minutes_night_tiebreak += 1

        for column in hardware_value_columns:
            output_row[column] = hardware_row[column]

        merged_rows.append(output_row)

    merged = pd.DataFrame(merged_rows)
    merged, onboarding_unmatched_rows = merge_onboarding_baseline(merged, onboarding_path)
    merged = merged.drop(columns=[col for col in OUTPUT_COLUMNS_TO_DROP if col in merged.columns])

    os.makedirs(os.path.dirname(output_xlsx) or ".", exist_ok=True)
    merged.to_excel(output_xlsx, index=False)

    log_columns = [
        "pid",
        "night_number",
        "device_time_start",
        "rem_minutes",
        "night_summary_night_number",
        "night_summary_session_doc_id",
        "reason",
    ]
    pd.DataFrame(unmatched_log_rows, columns=log_columns).to_csv(merge_log_csv, index=False)

    return {
        "hardware_rows": len(hardware),
        "dream_rows": len(dream),
        "merged_rows": len(merged),
        "matched_exact_start": matched_exact_start,
        "matched_exact_start_night_tiebreak": matched_exact_start_night_tiebreak,
        "matched_rem_minutes": matched_rem_minutes,
        "matched_rem_minutes_night_tiebreak": matched_rem_minutes_night_tiebreak,
        "unmatched": unmatched,
        "merge_log_rows": len(unmatched_log_rows),
        "onboarding_unmatched_rows": onboarding_unmatched_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge hardware and dream report data.")
    parser.add_argument(
        "--hardware",
        default=DEFAULT_HARDWARE_XLSX,
        help=f"Hardware XLSX (default: {DEFAULT_HARDWARE_XLSX})",
    )
    parser.add_argument(
        "--dream",
        default=DEFAULT_DREAM_CSV,
        help=f"Dream report CSV (default: {DEFAULT_DREAM_CSV})",
    )
    parser.add_argument(
        "--onboarding",
        default=DEFAULT_ONBOARDING_CSV,
        help=f"Onboarding CSV (default: {DEFAULT_ONBOARDING_CSV})",
    )
    parser.add_argument(
        "--night-summary",
        default=DEFAULT_NIGHT_SUMMARY_CSV,
        help=f"Night summary CSV for unmatched reasons (default: {DEFAULT_NIGHT_SUMMARY_CSV})",
    )
    parser.add_argument(
        "--merge-log",
        default=DEFAULT_MERGE_LOG_CSV,
        help=f"Unmatched merge log CSV (default: {DEFAULT_MERGE_LOG_CSV})",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_XLSX,
        help=f"Merged XLSX (default: {DEFAULT_OUTPUT_XLSX})",
    )
    args = parser.parse_args()

    stats = merge_hardware_and_dream(
        args.hardware,
        args.dream,
        args.output,
        args.merge_log,
        args.night_summary,
        args.onboarding,
    )
    print(f"Wrote {stats['merged_rows']} rows to {args.output}")
    print(f"Wrote {stats['merge_log_rows']} unmatched rows to {args.merge_log}")
    print(f"{stats['onboarding_unmatched_rows']} rows had no onboarding baseline match by pid")
    print(
        "Matched dream rows to hardware sessions: "
        f"{stats['matched_exact_start']} pid+device_time_start+rem_minutes, "
        f"{stats['matched_exact_start_night_tiebreak']} pid+start+rem with night_number tie-break, "
        f"{stats['matched_rem_minutes']} pid+rem_minutes, "
        f"{stats['matched_rem_minutes_night_tiebreak']} pid+rem_minutes with night_number tie-break, "
        f"{stats['unmatched']} unmatched"
    )


if __name__ == "__main__":
    main()
