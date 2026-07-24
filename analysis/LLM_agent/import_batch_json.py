#!/usr/bin/env python3
"""
Convert batch scoring JSON (from Parley chat or saved API responses) to CSV for merge_scores.py.

Accepts the compact columnar format from prompts/score_dream_batch.txt:
  {"n": 10, "row_id": [...], "awareness": [[...], ...], ...}

Example:
  python3 analysis/LLM_agent/import_batch_json.py --input batch_0-9.json --output output/scores.csv
  python3 analysis/LLM_agent/import_batch_json.py --input batches/*.json --output output/scores.csv --append
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Any, Dict, List

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from batch_scoring import flatten_batch_row, validate_batch_scores  # noqa: E402

OUTPUT_FIELDS = [
    "row_id",
    "awareness_score",
    "control_score",
    "cue_incorporation",
    "bizarreness_count",
    "awareness_score_1",
    "awareness_score_2",
    "awareness_score_3",
    "control_score_1",
    "control_score_2",
    "control_score_3",
    "cue_incorporation_1",
    "cue_incorporation_2",
    "cue_incorporation_3",
    "bizarreness_count_1",
    "bizarreness_count_2",
    "bizarreness_count_3",
    "llm_provider",
    "llm_model",
    "error",
]


def load_payload(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        text = f.read().strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(line for line in lines if not line.strip().startswith("```"))
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected JSON object, got {type(data).__name__}")
    return data


def rows_from_payload(payload: Dict[str, Any], *, source: str, model: str) -> List[Dict[str, Any]]:
    if payload.get("error"):
        raise ValueError(f"Batch JSON error: {payload['error']}")
    row_ids = [int(x) for x in payload["row_id"]]
    validate_batch_scores(payload, expected_row_ids=row_ids)
    rows: List[Dict[str, Any]] = []
    for idx in range(payload["n"]):
        row = flatten_batch_row(payload, idx)
        row.update(
            {
                "llm_provider": "parley-batch-manual",
                "llm_model": model,
                "error": "",
            }
        )
        rows.append(row)
    return rows


def write_rows(path: str, rows: List[Dict[str, Any]], *, append: bool) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    write_header = not (append and os.path.isfile(path))
    mode = "a" if append and os.path.isfile(path) else "w"
    with open(path, mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import batch scoring JSON into scores CSV.")
    parser.add_argument("--input", nargs="+", required=True, help="One or more batch JSON files")
    parser.add_argument("--output", required=True, help="Output CSV for merge_scores.py")
    parser.add_argument("--model", default="manual", help="Label for llm_model column")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to existing CSV (use when merging multiple batch files)",
    )
    args = parser.parse_args()

    all_rows: List[Dict[str, Any]] = []
    for path in args.input:
        payload = load_payload(path)
        all_rows.extend(rows_from_payload(payload, source=path, model=args.model))

    all_rows.sort(key=lambda r: int(r["row_id"]))
    write_rows(args.output, all_rows, append=args.append)
    print(f"Wrote {len(all_rows)} rows to {os.path.abspath(args.output)}")


if __name__ == "__main__":
    main()
