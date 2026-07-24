#!/usr/bin/env python3
"""
Export dream text columns from merged_data.xlsx to CSV for batch LLM scoring.

Output columns:
  row_id, pid, <5 Qualtrics text fields>, dream_text

Example:
  python3 analysis/LLM_agent/export_dream_csv.py
  python3 analysis/LLM_agent/export_dream_csv.py --output prompts/data_test.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from typing import List, Optional

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))
DEFAULT_INPUT = os.path.join(
    REPO_ROOT, "data_prep", "output", "analysis_data", "merged_data.xlsx"
)
DEFAULT_OUTPUT = os.path.join(BASE_DIR, "output", "dream_text_export.csv")

sys.path.insert(0, BASE_DIR)

from build_dream_text import build_dream_narrative  # noqa: E402
from dream_text_columns import DREAM_TEXT_COLUMNS, DREAM_TEXT_EXPORT_COLUMNS  # noqa: E402


def resolve_input_path(explicit: str) -> str:
    env_path = os.getenv("LUCIDREAMS_MERGED_DATA", "")
    for candidate in (explicit, env_path, DEFAULT_INPUT):
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    raise FileNotFoundError(
        f"Could not find merged data. Tried: {explicit}, {env_path}, {DEFAULT_INPUT}"
    )


def cell_str(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def export_dream_csv(
    *,
    input_path: str,
    output_path: str,
    start_row: int = 0,
    limit: Optional[int] = None,
) -> int:
    required = ["pid", *DREAM_TEXT_COLUMNS]
    df = pd.read_excel(input_path)
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Missing columns in {input_path}: {missing}")

    row_ids = list(range(start_row, len(df)))
    if limit is not None:
        row_ids = row_ids[:limit]

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    fieldnames: List[str] = list(DREAM_TEXT_EXPORT_COLUMNS)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for row_id in row_ids:
            row = df.iloc[row_id]
            record = {
                "row_id": row_id,
                "pid": cell_str(row["pid"]),
                "dream_text": build_dream_narrative(row),
            }
            for col in DREAM_TEXT_COLUMNS:
                record[col] = cell_str(row.get(col, ""))
            writer.writerow(record)

    return len(row_ids)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export dream text columns from merged_data.xlsx to CSV."
    )
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Path to merged_data.xlsx")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output CSV path")
    parser.add_argument("--start-row", type=int, default=0, help="First row_id (0-based)")
    parser.add_argument("--limit", type=int, default=None, help="Max rows to export")
    args = parser.parse_args()

    input_path = resolve_input_path(args.input)
    n = export_dream_csv(
        input_path=input_path,
        output_path=args.output,
        start_row=args.start_row,
        limit=args.limit,
    )

    print(f"Input:  {input_path}")
    print(f"Output: {os.path.abspath(args.output)}")
    print(f"Rows:   {n}")


if __name__ == "__main__":
    main()
