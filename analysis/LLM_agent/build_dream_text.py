"""Build a single narrative block from the five dream text columns."""

from __future__ import annotations

from typing import Mapping

from dream_text_columns import DREAM_TEXT_COLUMNS, DREAM_TEXT_LABELS


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "n/a"}:
        return ""
    return text


def build_dream_narrative(row: Mapping[str, object]) -> str:
    sections: list[str] = []
    for column in DREAM_TEXT_COLUMNS:
        text = normalize_text(row.get(column, ""))
        if not text:
            continue
        label = DREAM_TEXT_LABELS[column]
        sections.append(f"### {label}\n{text}")

    if not sections:
        return ""
    return "\n\n".join(sections)


def has_any_dream_text(row: Mapping[str, object]) -> bool:
    return bool(build_dream_narrative(row))
