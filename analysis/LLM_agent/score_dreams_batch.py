#!/usr/bin/env python3
"""
Batch dream scoring via Parley (compact JSON, chunked).

Scores up to 10 nights per API call using prompts/score_dream_batch.txt.
Triple scores per metric; main columns are means of the three passes.

Example:
  python3 analysis/LLM_agent/score_dreams_batch.py --limit 20 --batch-size 10
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

DEFAULT_INPUT = os.path.join(
    REPO_ROOT, "data_prep", "output", "analysis_data", "merged_data.xlsx"
)
DEFAULT_OUTPUT_DIR = os.path.join(BASE_DIR, "output")
DEFAULT_PROMPT = os.path.join(BASE_DIR, "prompts", "score_dream_batch.txt")
MAX_BATCH_SIZE = 10

sys.path.insert(0, BASE_DIR)

from batch_scoring import flatten_batch_row, validate_batch_scores  # noqa: E402
from build_dream_text import build_dream_narrative, has_any_dream_text  # noqa: E402
from dream_text_columns import DREAM_TEXT_COLUMNS  # noqa: E402
from llm_client import extract_json_object, load_system_prompt  # noqa: E402
from parley import (  # noqa: E402
    DEFAULT_PARLEY_MODEL,
    build_parley_client,
    check_parley_gateway,
    resolve_parley_api_key,
)
from score_dreams import (  # noqa: E402
    DEFAULT_INPUT as SCORE_DEFAULT_INPUT,
    READ_COLUMNS,
    append_row,
    base_row,
    load_completed_row_ids,
    resolve_input_path,
)

OUTPUT_FIELDS = [
    "row_id",
    "pid",
    "condition",
    "lucid_state",
    "cue_notice",
    "time_asleep",
    "has_dream_text",
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
    "batch_size",
    "scored_at_utc",
    "error",
]


def build_batch_user_message(df: pd.DataFrame, row_ids: List[int]) -> str:
    lines = [
        "Score every row below. Return compact columnar JSON (max 10 rows).",
        "",
        "| row_id | pid | dream_text |",
        "|--------|-----|------------|",
    ]
    for row_id in row_ids:
        row = df.iloc[row_id]
        text = build_dream_narrative(row).replace("\n", " ").replace("|", "\\|")
        if len(text) > 3500:
            text = text[:3500] + "…"
        pid = str(row.get("pid", ""))
        lines.append(f"| {row_id} | {pid} | {text or '[empty]'} |")
    return "\n".join(lines)


def score_batch_chunk(
    client: Any,
    df: pd.DataFrame,
    row_ids: List[int],
) -> Dict[str, Any]:
    user_message = build_batch_user_message(df, row_ids)
    response = client._client.chat.completions.create(
        model=client.model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": client.system_prompt},
            {"role": "user", "content": user_message},
        ],
    )
    content = response.choices[0].message.content or ""
    payload = extract_json_object(content)
    validate_batch_scores(payload, expected_row_ids=row_ids)
    return payload


def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parser = argparse.ArgumentParser(description="Batch LLM dream scoring (Parley, chunked).")
    parser.add_argument("--input", default=SCORE_DEFAULT_INPUT)
    parser.add_argument(
        "--output",
        default=os.path.join(DEFAULT_OUTPUT_DIR, f"dream_llm_batch_scores_{timestamp}.csv"),
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--model", default=DEFAULT_PARLEY_MODEL)
    parser.add_argument("--batch-size", type=int, default=10, help="Max 10 nights per API call")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start-row", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    batch_size = min(max(1, args.batch_size), MAX_BATCH_SIZE)
    if args.batch_size > MAX_BATCH_SIZE:
        print(f"Note: batch-size capped at {MAX_BATCH_SIZE}")

    input_path = resolve_input_path(args.input)
    df = pd.read_excel(input_path)[READ_COLUMNS].copy()

    row_ids = list(range(args.start_row, len(df)))
    if args.limit is not None:
        row_ids = row_ids[: args.limit]

    completed = load_completed_row_ids(args.output) if args.resume else set()
    write_header = not (args.resume and os.path.isfile(args.output))

    print(f"Input: {input_path}")
    print(f"Output: {args.output}")
    print(f"Model: {args.model} | batch_size={batch_size} | nights={len(row_ids)}")

    if args.dry_run:
        for chunk_start in range(0, len(row_ids), batch_size):
            chunk_ids = row_ids[chunk_start : chunk_start + batch_size]
            print(f"\n--- batch row_ids={chunk_ids[0]}..{chunk_ids[-1]} ({len(chunk_ids)} nights) ---")
            print(build_batch_user_message(df, chunk_ids)[:2000])
        return

    resolve_parley_api_key()
    check_parley_gateway()
    system_prompt = load_system_prompt(args.prompt)
    client = build_parley_client(args.model, system_prompt)

    stats = {"scored": 0, "errors": 0, "skipped": 0, "api_calls": 0}

    for chunk_start in range(0, len(row_ids), batch_size):
        chunk_ids = [rid for rid in row_ids[chunk_start : chunk_start + batch_size] if rid not in completed]
        if not chunk_ids:
            stats["skipped"] += batch_size
            continue

        scored_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        try:
            payload = score_batch_chunk(client, df, chunk_ids)
            stats["api_calls"] += 1
            for idx, row_id in enumerate(chunk_ids):
                flat = flatten_batch_row(payload, idx)
                out = base_row(df, row_id)
                out.update(flat)
                out.update(
                    {
                        "llm_provider": "parley-batch",
                        "llm_model": args.model,
                        "batch_size": len(chunk_ids),
                        "scored_at_utc": scored_at,
                        "error": "",
                    }
                )
                append_row(args.output, out, OUTPUT_FIELDS, write_header=write_header)
                write_header = False
                stats["scored"] += 1
                print(
                    f"row_id={row_id} awareness={out['awareness_score']} "
                    f"control={out['control_score']} cue={out['cue_incorporation']} "
                    f"bizarre={out['bizarreness_count']}"
                )
        except Exception as exc:  # noqa: BLE001
            stats["api_calls"] += 1
            for row_id in chunk_ids:
                out = base_row(df, row_id)
                out.update(
                    {
                        "awareness_score": "",
                        "control_score": "",
                        "cue_incorporation": "",
                        "bizarreness_count": "",
                        **{f"awareness_score_{i}": "" for i in (1, 2, 3)},
                        **{f"control_score_{i}": "" for i in (1, 2, 3)},
                        **{f"cue_incorporation_{i}": "" for i in (1, 2, 3)},
                        **{f"bizarreness_count_{i}": "" for i in (1, 2, 3)},
                        "llm_provider": "parley-batch",
                        "llm_model": args.model,
                        "batch_size": len(chunk_ids),
                        "scored_at_utc": scored_at,
                        "error": str(exc),
                    }
                )
                append_row(args.output, out, OUTPUT_FIELDS, write_header=write_header)
                write_header = False
                stats["errors"] += 1
            print(f"batch failed row_ids={chunk_ids}: {exc}")

    print("\nDone.", stats)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        if "Parley preflight check failed" in str(exc):
            print(f"\n{exc}\n")
            sys.exit(1)
        raise
