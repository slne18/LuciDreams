"""Score each dream with multiple LLM providers and aggregate results."""

from __future__ import annotations

import json
import os
from statistics import mean
from typing import Any, Dict, List, Mapping, Optional, Sequence

from llm_client import (
    PROVIDER_DEFAULT_MODELS,
    DreamScoringClient,
    resolve_api_key,
)

DEFAULT_ENSEMBLE_PROVIDERS = ("openai", "anthropic", "gemini")

SCORE_METRICS = (
    "awareness_score",
    "control_score",
    "cue_incorporation",
    "bizarreness_count",
)


def summarize_ensemble_errors(errors: List[str]) -> str:
    """Collapse identical per-model errors into one readable message."""
    if not errors:
        return "All ensemble providers failed (no details)."

    parsed: List[tuple[str, str]] = []
    for entry in errors:
        if ": " in entry:
            label, message = entry.split(": ", 1)
            parsed.append((label, message))
        else:
            parsed.append(("unknown", entry))

    unique_messages = list(dict.fromkeys(message for _, message in parsed))
    if len(unique_messages) == 1:
        labels = ", ".join(label for label, _ in parsed)
        return f"All {len(parsed)} models failed ({labels}): {unique_messages[0]}"

    return "All ensemble providers failed: " + "; ".join(errors)


def parse_provider_list(value: str) -> List[str]:
    providers = [part.strip().lower() for part in value.split(",") if part.strip()]
    if not providers:
        raise ValueError("Ensemble provider list is empty.")
    unknown = [p for p in providers if p not in PROVIDER_DEFAULT_MODELS]
    if unknown:
        raise ValueError(f"Unknown ensemble providers: {unknown}")
    return providers


def model_for_ensemble_provider(provider: str) -> str:
    env_key = f"LLM_DREAM_MODEL_{provider.upper()}"
    return os.getenv(env_key, PROVIDER_DEFAULT_MODELS[provider])


def verify_ensemble_api_keys(providers: Sequence[str]) -> None:
    missing: List[str] = []
    for provider in providers:
        try:
            resolve_api_key(provider)
        except RuntimeError:
            missing.append(provider)
    if missing:
        raise RuntimeError(
            "Missing API keys for ensemble providers: "
            + ", ".join(missing)
            + ". Set OPENAI_API_KEY, ANTHROPIC_API_KEY, and GOOGLE_API_KEY/GEMINI_API_KEY."
        )


def build_ensemble_clients(
    providers: Sequence[str],
    system_prompt: str,
    *,
    base_url: Optional[str] = None,
) -> Dict[str, DreamScoringClient]:
    clients: Dict[str, DreamScoringClient] = {}
    for provider in providers:
        clients[provider] = DreamScoringClient(
            provider=provider,
            model=model_for_ensemble_provider(provider),
            system_prompt=system_prompt,
            base_url=base_url if provider == "openai" else None,
        )
    return clients


def aggregate_provider_scores(
    provider_results: Mapping[str, Mapping[str, Any]],
    *,
    ensemble_label: str = "ensemble",
) -> Dict[str, Any]:
    awareness_vals = [int(r["awareness_score"]) for r in provider_results.values()]
    control_vals = [int(r["control_score"]) for r in provider_results.values()]
    cue_vals = [int(r["cue_incorporation"]) for r in provider_results.values()]
    bizarre_vals = [int(r["bizarreness_count"]) for r in provider_results.values()]

    awareness_mean = mean(awareness_vals)
    control_mean = mean(control_vals)
    cue_mean = mean(cue_vals)
    bizarre_mean = mean(bizarre_vals)

    out: Dict[str, Any] = {
        "awareness_score": round(awareness_mean, 2),
        "control_score": round(control_mean, 2),
        "awareness_score_mean": round(awareness_mean, 4),
        "control_score_mean": round(control_mean, 4),
        "cue_incorporation_mean": round(cue_mean, 4),
        "bizarreness_count_mean": round(bizarre_mean, 4),
        "cue_incorporation": int(round(cue_mean)),
        "bizarreness_count": int(round(bizarre_mean)),
        "llm_provider": ensemble_label,
        "llm_model": "|".join(
            f"{provider}:{provider_results[provider].get('model', '')}"
            for provider in sorted(provider_results)
        ),
        "ensemble_providers": ",".join(sorted(provider_results)),
        "llm_rationale": " | ".join(
            f"{provider}: {provider_results[provider].get('rationale', '')[:120]}"
            for provider in sorted(provider_results)
        )[:400],
    }

    for provider, result in provider_results.items():
        for metric in SCORE_METRICS:
            out[f"{metric}_{provider}"] = result[metric]

    return out


def score_dream_ensemble(
    clients: Mapping[str, DreamScoringClient],
    narrative: str,
    *,
    row_id: Optional[int],
    pid: Optional[str],
    condition: Optional[object],
    ensemble_label: str = "ensemble",
) -> Dict[str, Any]:
    provider_results: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []

    for provider, client in clients.items():
        try:
            provider_results[provider] = client.score_dream(
                narrative,
                row_id=row_id,
                pid=pid,
                condition=condition,
            )
        except Exception as exc:  # noqa: BLE001 - collect per-provider failures
            errors.append(f"{provider}: {exc}")

    if not provider_results:
        raise RuntimeError(summarize_ensemble_errors(errors))

    aggregated = aggregate_provider_scores(provider_results, ensemble_label=ensemble_label)
    aggregated["provider_results"] = provider_results
    if errors:
        aggregated["ensemble_partial_errors"] = "; ".join(errors)
    return aggregated


def ensemble_output_fields(providers: Sequence[str]) -> List[str]:
    base = [
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
        "awareness_score_mean",
        "control_score_mean",
        "cue_incorporation_mean",
        "bizarreness_count_mean",
        "ensemble_providers",
        "llm_rationale",
        "llm_provider",
        "llm_model",
        "scored_at_utc",
        "error",
        "ensemble_partial_errors",
    ]
    per_provider = [f"{metric}_{provider}" for provider in providers for metric in SCORE_METRICS]
    return base + per_provider
