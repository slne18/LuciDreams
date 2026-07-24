"""Validate compact batch scoring JSON from score_dream_batch.txt."""

from __future__ import annotations

from typing import Any, Dict, List

from jsonschema import Draft7Validator

BATCH_SCORE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["n", "row_id", "awareness", "control", "cue", "bizarre"],
    "properties": {
        "n": {"type": "integer", "minimum": 1, "maximum": 10},
        "row_id": {
            "type": "array",
            "items": {"type": "integer"},
            "minItems": 1,
            "maxItems": 10,
        },
        "awareness": {"type": "array", "items": {"$ref": "#/definitions/triple_1_5"}},
        "control": {"type": "array", "items": {"$ref": "#/definitions/triple_1_5"}},
        "cue": {"type": "array", "items": {"$ref": "#/definitions/triple_01"}},
        "bizarre": {"type": "array", "items": {"$ref": "#/definitions/triple_nonneg"}},
        "error": {"type": "string"},
    },
    "definitions": {
        "triple_1_5": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {"type": "integer", "minimum": 1, "maximum": 5},
        },
        "triple_01": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {"type": "integer", "enum": [0, 1]},
        },
        "triple_nonneg": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {"type": "integer", "minimum": 0},
        },
    },
    "additionalProperties": False,
}

_batch_validator = Draft7Validator(BATCH_SCORE_SCHEMA)

METRIC_KEYS = ("awareness", "control", "cue", "bizarre")
OUTPUT_PREFIX = {
    "awareness": "awareness_score",
    "control": "control_score",
    "cue": "cue_incorporation",
    "bizarre": "bizarreness_count",
}


def validate_batch_scores(payload: Dict[str, Any], *, expected_row_ids: List[int]) -> Dict[str, Any]:
    if payload.get("error"):
        raise ValueError(f"LLM batch error: {payload['error']}")

    errors = sorted(_batch_validator.iter_errors(payload), key=lambda e: e.path)
    if errors:
        msg = "; ".join(f"{list(e.path)}: {e.message}" for e in errors)
        raise ValueError(f"Invalid batch JSON: {msg}")

    n = payload["n"]
    row_ids = payload["row_id"]
    if n != len(row_ids):
        raise ValueError(f"Batch n={n} but len(row_id)={len(row_ids)}")
    if row_ids != expected_row_ids:
        raise ValueError(f"row_id mismatch: expected {expected_row_ids}, got {row_ids}")

    for metric in METRIC_KEYS:
        rows = payload[metric]
        if len(rows) != n:
            raise ValueError(f"len({metric})={len(rows)} but n={n}")

    return payload


def flatten_batch_row(payload: Dict[str, Any], index: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {"row_id": payload["row_id"][index]}
    for metric, prefix in OUTPUT_PREFIX.items():
        triple = payload[metric][index]
        for pass_idx, value in enumerate(triple, start=1):
            out[f"{prefix}_{pass_idx}"] = value
        if metric in ("awareness", "control"):
            out[prefix] = round(sum(triple) / 3, 2)
        elif metric == "cue":
            out[prefix] = int(round(sum(triple) / 3))
        else:
            out[prefix] = int(round(sum(triple) / 3))
    return out
