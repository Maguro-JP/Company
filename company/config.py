"""company.yaml と agents/*.md を読み込む。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "company.yaml"
AGENTS_DIR = ROOT / "agents"
DATA_DIR = ROOT / "data"
DECISIONS_DIR = DATA_DIR / "decisions"
METRICS_PATH = DATA_DIR / "metrics.csv"
RUNS_PATH = DATA_DIR / "runs.csv"   # 役職ごとの実行の実績。runner が追記し、context が読む

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


@dataclass(frozen=True)
class Agent:
    role: str
    title: str
    model_tier: str
    instructions: str

    @property
    def path(self) -> Path:
        return AGENTS_DIR / f"{self.role}.md"


class Config:
    def __init__(self, raw: dict[str, Any]):
        self.raw = raw

    @property
    def company(self) -> dict[str, Any]:
        return self.raw.get("company", {})

    @property
    def autonomy(self) -> dict[str, Any]:
        return self.raw.get("autonomy", {})

    @property
    def blocked_on_owner(self) -> list[str]:
        """AIに実行手段が無い作業。禁止事項ではない。"""
        return self.raw.get("blocked_on_owner", [])

    @property
    def kpi(self) -> dict[str, Any]:
        return self.raw.get("kpi", {})

    def roles_for(self, cadence: str) -> list[str]:
        return self.raw.get("cadence", {}).get(cadence, [])

    def model_for(self, tier: str) -> str:
        models = self.raw.get("model", {})
        return models.get(tier) or models.get("default", "claude-sonnet-5")

    def unset_fields(self) -> list[str]:
        """まだ TODO のままの必須項目を返す。ここが空になるまで会社は動かない。"""
        missing = []
        for key, value in self.company.items():
            if isinstance(value, str) and value.strip().startswith("TODO"):
                missing.append(f"company.{key}")
        north_star = self.kpi.get("north_star", "")
        if isinstance(north_star, str) and north_star.strip().startswith("TODO"):
            missing.append("kpi.north_star")
        return missing


def load_config(path: Path = CONFIG_PATH) -> Config:
    with path.open(encoding="utf-8") as fh:
        return Config(yaml.safe_load(fh) or {})


def load_agent(role: str) -> Agent:
    path = AGENTS_DIR / f"{role}.md"
    if not path.exists():
        raise FileNotFoundError(f"役職の定義がありません: {path}")
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER.match(text)
    if not match:
        raise ValueError(f"frontmatter がありません: {path}")
    meta = yaml.safe_load(match.group(1)) or {}
    return Agent(
        role=meta.get("role", role),
        title=meta.get("title", role),
        model_tier=meta.get("model", "default"),
        instructions=text[match.end():].strip(),
    )


def load_agents(roles: list[str]) -> list[Agent]:
    return [load_agent(role) for role in roles]
