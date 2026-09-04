"""実行サイクル。エージェントに実際のツールを渡し、コードを書かせてコミットさせる。

board.py（報告を書くだけ）との違いは、ここでは Claude Agent SDK を使い、
ファイル編集・テスト実行・git を含むツールを与えている点。
出力は文章ではなく、リポジトリへの変更そのもの。
"""

from __future__ import annotations

import asyncio

from .config import ROOT, Config, load_agent

NO_CLI = (
    "エージェントを起動できませんでした。多くは認証切れです。次を確認してください:\n"
    "  claude --version   （Claude Code CLI が入っているか）\n"
    "  claude             （対話で一度ログインする）\n"
    "  または export ANTHROPIC_API_KEY=sk-ant-..."
)

# 実行に必要なツール。ここを絞ると、エージェントは提案しかできなくなる。
BUILD_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Bash", "TodoWrite"]
REVIEW_TOOLS = ["Read", "Glob", "Grep", "Bash"]

TOOLS_BY_ROLE = {
    "ceo": REVIEW_TOOLS,
    "cfo": REVIEW_TOOLS,
    "ops": REVIEW_TOOLS,
    "builder": BUILD_TOOLS,
    "cto": BUILD_TOOLS,
    "cmo": BUILD_TOOLS,
}


def _require_sdk():
    try:
        from claude_agent_sdk import (  # noqa: PLC0415
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            query,
        )
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "claude-agent-sdk が必要です: pip install -r requirements.txt"
        ) from exc
    return AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query


async def run_role(config: Config, role: str, task: str) -> str:
    """1つの役職に、実際に作業をさせる。戻り値は最後のテキスト出力。"""
    AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query = _require_sdk()

    agent = load_agent(role)
    limits = config.raw.get("limits", {})

    options = ClaudeAgentOptions(
        system_prompt=agent.instructions,
        model=config.model_for(agent.model_tier),
        allowed_tools=TOOLS_BY_ROLE.get(role, REVIEW_TOOLS),
        # 承認を挟まない。挟むと「AIが経営する」にならない。
        permission_mode="bypassPermissions",
        cwd=str(ROOT),
        max_turns=limits.get("max_turns", 120),
        max_budget_usd=limits.get("max_budget_usd", 5.0),
    )

    from claude_agent_sdk import CLINotFoundError, ProcessError  # noqa: PLC0415

    last_text = ""
    cost = None
    try:
        stream = query(prompt=task, options=options)
    except CLINotFoundError as exc:  # pragma: no cover
        raise SystemExit(NO_CLI) from exc

    try:
        async for message in stream:
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        last_text = block.text
                        print(f"[{role}] {block.text.strip()[:160]}", flush=True)
            elif isinstance(message, ResultMessage):
                cost = getattr(message, "total_cost_usd", None)
    except (CLINotFoundError, ProcessError) as exc:
        raise SystemExit(f"{NO_CLI}\n\n（内部エラー: {exc}）") from exc

    if cost is not None:
        print(f"[{role}] 完了 (${cost:.4f})", flush=True)
    return last_text


def build_task(config: Config, shared: str) -> str:
    """実装役への指示。何を作るかではなく、どう終わらせるかを固定する。"""
    return f"""{shared}

# 今回のタスク

product/ にあるプロダクトを、顧客にとって価値が1つ増える状態にしてください。
何を作るかはあなたが決めます。ただし、以下を満たすまでが1タスクです。

1. 実装する
2. product/tests/ にテストを足す
3. `cd product && python -m unittest discover -s tests` が通ることを確認する
4. 通ってから `git add` して `git commit` する（テストが落ちたままコミットしない）

大きく作らないでください。1サイクルで1つ、確実に動くものを足す方が価値が高い。
既にあるものを壊さないこと。作業が終わったら、何を足したかを3行以内で報告してください。
"""


def review_task(config: Config, shared: str) -> str:
    return f"""{shared}

# 今回のタスク

リポジトリの現状を実際に読んで確認し、あなたの役割定義の形式で報告してください。
推測で書かず、ファイルを読んで確かめること。テストの状態は実際に実行して確認すること。
"""


async def run_cycle_async(config: Config, cadence: str) -> None:
    from . import context as context_builder  # noqa: PLC0415

    roles = config.roles_for(cadence)
    if not roles:
        raise SystemExit(f"cadence '{cadence}' が company.yaml にありません。")

    shared = context_builder.build(config, cadence)
    for role in roles:
        task = build_task(config, shared) if role in ("builder", "cto") else review_task(config, shared)
        print(f"\n=== {role} 開始 ===", flush=True)
        await run_role(config, role, task)


def run_cycle(config: Config, cadence: str) -> None:
    asyncio.run(run_cycle_async(config, cadence))
