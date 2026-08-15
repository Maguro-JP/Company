"""各エージェントに渡す共有コンテキストを組み立てる。

エージェントは「会社の現状」を推測してはいけない。ここで渡した事実だけを使う。
"""

from __future__ import annotations

import csv
from pathlib import Path

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
        "## 越えてはいけない線",
        "以下はオーナー（人間）の専権事項です。あなたはこれらを決定できません。",
    ]
    lines += [f"- {item}" for item in config.human_authority]
    lines += [
        "",
        "## あなたに許された範囲",
        f"- 1アクションあたりの支出上限: {autonomy.get('max_spend_per_action', 0)} 円",
        f"- 外部公開: {'可' if autonomy.get('can_publish_externally') else '不可（下書きまで）'}",
        f"- コード変更: {'可' if autonomy.get('can_write_code') else '不可'}",
        f"- PRのマージ: {'可' if autonomy.get('can_merge_pull_requests') else '不可'}",
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
        "## 直近の意思決定記録",
        _decisions_block(),
        "",
        "## 今回のサイクル",
        f"- 種別: {cadence}",
        "",
        "上記に無い事実は「不明」として扱ってください。数値・事例・引用を創作しないこと。",
    ]
    return "\n".join(lines)
