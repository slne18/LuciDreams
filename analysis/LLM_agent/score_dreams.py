#!/usr/bin/env python3
"""
Score raw dream narratives with an LLM.

Reads merged_data.xlsx and scores **one night at a time** (never batches multiple nights
in one prompt). Single-provider mode: one API call per night. Ensemble mode: one call
per provider per night (e.g. OpenAI + Claude + Gemini), then averages scores.

Outputs CSV (+ optional JSONL audit log). Supports resume via --resume.

Example:
  export OPENAI_API_KEY=sk-...
  python3 analysis/LLM_agent/score_dreams.py
  python3 analysis/LLM_agent/score_dreams.py --provider ensemble
  python3 analysis/LLM_agent/score_dreams.py --provider parley-ensemble --limit 1
  python3 analysis/LLM_agent/score_dreams.py --limit 5 --dry-run
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
DEFAULT_PROMPT = os.path.join(BASE_DIR, "prompts", "score_dream.txt")
DEFAULT_PROVIDER = os.getenv("LLM_PROVIDER", "openai")

sys.path.insert(0, BASE_DIR)

from build_dream_text import build_dream_narrative, has_any_dream_text  # noqa: E402
from dream_text_columns import DREAM_TEXT_COLUMNS  # noqa: E402
from ensemble import (  # noqa: E402
    DEFAULT_ENSEMBLE_PROVIDERS,
    build_ensemble_clients,
    ensemble_output_fields,
    parse_provider_list,
    score_dream_ensemble,
    verify_ensemble_api_keys,
)
from llm_client import (  # noqa: E402
    DreamScoringClient,
    default_model_for_provider,
    load_system_prompt,
)
from parley import (  # noqa: E402
    DEFAULT_PARLEY_ENSEMBLE_MODELS,
    DEFAULT_PARLEY_MODEL,
    build_parley_client,
    build_parley_ensemble_clients,
    check_parley_gateway,
    parse_parley_model_map,
    resolve_parley_api_key,
)

READ_COLUMNS = [
    "pid",
    "condition",
    "lucid_state",
    "cue_notice",
    "time_asleep",
    *DREAM_TEXT_COLUMNS,
]

SINGLE_OUTPUT_FIELDS = [
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
    "llm_rationale",
    "llm_provider",
    "llm_model",
    "scored_at_utc",
    "error",
]


def resolve_input_path(explicit: str) -> str:
    env_path = os.getenv("LUCIDREAMS_MERGED_DATA", "")
    for candidate in (explicit, env_path, DEFAULT_INPUT):
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    raise FileNotFoundError(
        f"Could not find merged data. Tried: {explicit}, {env_path}, {DEFAULT_INPUT}"
    )


def load_completed_row_ids(output_csv: str) -> Set[int]:
    if not os.path.isfile(output_csv):
        return set()
    done: Set[int] = set()
    with open(output_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("error"):
                continue
            try:
                done.add(int(row["row_id"]))
            except (KeyError, ValueError):
                continue
    return done


def append_row(
    output_csv: str,
    row: Dict[str, Any],
    fieldnames: List[str],
    write_header: bool,
) -> None:
    os.makedirs(os.path.dirname(output_csv) or ".", exist_ok=True)
    mode = "a" if os.path.isfile(output_csv) and not write_header else "w"
    with open(output_csv, mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if mode == "w":
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in fieldnames})


def append_jsonl(path: str, record: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def base_row(df: pd.DataFrame, row_id: int) -> Dict[str, Any]:
    row = df.iloc[row_id]
    return {
        "row_id": row_id,
        "pid": str(row.get("pid", "")),
        "condition": row.get("condition", ""),
        "lucid_state": row.get("lucid_state", ""),
        "cue_notice": row.get("cue_notice", ""),
        "time_asleep": row.get("time_asleep", ""),
        "has_dream_text": int(has_any_dream_text(row)),
    }


def is_ensemble_provider(provider: str) -> bool:
    return provider in {"ensemble", "parley-ensemble"}


def score_dreams(
    *,
    input_path: str,
    output_csv: str,
    audit_jsonl: Optional[str],
    prompt_path: str,
    provider: str,
    model: str,
    ensemble_providers: List[str],
    parley_models: Dict[str, str],
    output_fields: List[str],
    limit: Optional[int],
    start_row: int,
    resume: bool,
    dry_run: bool,
    api_key: Optional[str],
    base_url: Optional[str],
) -> Dict[str, int]:
    df = pd.read_excel(input_path)
    missing = [c for c in READ_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns in input data: {missing}")
    df = df[READ_COLUMNS].copy()

    completed = load_completed_row_ids(output_csv) if resume else set()
    write_header = not (resume and os.path.isfile(output_csv))

    system_prompt = load_system_prompt(prompt_path)
    client: Optional[DreamScoringClient] = None
    ensemble_clients: Optional[Dict[str, DreamScoringClient]] = None

    if not dry_run:
        if provider == "ensemble":
            verify_ensemble_api_keys(ensemble_providers)
            ensemble_clients = build_ensemble_clients(
                ensemble_providers,
                system_prompt,
                base_url=base_url,
            )
        elif provider == "parley-ensemble":
            resolve_parley_api_key(api_key)
            check_parley_gateway(api_key=api_key, base_url=base_url)
            ensemble_clients = build_parley_ensemble_clients(
                parley_models,
                system_prompt,
                api_key=api_key,
                base_url=base_url,
            )
        elif provider == "parley":
            check_parley_gateway(api_key=api_key, base_url=base_url)
            client = build_parley_client(
                model,
                system_prompt,
                api_key=api_key,
                base_url=base_url,
            )
        else:
            client = DreamScoringClient(
                provider=provider,
                model=model,
                system_prompt=system_prompt,
                api_key=api_key,
                base_url=base_url,
            )

    stats = {
        "input_rows": len(df),
        "scored": 0,
        "skipped_resume": 0,
        "errors": 0,
        "dry_run_printed": 0,
    }

    row_ids = list(range(start_row, len(df)))
    if limit is not None:
        row_ids = row_ids[:limit]

    if provider == "ensemble":
        print(
            f"Scoring mode: ensemble ({', '.join(ensemble_providers)}) — "
            f"{len(ensemble_providers)} API calls per night, {len(row_ids)} nights queued"
        )
    elif provider == "parley-ensemble":
        print(
            f"Scoring mode: parley-ensemble ({', '.join(f'{k}={v}' for k, v in parley_models.items())}) — "
            f"{len(parley_models)} API calls per night, {len(row_ids)} nights queued"
        )
    else:
        print(
            f"Scoring mode: one API call per night ({len(row_ids)} nights queued, "
            f"{len(df)} total in file)"
        )

    for row_id in row_ids:
        if row_id in completed:
            stats["skipped_resume"] += 1
            continue

        row = df.iloc[row_id]
        narrative = build_dream_narrative(row)
        out = base_row(df, row_id)

        if dry_run:
            print(f"\n--- row_id={row_id} pid={out['pid']} ---")
            if is_ensemble_provider(provider):
                if provider == "ensemble":
                    print(f"Would call providers: {', '.join(ensemble_providers)}")
                else:
                    print(
                        "Would call Parley models: "
                        + ", ".join(f"{k}={v}" for k, v in parley_models.items())
                    )
            print(narrative[:2000] if narrative else "[empty narrative]")
            stats["dry_run_printed"] += 1
            if stats["dry_run_printed"] >= (limit or 1):
                break
            continue

        scored_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        try:
            if is_ensemble_provider(provider):
                assert ensemble_clients is not None
                ensemble_label = "parley-ensemble" if provider == "parley-ensemble" else "ensemble"
                result = score_dream_ensemble(
                    ensemble_clients,
                    narrative,
                    row_id=row_id,
                    pid=str(out["pid"]),
                    condition=out.get("condition"),
                    ensemble_label=ensemble_label,
                )
                provider_results = result.pop("provider_results", {})
                out.update(
                    {
                        k: v
                        for k, v in result.items()
                        if k not in {"provider_results", "ensemble_partial_errors"}
                    }
                )
                out["scored_at_utc"] = scored_at
                out["error"] = result.get("ensemble_partial_errors", "")
                audit_payload = {
                    **out,
                    "narrative": narrative,
                    "provider_results": provider_results,
                }
            else:
                assert client is not None
                result = client.score_dream(
                    narrative,
                    row_id=row_id,
                    pid=str(out["pid"]),
                    condition=out.get("condition"),
                )
                out.update(
                    {
                        "awareness_score": result["awareness_score"],
                        "control_score": result["control_score"],
                        "cue_incorporation": result["cue_incorporation"],
                        "bizarreness_count": result["bizarreness_count"],
                        "llm_rationale": result["rationale"],
                        "llm_provider": result.get("provider", provider),
                        "llm_model": result.get("model", model),
                        "scored_at_utc": scored_at,
                        "error": "",
                    }
                )
                audit_payload = {
                    **out,
                    "narrative": narrative,
                    "prompt_tokens": result.get("prompt_tokens"),
                    "completion_tokens": result.get("completion_tokens"),
                }

            stats["scored"] += 1
            if audit_jsonl:
                append_jsonl(audit_jsonl, audit_payload)
        except Exception as exc:  # noqa: BLE001 - log per-row failures
            out.update(
                {
                    "awareness_score": "",
                    "control_score": "",
                    "cue_incorporation": "",
                    "bizarreness_count": "",
                    "llm_rationale": "",
                    "llm_provider": provider,
                    "llm_model": model if not is_ensemble_provider(provider) else "",
                    "scored_at_utc": scored_at,
                    "error": str(exc),
                }
            )
            stats["errors"] += 1

        append_row(output_csv, out, output_fields, write_header=write_header)
        write_header = False
        ensemble_members = (
            list(parley_models.keys())
            if provider == "parley-ensemble"
            else ensemble_providers
            if provider == "ensemble"
            else []
        )
        calls = len(ensemble_members) if is_ensemble_provider(provider) else 1
        print(
            f"night {row_id + 1}/{len(df)} ({calls} API call(s)) pid={out['pid']} "
            f"awareness={out.get('awareness_score')} control={out.get('control_score')} "
            f"cue_inc={out.get('cue_incorporation')} bizarre={out.get('bizarreness_count')}"
        )

    return stats


def main() -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parser = argparse.ArgumentParser(description="LLM scoring pipeline for dream narratives.")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Path to merged_data.xlsx")
    parser.add_argument(
        "--output",
        default=os.path.join(DEFAULT_OUTPUT_DIR, f"dream_llm_scores_{timestamp}.csv"),
        help="Output CSV path",
    )
    parser.add_argument(
        "--audit-jsonl",
        default=os.path.join(DEFAULT_OUTPUT_DIR, f"dream_llm_scores_{timestamp}.jsonl"),
        help="Optional JSONL audit log (set empty string to disable)",
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="System prompt file")
    parser.add_argument(
        "--provider",
        choices=["openai", "anthropic", "gemini", "ensemble", "parley", "parley-ensemble"],
        default=DEFAULT_PROVIDER,
        help=(
            "LLM provider, 'ensemble' (3 separate API keys), or Parley gateway "
            "('parley' / 'parley-ensemble' with one PARLEY_API_KEY)"
        ),
    )
    parser.add_argument(
        "--ensemble-providers",
        default=",".join(DEFAULT_ENSEMBLE_PROVIDERS),
        help="Comma-separated providers for --provider ensemble (default: openai,anthropic,gemini)",
    )
    parser.add_argument(
        "--parley-models",
        default=None,
        help=(
            "Parley ensemble model map, e.g. "
            "gpt:GPT-5.4 Mini,claude:Claude Sonnet 4.6,gemini:Gemini 3.1 Pro "
            "(default: env PARLEY_MODEL_* or built-in defaults)"
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name for single-provider mode (default: provider-specific or Parley default)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max rows to score")
    parser.add_argument("--start-row", type=int, default=0, help="Start row index (0-based)")
    parser.add_argument("--resume", action="store_true", help="Skip rows already in output CSV")
    parser.add_argument("--dry-run", action="store_true", help="Print narratives only; no API calls")
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key override (single-provider or Parley modes)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="OpenAI-compatible base URL (openai / Parley gateway override)",
    )
    args = parser.parse_args()

    ensemble_providers = parse_provider_list(args.ensemble_providers)
    if args.parley_models:
        parley_models = parse_parley_model_map(args.parley_models)
    else:
        parley_models = dict(DEFAULT_PARLEY_ENSEMBLE_MODELS)

    if args.provider == "ensemble":
        output_fields = ensemble_output_fields(ensemble_providers)
        model = ""
    elif args.provider == "parley-ensemble":
        output_fields = ensemble_output_fields(list(parley_models.keys()))
        model = ""
    elif args.provider == "parley":
        output_fields = SINGLE_OUTPUT_FIELDS
        model = args.model or DEFAULT_PARLEY_MODEL
    else:
        output_fields = SINGLE_OUTPUT_FIELDS
        model = args.model or default_model_for_provider(args.provider)

    input_path = resolve_input_path(args.input)
    audit_jsonl = args.audit_jsonl if args.audit_jsonl else None

    print(f"Input: {input_path}")
    print(f"Output: {args.output}")
    if audit_jsonl:
        print(f"Audit log: {audit_jsonl}")
    print(f"Provider: {args.provider}")
    if args.provider == "ensemble":
        print(f"Ensemble providers: {', '.join(ensemble_providers)}")
    elif args.provider == "parley-ensemble":
        print(
            "Parley ensemble models: "
            + ", ".join(f"{label}={name}" for label, name in parley_models.items())
        )
    else:
        print(f"Model: {model}")

    stats = score_dreams(
        input_path=input_path,
        output_csv=args.output,
        audit_jsonl=audit_jsonl,
        prompt_path=args.prompt,
        provider=args.provider,
        model=model,
        ensemble_providers=ensemble_providers,
        parley_models=parley_models,
        output_fields=output_fields,
        limit=args.limit,
        start_row=args.start_row,
        resume=args.resume,
        dry_run=args.dry_run,
        api_key=args.api_key,
        base_url=args.base_url,
    )

    print("\nDone.")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        if "Parley preflight check failed" in str(exc):
            print(f"\n{exc}\n")
            sys.exit(1)
        raise
