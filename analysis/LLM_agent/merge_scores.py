#!/usr/bin/env python3
"""
Merge LLM dream scores back into merged analysis data.

Joins on row_id (position in merged_data.xlsx). Writes:
  - output/merged_data_with_llm_scores.xlsx
  - output/merged_data_with_llm_scores.csv
"""

from __future__ import annotations

import argparse
import glob
import os
from typing import Optional

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))
DEFAULT_INPUT = os.path.join(
    REPO_ROOT, "data_prep", "output", "analysis_data", "merged_data.xlsx"
)
DEFAULT_OUTPUT_DIR = os.path.join(BASE_DIR, "output")

LLM_SCORE_COLUMNS = [
    "awareness_score",
    "control_score",
    "cue_incorporation",
    "bizarreness_count",
    "awareness_score_mean",
    "control_score_mean",
    "cue_incorporation_mean",
    "bizarreness_count_mean",
    "ensemble_providers",
    "llm_rationale",
    "llm_provider",
    "llm_model",
    "scored_at_utc",
]


def latest_scores_csv(output_dir: str) -> Optional[str]:
    paths = sorted(glob.glob(os.path.join(output_dir, "dream_llm_scores_*.csv")))
    return paths[-1] if paths else None


def merge_scores(
    merged_path: str,
    scores_path: str,
    output_xlsx: str,
    output_csv: str,
) -> dict[str, int]:
    merged = pd.read_excel(merged_path)
    scores = pd.read_csv(scores_path)

    if "row_id" not in scores.columns:
        raise ValueError("Scores file must contain row_id.")

    if "error" in ok.columns:
        ok = ok[ok["error"].fillna("") == ""].copy()
    ok = ok.drop_duplicates("row_id", keep="last")

    merged = merged.reset_index(drop=True)
    merged["row_id"] = merged.index

    keep_cols = ["row_id", *LLM_SCORE_COLUMNS]
    keep_cols = [c for c in keep_cols if c in ok.columns]
    enriched = merged.merge(ok[keep_cols], on="row_id", how="left")
    enriched = enriched.drop(columns=["row_id"])

    os.makedirs(os.path.dirname(output_xlsx) or ".", exist_ok=True)
    enriched.to_excel(output_xlsx, index=False)
    enriched.to_csv(output_csv, index=False)

    n_scored = ok["awareness_score"].notna().sum() if "awareness_score" in ok.columns else len(ok)
    return {
        "merged_rows": len(enriched),
        "scored_rows_joined": int(n_scored),
        "score_columns": len([c for c in LLM_SCORE_COLUMNS if c in enriched.columns]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge LLM dream scores into merged data.")
    parser.add_argument("--merged", default=DEFAULT_INPUT)
    parser.add_argument("--scores", default=None, help="LLM scores CSV (default: latest in output/)")
    parser.add_argument(
        "--output-xlsx",
        default=os.path.join(DEFAULT_OUTPUT_DIR, "merged_data_with_llm_scores.xlsx"),
    )
    parser.add_argument(
        "--output-csv",
        default=os.path.join(DEFAULT_OUTPUT_DIR, "merged_data_with_llm_scores.csv"),
    )
    args = parser.parse_args()

    scores_path = args.scores or latest_scores_csv(DEFAULT_OUTPUT_DIR)
    if not scores_path or not os.path.isfile(scores_path):
        raise FileNotFoundError(
            "No scores CSV found. Run score_dreams.py first or pass --scores."
        )

    stats = merge_scores(args.merged, scores_path, args.output_xlsx, args.output_csv)
    print(f"Scores from: {scores_path}")
    print(f"Wrote: {args.output_xlsx}")
    print(f"Wrote: {args.output_csv}")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
