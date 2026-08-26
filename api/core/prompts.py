"""Load prompt templates from api/core/prompts.yml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from api.core.constants import PROMPTS_FILE


@lru_cache(maxsize=1)
def load_prompts() -> dict[str, Any]:
    with PROMPTS_FILE.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid prompts file: {PROMPTS_FILE}")
    return data


def get_prompt(*keys: str) -> str:
    """Return a prompt string by nested keys, e.g. get_prompt('query_analysis', 'system')."""
    node: Any = load_prompts()
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            raise KeyError(f"Prompt not found: {'.'.join(keys)}")
        node = node[key]
    if not isinstance(node, str):
        raise TypeError(f"Prompt at {'.'.join(keys)} is not a string")
    return node
