#!/usr/bin/env python3
"""
Clean Qualtrics onboarding export (onboarding.csv or .xlsx).

Steps:
  1. Drop file row 1 (English Qualtrics keys) and row 3 (ImportId); use row 2 as header.
  2. Drop metadata columns through column R (UserLanguage), keep survey fields.
  3. Drop participants who answered Yes to the implanted-device / unsafe-sleep question.
  4. Set PID to Part1 + 9 + Part2 + 2 + Part3 + 3 + Part4 + 6
  5. Drop voice-consent, pacemaker, signature, and Part1–Part4 columns; rename selected survey columns.
  6. Add baseline_LD_freq_ord (0 = lowest lucid-dream frequency, 6 = highest).

Default input:  data_prep/input/onboarding.csv
Default output: data_prep/output/analysis_data/onboarding_clean.csv
"""

from __future__ import annotations

import argparse
import csv
import os
from typing import List, Sequence

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = os.path.join(BASE_DIR, "input", "onboarding.csv")
DEFAULT_OUTPUT = os.path.join(BASE_DIR, "output", "analysis_data", "onboarding_clean.csv")

# Qualtrics metadata columns A through Q (0-based indices 0-16); column R starts at Q6.
METADATA_COLUMN_COUNT = 17

PACEMAKER_COLUMN = (
    "Do you have any implanted medical devices (such as pacemakers) or any condition "
    "that would make sleeping with a phone in bed unsafe?"
)
PART_COLUMNS = ("Part1", "Part2", "Part3", "Part4")
PID_COLUMN = "PID"

VOICE_CONSENT_COLUMN = (
    "Please add your initial and date if you give permission for your voice audio to be recorded "
    "for this study."
)
SIGNATURE_COLUMN_PREFIX = "SIGNATURE OF RESEARCH PARTICIPANT OR LEGAL REPRESENTATIVE"

COLUMNS_TO_DROP = (
    VOICE_CONSENT_COLUMN,
    PACEMAKER_COLUMN,
    *PART_COLUMNS,
)

COLUMN_RENAMES = {
    "Gender - Selected Choice": "Gender",
    "How often do you experience lucid dreams?": "baseline_LD_freq",
    "Rate your sleep quality over the past week: - (0 = very poor,             10 = excellent)": (
        "baseline_sleep_qual"
    ),
}

BASELINE_LD_FREQ = "baseline_LD_freq"
BASELINE_LD_FREQ_ORD = "baseline_LD_freq_ord"
GENDER_OTHER_TEXT_COLUMN = "Gender - Other - Text"
EMPTY_COLUMNS_TO_DROP = (GENDER_OTHER_TEXT_COLUMN,)
BASELINE_LD_FREQ_ORDINAL = {
    "Never": 0,
    "Less than once per year": 1,
    "A few times per year": 2,
    "Monthly": 3,
    "Weekly": 4,
    "Several times per week": 5,
    "Daily (including multiple times per night)": 6,
}


def load_onboarding_rows(path: str) -> tuple[List[str], List[List[str]]]:
    if path.lower().endswith((".xlsx", ".xls")):
        sheet = pd.read_excel(path, header=None, dtype=str)
        rows = sheet.fillna("").astype(str).values.tolist()
    else:
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))

    if len(rows) < 4:
        raise ValueError(f"Expected at least 4 rows in {path}, found {len(rows)}")

    # Row 1 (English keys) and row 3 (ImportId) are dropped; row 2 is the header.
    header = rows[1]
    data_rows = rows[3:]
    return header, data_rows


def drop_metadata_columns(header: Sequence[str], rows: List[List[str]]) -> tuple[List[str], List[List[str]]]:
    if len(header) <= METADATA_COLUMN_COUNT:
        raise ValueError(
            f"Expected survey columns after column R, found only {len(header)} columns"
        )

    trimmed_header = list(header[METADATA_COLUMN_COUNT:])
    trimmed_rows: List[List[str]] = []
    for row in rows:
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))
        trimmed_rows.append(row[METADATA_COLUMN_COUNT:])
    return trimmed_header, trimmed_rows


def part_value(value: str) -> str:
    text = str(value).strip()
    if not text:
        return ""
    if text.endswith(".0"):
        try:
            return str(int(float(text)))
        except ValueError:
            return text
    return text


def build_pid(row: dict[str, str]) -> str:
    part1, part2, part3, part4 = (part_value(row.get(column, "")) for column in PART_COLUMNS)
    return f"{part1}9{part2}2{part3}3{part4}6"


def is_pacemaker_yes(row: dict[str, str]) -> bool:
    return row.get(PACEMAKER_COLUMN, "").strip().lower() == "yes"


def baseline_ld_freq_ord(value: str) -> str:
    label = value.strip()
    if not label:
        return ""
    try:
        return str(BASELINE_LD_FREQ_ORDINAL[label])
    except KeyError as exc:
        known = ", ".join(sorted(BASELINE_LD_FREQ_ORDINAL))
        raise ValueError(f"Unknown {BASELINE_LD_FREQ} value {label!r}. Expected one of: {known}") from exc


def signature_column(header: Sequence[str]) -> str | None:
    for name in header:
        if name.startswith(SIGNATURE_COLUMN_PREFIX):
            return name
    return None


def output_fieldnames(header: Sequence[str]) -> List[str]:
    signature = signature_column(header)
    drop = set(COLUMNS_TO_DROP)
    if signature:
        drop.add(signature)

    fieldnames: List[str] = []
    for name in header:
        if name in drop:
            continue
        renamed = COLUMN_RENAMES.get(name, name)
        fieldnames.append(renamed)
        if renamed == BASELINE_LD_FREQ:
            fieldnames.append(BASELINE_LD_FREQ_ORD)
    return fieldnames


def finalize_row(row: dict[str, str], header: Sequence[str]) -> dict[str, str]:
    signature = signature_column(header)
    drop = set(COLUMNS_TO_DROP)
    if signature:
        drop.add(signature)

    cleaned: dict[str, str] = {}
    for name in header:
        if name in drop:
            continue
        cleaned[COLUMN_RENAMES.get(name, name)] = row.get(name, "")

    if BASELINE_LD_FREQ in cleaned:
        cleaned[BASELINE_LD_FREQ_ORD] = baseline_ld_freq_ord(cleaned[BASELINE_LD_FREQ])
    return cleaned


def drop_columns_if_empty(
    rows: List[dict[str, str]],
    fieldnames: List[str],
    candidates: Sequence[str],
) -> tuple[List[dict[str, str]], List[str]]:
    drop = {
        column
        for column in candidates
        if column in fieldnames
        and all(not str(row.get(column, "")).strip() for row in rows)
    }
    if not drop:
        return rows, fieldnames

    trimmed_fieldnames = [name for name in fieldnames if name not in drop]
    trimmed_rows = [{key: value for key, value in row.items() if key not in drop} for row in rows]
    return trimmed_rows, trimmed_fieldnames


def process_onboarding(input_path: str, output_path: str) -> dict[str, int]:
    header, data_rows = load_onboarding_rows(input_path)
    header, data_rows = drop_metadata_columns(header, data_rows)

    kept_rows: List[dict[str, str]] = []
    removed_pacemaker = 0

    for raw_row in data_rows:
        if len(raw_row) < len(header):
            raw_row = raw_row + [""] * (len(header) - len(raw_row))
        row = dict(zip(header, raw_row))

        if is_pacemaker_yes(row):
            removed_pacemaker += 1
            continue

        row[PID_COLUMN] = build_pid(row)
        kept_rows.append(finalize_row(row, header))

    output_header = output_fieldnames(header)
    kept_rows, output_header = drop_columns_if_empty(kept_rows, output_header, EMPTY_COLUMNS_TO_DROP)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=output_header)
        writer.writeheader()
        writer.writerows(kept_rows)

    return {
        "input_rows": len(data_rows),
        "removed_pacemaker": removed_pacemaker,
        "output_rows": len(kept_rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean Qualtrics onboarding export.")
    parser.add_argument("--input", default=DEFAULT_INPUT, help=f"Input CSV (default: {DEFAULT_INPUT})")
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Output CSV (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    stats = process_onboarding(args.input, args.output)
    print(f"Wrote {stats['output_rows']} rows to {args.output}")
    print(
        f"Removed {stats['removed_pacemaker']} pacemaker/unsafe-sleep Yes rows "
        f"from {stats['input_rows']} participant rows"
    )


if __name__ == "__main__":
    main()
