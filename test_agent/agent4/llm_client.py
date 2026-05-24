from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from openai import AsyncOpenAI


ROOT_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT_DIR / ".env"


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str
    model_name: str


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _require_keys(values: dict[str, str], keys: Iterable[str]) -> None:
    missing = [key for key in keys if not values.get(key)]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"Missing required .env values: {joined}")


def load_llm_config(*, allow_fake: bool = False) -> LLMConfig:
    env_values = _parse_env_file(ENV_PATH)
    config_values = {
        "API_KEY": (
            os.getenv("API_KEY")
            or env_values.get("API_KEY", "")
            or os.getenv("OPENROUTER_API_KEY")
            or env_values.get("OPENROUTER_API_KEY", "")
        ),
        "BASE_URL": (
            os.getenv("BASE_URL")
            or env_values.get("BASE_URL", "")
            or os.getenv("OPENROUTER_BASE_URL")
            or env_values.get("OPENROUTER_BASE_URL", "")
        ),
        "MODEL_NAME": os.getenv("MODEL_NAME") or env_values.get("MODEL_NAME", ""),
    }

    if allow_fake:
        config_values = {
            "API_KEY": config_values["API_KEY"] or "agent4-fake-key",
            "BASE_URL": config_values["BASE_URL"] or "http://example.invalid/v1",
            "MODEL_NAME": config_values["MODEL_NAME"] or "agent4-fake-model",
        }

    _require_keys(config_values, config_values.keys())
    return LLMConfig(
        api_key=config_values["API_KEY"],
        base_url=config_values["BASE_URL"],
        model_name=config_values["MODEL_NAME"],
    )


def create_llm_client(config: LLMConfig | None = None, *, allow_fake: bool = False) -> AsyncOpenAI:
    llm_config = config or load_llm_config(allow_fake=allow_fake)
    return AsyncOpenAI(api_key=llm_config.api_key, base_url=llm_config.base_url)

