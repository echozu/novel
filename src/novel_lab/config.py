"""配置加载：prompts + 题材套路库。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
PROMPTS_DIR = CONFIG_DIR / "prompts"
GENRES_DIR = CONFIG_DIR / "genres"


@lru_cache(maxsize=64)
def load_prompt(name: str) -> str:
    p = PROMPTS_DIR / f"{name}.md"
    if not p.exists():
        raise FileNotFoundError(f"prompt not found: {p}")
    return p.read_text(encoding="utf-8")


@lru_cache(maxsize=16)
def load_genre(genre_id: str) -> dict[str, Any]:
    p = GENRES_DIR / f"{genre_id}.yaml"
    if not p.exists():
        # fallback to generic
        p = GENRES_DIR / "generic.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def genre_trope_lookup(genre_id: str) -> dict[str, str]:
    """返回 {trope_id: trope_name} 速查表。"""
    cfg = load_genre(genre_id)
    return {t["id"]: t["name"] for t in cfg.get("tropes", [])}


def genre_anti_patterns(genre_id: str) -> list[str]:
    cfg = load_genre(genre_id)
    return list(cfg.get("anti_patterns", []))


def system_base() -> str:
    return load_prompt("system_base")
