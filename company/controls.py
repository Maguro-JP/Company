"""会社を外から操作するための関数。Gemini・MCP（AdaptiveAIStudio）のどちらもここを呼ぶ。

入口は複数あっても、できることは1つの表で決める。入口ごとに別の操作が
できると、どこから何が起きたか追えなくなる。
"""

from __future__ import annotations

from typing import Any

from . import runner
from .config import ROOT, load_config

FUNCTIONS: list[dict[str, Any]] = [
    {
        "name": "list_roles",
        "description": "会社の役職と、実行できるサイクルの一覧を返す。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "start_cycle",
        "description": "サイクル（build = 次の一手を決めて実装しコミットする / review = 進捗と数字を確認する）を開始する。run_id を返す。",
        "parameters": {
            "type": "object",
            "properties": {
                "cadence": {"type": "string", "description": "build または review"},
                "instruction": {"type": "string", "description": "オーナーからの指示（任意）"},
            },
            "required": ["cadence"],
        },
    },
    {
        "name": "start_role",
        "description": "役職を1つだけ動かす。run_id を返す。",
        "parameters": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "description": "ceo / builder / cto / cmo / cfo / ops"},
                "instruction": {"type": "string", "description": "その役職への指示（任意）"},
            },
            "required": ["role"],
        },
    },
    {
        "name": "get_run",
        "description": "実行の状態と、これまでの出力（発言・ツール呼び出し）を返す。完了まで繰り返し呼んでよい。",
        "parameters": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
    },
    {
        "name": "stop_run",
        "description": "実行中のランを止める。",
        "parameters": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
    },
    {
        "name": "read_file",
        "description": "会社のリポジトリ内のファイルを読む（README.md, company.yaml, product/ 配下など）。",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "リポジトリルートからの相対パス"}},
            "required": ["path"],
        },
    },
]

EVENT_TYPES_FOR_SUMMARY = ("text", "tool_call", "status", "error", "role_start", "role_end")


def read_repo_file(path: str) -> dict[str, Any]:
    target = (ROOT / path).resolve()
    try:
        target.relative_to(ROOT)
    except ValueError:
        return {"error": "リポジトリの外です"}
    if not target.is_file():
        return {"error": f"ファイルがありません: {path}"}
    return {"path": path, "content": target.read_text(encoding="utf-8", errors="replace")}


def dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """関数名と引数で会社を操作する。失敗は例外ではなく {"error": ...} で返す（外の相手に伝えるため）。"""
    try:
        if name == "list_roles":
            return {"roles": runner.list_roles(), "cadences": load_config().raw.get("cadence", {})}
        if name == "start_cycle":
            return runner.start_cycle(args["cadence"], args.get("instruction")).summary()
        if name == "start_role":
            return runner.start_role(args["role"], args.get("instruction")).summary()
        if name in ("get_run", "stop_run"):
            run = runner.REGISTRY.get(args["run_id"])
            if run is None:
                return {"error": f"run が見つかりません: {args['run_id']}"}
            if name == "stop_run":
                run.stop.set()
                return run.summary()
            events = [
                {k: (v[:600] + "…" if isinstance(v, str) and len(v) > 600 else v) for k, v in e.items()}
                for e in run.events if e["type"] in EVENT_TYPES_FOR_SUMMARY
            ]
            return {**run.summary(), "events": events}
        if name == "read_file":
            return read_repo_file(args["path"])
        return {"error": f"未知の関数です: {name}"}
    except KeyError as exc:
        return {"error": f"引数が足りません: {exc}"}
    except (ValueError, FileNotFoundError) as exc:
        return {"error": str(exc)}
