"""LLM clients for dream scoring (OpenAI, Anthropic Claude, Google Gemini)."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, Literal, Optional

from jsonschema import Draft7Validator

Provider = Literal["openai", "anthropic", "gemini"]

PROVIDER_DEFAULT_MODELS: Dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-latest",
    "gemini": "gemini-2.0-flash",
}

SCORE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": [
        "awareness_score",
        "control_score",
        "cue_incorporation",
        "bizarreness_count",
        "rationale",
    ],
    "properties": {
        "awareness_score": {"type": "integer", "minimum": 1, "maximum": 5},
        "control_score": {"type": "integer", "minimum": 1, "maximum": 5},
        "cue_incorporation": {"type": "integer", "enum": [0, 1]},
        "bizarreness_count": {"type": "integer", "minimum": 0},
        "rationale": {"type": "string", "maxLength": 400},
    },
    "additionalProperties": False,
}

_validator = Draft7Validator(SCORE_SCHEMA)


def default_model_for_provider(provider: str) -> str:
    key = provider.lower().strip()
    if key not in PROVIDER_DEFAULT_MODELS:
        raise ValueError(
            f"Unknown provider {provider!r}. Choose: {', '.join(PROVIDER_DEFAULT_MODELS)}"
        )
    return os.getenv("LLM_DREAM_MODEL", PROVIDER_DEFAULT_MODELS[key])


def resolve_api_key(provider: str, explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    env_by_provider = {
        "openai": ("OPENAI_API_KEY",),
        "anthropic": ("ANTHROPIC_API_KEY",),
        "gemini": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    }
    for env_name in env_by_provider.get(provider, ()):
        value = os.getenv(env_name, "")
        if value:
            return value
    raise RuntimeError(
        f"Missing API key for provider {provider!r}. "
        f"Set one of: {', '.join(env_by_provider.get(provider, ()))} or pass --api-key."
    )


def load_system_prompt(prompt_path: str) -> str:
    with open(prompt_path, encoding="utf-8") as f:
        return f.read().strip()


def extract_json_object(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("LLM response was not a JSON object.")
    return parsed


def validate_scores(payload: Dict[str, Any]) -> Dict[str, Any]:
    errors = sorted(_validator.iter_errors(payload), key=lambda e: e.path)
    if errors:
        msg = "; ".join(f"{list(e.path)}: {e.message}" for e in errors)
        raise ValueError(f"Invalid LLM JSON: {msg}")
    return payload


class DreamScoringClient:
    def __init__(
        self,
        *,
        provider: str = "openai",
        model: str,
        system_prompt: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        max_retries: int = 3,
        result_label: Optional[str] = None,
    ) -> None:
        self.provider = provider.lower().strip()
        if self.provider not in PROVIDER_DEFAULT_MODELS:
            raise ValueError(
                f"Unknown provider {provider!r}. Choose: {', '.join(PROVIDER_DEFAULT_MODELS)}"
            )
        self.model = model
        self.system_prompt = system_prompt
        self.max_retries = max_retries
        self.result_label = result_label or self.provider
        self.api_key = api_key if api_key else resolve_api_key(self.provider)
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")
        self._client: Any = None
        self._init_client()

    def _init_client(self) -> None:
        if self.provider == "openai":
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError("pip install openai") from exc
            kwargs: Dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
            return

        if self.provider == "anthropic":
            try:
                import anthropic
            except ImportError as exc:
                raise RuntimeError("pip install anthropic") from exc
            self._client = anthropic.Anthropic(api_key=self.api_key)
            return

        if self.provider == "gemini":
            try:
                from google import genai
            except ImportError as exc:
                raise RuntimeError("pip install google-genai") from exc
            self._client = genai.Client(api_key=self.api_key)
            return

        raise ValueError(f"Unsupported provider: {self.provider}")

    def _build_user_message(
        self,
        narrative: str,
        *,
        row_id: Optional[int],
        pid: Optional[str],
        condition: Optional[object],
    ) -> str:
        header_lines = ["Score ONLY this single night's dream report (no other nights)."]
        if row_id is not None:
            header_lines.append(f"row_id: {row_id}")
        if pid:
            header_lines.append(f"pid: {pid}")
        if condition is not None and str(condition).strip():
            header_lines.append(f"condition: {condition}")
        return (
            "\n".join(header_lines)
            + "\n\n"
            + (narrative if narrative.strip() else "[No dream text provided — use minimum scores.]")
        )

    def _call_openai(self, user_message: str) -> tuple[str, Optional[int], Optional[int]]:
        response = self._client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        content = response.choices[0].message.content or ""
        usage = response.usage
        return content, getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)

    def _call_anthropic(self, user_message: str) -> tuple[str, Optional[int], Optional[int]]:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=1024,
            temperature=0,
            system=self.system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        parts = []
        for block in response.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        usage = response.usage
        return (
            "".join(parts),
            getattr(usage, "input_tokens", None),
            getattr(usage, "output_tokens", None),
        )

    def _call_gemini(self, user_message: str) -> tuple[str, Optional[int], Optional[int]]:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self.model,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                temperature=0,
                response_mime_type="application/json",
            ),
        )
        usage = getattr(response, "usage_metadata", None)
        return (
            response.text or "",
            getattr(usage, "prompt_token_count", None) if usage else None,
            getattr(usage, "candidates_token_count", None) if usage else None,
        )

    def _complete(self, user_message: str) -> tuple[str, Optional[int], Optional[int]]:
        if self.provider == "openai":
            return self._call_openai(user_message)
        if self.provider == "anthropic":
            return self._call_anthropic(user_message)
        if self.provider == "gemini":
            return self._call_gemini(user_message)
        raise ValueError(f"Unsupported provider: {self.provider}")

    def score_dream(
        self,
        narrative: str,
        *,
        row_id: Optional[int] = None,
        pid: Optional[str] = None,
        condition: Optional[object] = None,
    ) -> Dict[str, Any]:
        user_message = self._build_user_message(
            narrative,
            row_id=row_id,
            pid=pid,
            condition=condition,
        )

        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                content, prompt_tokens, completion_tokens = self._complete(user_message)
                payload = validate_scores(extract_json_object(content))
                payload["provider"] = self.result_label
                payload["model"] = self.model
                payload["prompt_tokens"] = prompt_tokens
                payload["completion_tokens"] = completion_tokens
                return payload
            except Exception as exc:  # noqa: BLE001 - retry loop
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(1.5 * attempt)
                continue

        detail = self._format_failure(last_error)
        raise RuntimeError(
            f"LLM scoring failed ({self.result_label}, model={self.model!r}) "
            f"after {self.max_retries} attempts: {detail}"
        )

    def _format_failure(self, exc: Optional[Exception]) -> str:
        if exc is None:
            return "unknown error"
        if self.base_url and "keys.theparley.org" in self.base_url:
            from parley import format_parley_error

            return format_parley_error(
                exc,
                model=self.model,
                label=self.result_label,
            )
        return str(exc)
