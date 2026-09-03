"""役員会。各エージェントを順に走らせ、結果を意思決定記録に残す。

順序には意味がある。CEOが決めた一点集中を、後続の役職が前提として受け取る。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import context as context_builder
from .config import Agent, Config, DECISIONS_DIR, load_agents

# CEOが先、Opsが最後。それ以外はこの順で挟む。
ROLE_ORDER = ["ceo", "cto", "cmo", "cfo", "ops"]

# 役員の報告は長くなる。ここを絞ると report が途中で切れる。
MAX_TOKENS = 16000


@dataclass
class Report:
    agent: Agent
    body: str


def _sort_roles(roles: list[str]) -> list[str]:
    known = [r for r in ROLE_ORDER if r in roles]
    unknown = [r for r in roles if r not in ROLE_ORDER]
    return known + unknown


NO_CREDENTIALS = (
    "Anthropic の認証情報が見つかりません。次のいずれかを設定してください:\n"
    "  export ANTHROPIC_API_KEY=sk-ant-...\n"
    "  または ant auth login （プロファイルはSDKが自動的に読む）"
)


def _client():
    try:
        from anthropic import Anthropic
    except ImportError as exc:  # pragma: no cover - 依存が無い環境向け
        raise SystemExit(
            "anthropic パッケージが必要です: pip install -r requirements.txt"
        ) from exc
    # 認証情報の解決はSDKに任せる。APIキーだけを見ると、
    # ant auth login で作ったプロファイルを使っている環境を誤って弾く。
    # なおSDKは生成時ではなく最初のリクエスト時に認証エラーを出すので、
    # 検知は _ask 側で行う。
    return Anthropic()


def _ask(client, model: str, agent: Agent, shared: str, upstream: list[Report]) -> str:
    prompt = [shared]
    if upstream:
        prompt.append("\n# 同じサイクルで先に報告された内容\n")
        for report in upstream:
            prompt.append(f"## {report.agent.title}\n{report.body}\n")
    prompt.append(
        "\n# あなたへの指示\n"
        "上記を踏まえ、あなたの役割定義に書かれた形式でのみ回答してください。"
        "前置きと結びの挨拶は不要です。"
    )
    try:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=agent.instructions,
            messages=[{"role": "user", "content": "\n".join(prompt)}],
        )
    except TypeError as exc:
        # SDKは認証情報を解決できないとき、リクエスト時に TypeError を投げる。
        if "authentication method" in str(exc):
            raise SystemExit(NO_CREDENTIALS) from exc
        raise
    return "".join(block.text for block in response.content if block.type == "text").strip()


def run_cycle(config: Config, cadence: str, dry_run: bool = False) -> list[Report]:
    roles = _sort_roles(config.roles_for(cadence))
    if not roles:
        raise SystemExit(f"cadence '{cadence}' に対応する役職が company.yaml にありません。")

    shared = context_builder.build(config, cadence)
    agents = load_agents(roles)

    if dry_run:
        for agent in agents:
            print(f"=== {agent.title} ({agent.role}) / model={config.model_for(agent.model_tier)} ===")
        print("\n--- 共有コンテキスト ---\n")
        print(shared)
        return []

    client = _client()
    reports: list[Report] = []
    for agent in agents:
        model = config.model_for(agent.model_tier)
        print(f"[{agent.role}] {model} に問い合わせ中...", flush=True)
        body = _ask(client, model, agent, shared, reports)
        reports.append(Report(agent=agent, body=body))
    return reports


def write_record(config: Config, cadence: str, reports: list[Report]) -> Path:
    DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    existing = len(list(DECISIONS_DIR.glob(f"{today}-{cadence}*.md")))
    suffix = f"-{existing + 1}" if existing else ""
    path = DECISIONS_DIR / f"{today}-{cadence}{suffix}.md"

    lines = [
        f"# {today} {cadence} サイクル",
        "",
        f"- 会社: {config.company.get('name', '未設定')}",
        f"- ステージ: {config.company.get('stage', '未設定')}",
        "",
        "> この記録はAIエージェントが生成したものです。"
        "オーナー専権事項（company.yaml の human_authority）については、"
        "承認されるまで決定事項ではありません。",
        "",
    ]
    for report in reports:
        lines += [f"## {report.agent.title}", "", report.body, ""]
    lines += [
        "## オーナー承認欄",
        "",
        "- [ ] 内容を確認した",
        "- [ ] 承認が必要な項目に判断を記入した",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
