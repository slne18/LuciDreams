"""Parley gateway configuration (one API key, many models via OpenAI-compatible API)."""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Sequence

from llm_client import DreamScoringClient

PARLEY_BASE_URL = os.getenv("PARLEY_BASE_URL", "https://keys.theparley.org/v1")
PARLEY_GATEWAY_HOST = "keys.theparley.org"


def is_parley_base_url(base_url: Optional[str]) -> bool:
    return bool(base_url and PARLEY_GATEWAY_HOST in base_url)


def format_parley_error(
    exc: Exception,
    *,
    model: Optional[str] = None,
    label: Optional[str] = None,
) -> str:
    """Turn Parley/API exceptions into short, actionable messages."""
    raw = str(exc)
    status_code = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    body_text = body if isinstance(body, str) else str(body or "")
    combined = f"{raw}\n{body_text}"

    prefix = ""
    if label and model:
        prefix = f"[{label} / {model}] "
    elif model:
        prefix = f"[{model}] "

    if "DEPLOYMENT_PAUSED" in combined:
        return (
            prefix
            + "Parley gateway is paused (DEPLOYMENT_PAUSED). "
            "This is a temporary KSU server outage — not your API key, model names, or input data. "
            "Wait and retry later, or check https://keys.theparley.org ."
        )

    if status_code == 503 or re.search(r"\b503\b", raw):
        return (
            prefix
            + "Parley gateway unavailable (HTTP 503 — deployment paused or overloaded). "
            "This is a temporary KSU server issue, not your API key, model names, or input data. "
            "Wait and retry later, or check https://keys.theparley.org ."
        )

    if status_code == 401 or re.search(r"\b401\b", raw):
        return (
            prefix
            + "Parley rejected the API key (HTTP 401). "
            "Check PARLEY_API_KEY in analysis/LLM_agent/.env ."
        )

    if status_code == 404 or re.search(r"\b404\b", raw):
        return (
            prefix
            + f"Model not found on Parley (HTTP 404). "
            f"Use the exact name from Parley's model picker{(' for ' + model) if model else ''}."
        )

    return prefix + raw


def check_parley_gateway(*, api_key: Optional[str] = None, base_url: Optional[str] = None) -> None:
    """Fail fast with a clear message if Parley is down before scoring nights."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("pip install openai") from exc

    key = resolve_parley_api_key(api_key)
    url = base_url or PARLEY_BASE_URL
    client = OpenAI(api_key=key, base_url=url)
    try:
        client.models.list()
    except Exception as exc:  # noqa: BLE001 - surface gateway status
        detail = format_parley_error(exc)
        raise RuntimeError(
            "Parley preflight check failed — no nights were scored.\n\n"
            f"{detail}\n\n"
            f"Gateway: {url}\n"
            "Nothing is wrong with merged_data.xlsx or your prompt. "
            "If this keeps happening, contact KSU Parley support."
        ) from exc

    print(f"Parley gateway OK ({url})")


# Default model names as shown in Parley's model picker (override via env if needed).
DEFAULT_PARLEY_MODEL = os.getenv("PARLEY_MODEL", "GPT-5.4 Mini")

DEFAULT_PARLEY_ENSEMBLE_MODELS: Dict[str, str] = {
    "gpt": os.getenv("PARLEY_MODEL_GPT", "GPT-5.4 Mini"),
    "claude": os.getenv("PARLEY_MODEL_CLAUDE", "Claude Sonnet 4.6"),
    "gemini": os.getenv("PARLEY_MODEL_GEMINI", "Gemini 3.1 Pro"),
    "llama": os.getenv("PARLEY_MODEL_LLAMA", "Llama 4 Maverick 17B"),
}

PARLEY_MODEL_CATALOG = (
    "Claude Haiku 4.5",
    "Claude Sonnet 4.6",
    "Claude Opus 4.8",
    "Llama 4 Maverick 17B",
    "GPT-5.5",
    "GPT-5.3 Codex",
    "GPT-5.5 (Thinking)",
    "GPT-5.4 Pro",
    "GPT-5.5 Pro",
    "GPT-5.4 Mini",
    "GPT-5.4 Nano",
    "Gemini 3.1 Pro",
    "GPT Image 2",
)


def resolve_parley_api_key(explicit: Optional[str] = None) -> str:
    for candidate in (explicit, os.getenv("PARLEY_API_KEY"), os.getenv("OPENAI_API_KEY")):
        if candidate:
            return candidate
    raise RuntimeError(
        "Missing Parley API key. Set PARLEY_API_KEY or OPENAI_API_KEY, or pass --api-key."
    )


def parse_parley_model_map(value: str) -> Dict[str, str]:
    """Parse gpt:GPT-5.4 Mini,claude:Claude Sonnet 4.6,gemini:Gemini 3.1 Pro,llama:Llama 4 Maverick 17B"""
    out: Dict[str, str] = {}
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(
                f"Invalid --parley-models entry {part!r}. Use label:Model Name,label:Model Name"
            )
        label, model = part.split(":", 1)
        label = label.strip().lower()
        model = model.strip()
        if not label or not model:
            raise ValueError(f"Invalid --parley-models entry {part!r}.")
        out[label] = model
    if not out:
        raise ValueError("--parley-models is empty.")
    return out


def build_parley_client(
    model: str,
    system_prompt: str,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    result_label: str = "parley",
) -> DreamScoringClient:
    return DreamScoringClient(
        provider="openai",
        model=model,
        system_prompt=system_prompt,
        api_key=resolve_parley_api_key(api_key),
        base_url=base_url or PARLEY_BASE_URL,
        result_label=result_label,
    )


def build_parley_ensemble_clients(
    model_map: Dict[str, str],
    system_prompt: str,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, DreamScoringClient]:
    key = resolve_parley_api_key(api_key)
    url = base_url or PARLEY_BASE_URL
    return {
        label: DreamScoringClient(
            provider="openai",
            model=model_name,
            system_prompt=system_prompt,
            api_key=key,
            base_url=url,
            result_label=label,
        )
        for label, model_name in model_map.items()
    }


def parley_ensemble_labels(model_map: Dict[str, str]) -> List[str]:
    return list(model_map.keys())
