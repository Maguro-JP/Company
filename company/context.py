"""各エージェントに渡す共有コンテキストを組み立てる。

エージェントは「会社の現状」を推測してはいけない。ここで渡した事実だけを使う。
"""

from __future__ import annotations

import csv
from pathlib import Path

from . import config as config_module
from .config import Config, DECISIONS_DIR, METRICS_PATH

RECENT_DECISIONS = 3
RECENT_METRIC_ROWS = 8


def _metrics_block(path: Path = METRICS_PATH) -> str:
    if not path.exists():
        return "（data/metrics.csv が存在しません。数値は「データなし」として扱うこと）"
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    rows = [r for r in rows if r]
    if len(rows) <= 1:
        return "（data/metrics.csv にヘッダ以外の行がありません。数値は「データなし」として扱うこと）"
    header, *body = rows
    recent = body[-RECENT_METRIC_ROWS:]
    lines = [",".join(header)] + [",".join(r) for r in recent]
    return "\n".join(lines)


def _decisions_block(directory: Path = DECISIONS_DIR) -> str:
    if not directory.exists():
        return "（過去の意思決定記録はまだありません）"
    files = sorted(directory.glob("*.md"))[-RECENT_DECISIONS:]
    if not files:
        return "（過去の意思決定記録はまだありません）"
    return "\n\n".join(
        f"--- {f.name} ---\n{f.read_text(encoding='utf-8').strip()}" for f in files
    )


RECENT_RUNS = 8


def _runs_block() -> str:
    path = config_module.RUNS_PATH
    if not path.exists():
        return "（まだ実行の実績がありません）"
    with path.open(encoding="utf-8", newline="") as fh:
        rows = [r for r in csv.DictReader(fh)]
    if not rows:
        return "（まだ実行の実績がありません）"
    lines = ["at,role,provider,model,turns,tool_calls,input_tokens,output_tokens,reason"]
    for r in rows[-RECENT_RUNS:]:
        lines.append(",".join(str(r.get(k, "")) for k in
                              ("at", "role", "provider", "model", "turns", "tool_calls", "input_tokens", "output_tokens", "reason")))
    return "\n".join(lines)


def build(config: Config, cadence: str) -> str:
    company = config.company
    autonomy = config.autonomy
    lines = [
        "# 会社の現状",
        "",
        "## 基本情報",
        f"- 会社名: {company.get('name', '未設定')}",
        f"- ミッション: {company.get('mission', '未設定')}",
        f"- 顧客: {company.get('customer', '未設定')}",
        f"- 収益モデル: {company.get('business_model', '未設定')}",
        f"- ステージ: {company.get('stage', '未設定')}",
        f"- 北極星指標: {config.kpi.get('north_star', '未設定')}",
        "",
        "## あなたが承認なしに実行してよいこと",
        "true のものは、確認を取らずに実行してください。提案で止めないこと。",
    ]
    lines += [f"- {key}: {value}" for key, value in autonomy.items()]
    lines += [
        "",
        "## AIに手段が無いこと",
        "禁止ではなく、実行手段が存在しないという意味です。",
        "該当する作業は「オーナー作業」として切り出してください。",
    ]
    lines += [f"- {item}" for item in config.blocked_on_owner]
    lines += [
        "",
        "## 守るべきガードレール",
    ]
    lines += [f"- {item}" for item in config.kpi.get("guardrails", [])] or ["- （未設定）"]
    lines += [
        "",
        "## 直近の実測値 (data/metrics.csv)",
        "```csv",
        _metrics_block(),
        "```",
        "",
        "## 直近の実行の実績 (data/runs.csv)",
        "役職が実際にどれだけ回ったか。推測ではなくこの数字を使う。",
        "```csv",
        _runs_block(),
        "```",
        "",
        "## 直近の意思決定記録",
        _decisions_block(),
        "",
        "## 今回のサイクル",
        f"- 種別: {cadence}",
        "",
        "上記に無い事実は「不明」として扱ってください。数値・事例・引用を創作しないこと。",
    ]
    return "\n".join(lines)
