#!/usr/bin/env python3
"""
Clean Qualtrics dream report export (dream_report.csv).

Steps:
  1. Drop file row 1 (English Qualtrics keys) and row 3 (ImportId); use row 2 as header.
  2. Drop metadata columns through column Q (UserLanguage), plus redundant embedded session fields.
  3. Rename selected survey columns; encode lucid_state and cue_notice as 0/1 (No/Yes).
  4. Drop rows with rem_minutes = 0 or session duration < 4 h (device_time_start to wake_time).

Default input:  data_prep/input/dream_report.csv
Default output: data_prep/output/analysis_data/dream_report_clean.csv
"""

from __future__ import annotations

import argparse
import csv
import os
from datetime import datetime, timezone
from typing import List, Optional, Sequence

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = os.path.join(BASE_DIR, "input", "dream_report.csv")
DEFAULT_OUTPUT = os.path.join(BASE_DIR, "output", "analysis_data", "dream_report_clean.csv")

# Qualtrics metadata columns A through Q (0-based indices 0-16).
METADATA_COLUMN_COUNT = 17
FOUR_HOURS_SECONDS = 4 * 60 * 60

REM_MINUTES_COLUMN = "rem_minutes"
DEVICE_TIME_START_COLUMN = "device_time_start"
WAKE_TIME_COLUMN = "wake_time"

LUCID_STATE_COLUMN = "Were you at any point lucid, aware that you were dreaming while still asleep?"
CUE_NOTICE_COLUMN = (
    "Did you notice any cues (sound, vibrations, light) while you were dreaming?\n\n"
    "(Note that cues may not be played during sleep on some nights)."
)

COLUMNS_TO_DROP = (
    "app_version",
    "first_train_start_time",
    "last_train_start_time",
    "arousal_threshold",
    "arousal_reached_count",
    "wake_time",
)

COLUMN_RENAMES = {
    LUCID_STATE_COLUMN: "lucid_state",
    CUE_NOTICE_COLUMN: "cue_notice",
    (
        "Indicate the degree to which you disagree or agree with the following statements in "
        "regards to the MOST lucid dream you had last night. If you did not have a lucid dream, "
        "answer it regarding the most vivid dream you remember from your sleep.\n"
        "0=Strongly disagree, 5=strongly agree - While dreaming, I was aware of the fact that "
        "the things I was experiencing in the dream were not real."
    ): "While dreaming, I was aware of the fact that the things I was experiencing in the dream were not real.",
    (
        "Indicate the degree to which you disagree or agree with the following statements in "
        "regards to the MOST lucid dream you had last night. If you did not have a lucid dream, "
        "answer it regarding the most vivid dream you remember from your sleep.\n"
        "0=Strongly disagree, 5=strongly agree - While dreaming, I was aware that the self I "
        "experienced in my dream wasn\u2019t the same as my waking self."
    ): "While dreaming, I was aware that the self I experienced in my dream wasn't the same as my waking self.",
    (
        "Indicate the degree to which you disagree or agree with the following statements in "
        "regards to the MOST lucid dream you had last night. If you did not have a lucid dream, "
        "answer it regarding the most vivid dream you remember from your sleep.\n"
        "0=Strongly disagree, 5=strongly agree - While dreaming, I was aware of the fact that "
        "the body I experienced in the dream did not correspond to my real sleeping body."
    ): (
        "While dreaming, I was aware of the fact that the body I experienced in the dream "
        "did not correspond to my real sleeping body."
    ),
    (
        "Indicate the degree to which you disagree or agree with the following statements in "
        "regards to the MOST lucid dream you had last night. If you did not have a lucid dream, "
        "answer it regarding the most vivid dream you remember from your sleep.\n"
        "0=Strongly disagree, 5=strongly agree - I was very certain that the things I was "
        "experiencing in my dream wouldn\u2019t have any consequences on the real world."
    ): (
        "I was very certain that the things I was experiencing in my dream wouldn't have "
        "any consequences on the real world."
    ),
    (
        "Indicate the degree to which you disagree or agree with the following statements in "
        "regards to the MOST lucid dream you had last night. If you did not have a lucid dream, "
        "answer it regarding the most vivid dream you remember from your sleep.\n"
        "0=Strongly disagree, 5=strongly agree - While dreaming, I often asked myself whether "
        "I was dreaming."
    ): "While dreaming, I often asked myself whether I was dreaming.",
    (
        "Indicate the degree to which you disagree or agree with the following statements in "
        "regards to the MOST lucid dream you had last night. If you did not have a lucid dream, "
        "answer it regarding the most vivid dream you remember from your sleep.\n"
        "0=Strongly disagree, 5=strongly agree - While dreaming, I was aware of the fact that "
        "other dream characters in my dream were not real."
    ): (
        "While dreaming, I was aware of the fact that other dream characters in my dream were not real."
    ),
    (
        "Indicate the degree to which you disagree or agree with the following statements in "
        "regards to last night.\n\n0=Strongly disagree, 5=strongly agree - You feel more restless "
        "than usual"
    ): "You feel more restless than usual",
    (
        "Indicate the degree to which you disagree or agree with the following statements in "
        "regards to last night.\n\n0=Strongly disagree, 5=strongly agree - You woke up more than "
        "usual during last night"
    ): "You woke up more than usual during last night",
    (
        "Indicate the degree to which you disagree or agree with the following statements in "
        "regards to last night.\n\n0=Strongly disagree, 5=strongly agree - Waking up in the "
        "morning was more difficult than usual"
    ): "Waking up in the morning was more difficult than usual",
    (
        "Indicate the degree to which you disagree or agree with the following statements in "
        "regards to last night.\n\n0=Strongly disagree, 5=strongly agree - It took longer than "
        "usual to wake up"
    ): "It took longer than usual to wake up",
    (
        "Indicate the degree to which you disagree or agree with the following statements in "
        "regards to last night.\n\n0=Strongly disagree, 5=strongly agree - You felt more tired "
        "than usual when waking up"
    ): "You felt more tired than usual when waking up",
}

BINARY_COLUMNS = {"lucid_state", "cue_notice"}


def load_dream_report_rows(path: str) -> tuple[List[str], List[List[str]]]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))

    if len(rows) < 4:
        raise ValueError(f"Expected at least 4 rows in {path}, found {len(rows)}")

    header = rows[1]
    data_rows = rows[3:]
    return header, data_rows


def drop_metadata_columns(header: Sequence[str], rows: List[List[str]]) -> tuple[List[str], List[List[str]]]:
    if len(header) <= METADATA_COLUMN_COUNT:
        raise ValueError(
            f"Expected survey columns after column Q, found only {len(header)} columns"
        )

    trimmed_header = list(header[METADATA_COLUMN_COUNT:])
    trimmed_rows: List[List[str]] = []
    for row in rows:
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))
        trimmed_rows.append(row[METADATA_COLUMN_COUNT:])
    return trimmed_header, trimmed_rows


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
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


def session_duration_seconds(device_time_start: Optional[str], wake_time: Optional[str]) -> Optional[int]:
    start_dt = parse_iso(device_time_start)
    end_dt = parse_iso(wake_time)
    if start_dt is None or end_dt is None:
        return None
    return int((end_dt - start_dt).total_seconds())


def rem_minutes_is_zero(value: str) -> bool:
    text = value.strip()
    if not text:
        return True
    try:
        return float(text) == 0
    except ValueError:
        return False


def should_drop_row(row: dict[str, str]) -> tuple[bool, Optional[str]]:
    if rem_minutes_is_zero(row.get(REM_MINUTES_COLUMN, "")):
        return True, "zero_rem"

    duration = session_duration_seconds(
        row.get(DEVICE_TIME_START_COLUMN),
        row.get(WAKE_TIME_COLUMN),
    )
    if duration is None or duration < FOUR_HOURS_SECONDS:
        return True, "short_session"

    return False, None


def yes_no_to_binary(value: str, column_name: str) -> str:
    text = value.strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered == "yes":
        return "1"
    if lowered == "no":
        return "0"
    raise ValueError(f"Expected Yes/No in {column_name}, got {value!r}")


def output_fieldnames(header: Sequence[str]) -> List[str]:
    drop = set(COLUMNS_TO_DROP)
    fieldnames: List[str] = []
    for name in header:
        if name in drop:
            continue
        fieldnames.append(COLUMN_RENAMES.get(name, name))
    return fieldnames


def finalize_row(row: dict[str, str], header: Sequence[str]) -> dict[str, str]:
    drop = set(COLUMNS_TO_DROP)
    cleaned: dict[str, str] = {}

    for name in header:
        if name in drop:
            continue

        out_name = COLUMN_RENAMES.get(name, name)
        value = row.get(name, "")
        if out_name in BINARY_COLUMNS:
            value = yes_no_to_binary(value, out_name)
        cleaned[out_name] = value

    return cleaned


def process_dream_report(input_path: str, output_path: str) -> dict[str, int]:
    header, data_rows = load_dream_report_rows(input_path)
    header, data_rows = drop_metadata_columns(header, data_rows)

    kept_rows: List[dict[str, str]] = []
    removed_zero_rem = 0
    removed_short_session = 0

    for raw_row in data_rows:
        if len(raw_row) < len(header):
            raw_row = raw_row + [""] * (len(header) - len(raw_row))
        row = dict(zip(header, raw_row))

        drop, reason = should_drop_row(row)
        if drop:
            if reason == "zero_rem":
                removed_zero_rem += 1
            elif reason == "short_session":
                removed_short_session += 1
            continue

        kept_rows.append(finalize_row(row, header))

    output_header = output_fieldnames(header)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_header)
        writer.writeheader()
        writer.writerows(kept_rows)

    return {
        "input_rows": len(data_rows),
        "removed_zero_rem": removed_zero_rem,
        "removed_short_session": removed_short_session,
        "output_rows": len(kept_rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean Qualtrics dream_report.csv export.")
    parser.add_argument("--input", default=DEFAULT_INPUT, help=f"Input CSV (default: {DEFAULT_INPUT})")
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Output CSV (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    stats = process_dream_report(args.input, args.output)
    print(f"Wrote {stats['output_rows']} rows to {args.output}")
    print(
        f"Removed {stats['removed_zero_rem']} rows with rem_minutes = 0 and "
        f"{stats['removed_short_session']} rows with session duration < 4 h "
        f"from {stats['input_rows']} participant rows"
    )


if __name__ == "__main__":
    main()
