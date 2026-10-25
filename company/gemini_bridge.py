"""AI Studio（Gemini）側から会社を動かすブリッジ。

    python -m company.gemini_bridge                 # 対話。Gemini に話しかけると会社を操作する
    python -m company.gemini_bridge "build を回して" # 一発

Gemini には会社の関数（start_cycle / get_run など）だけを渡す。実際の処理は起動中のアプリ
（python app.py）の HTTP API に中継するので、画面とブリッジのどちらから動かしても同じ履歴に載る。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

from .gemini import BASE_URL, COMPANY_FUNCTIONS, ProviderError, _request

SYSTEM = """あなたは、AIが経営する会社のオーナー補佐です。ユーザーの意図を会社の操作に変換します。
- 何かを始めるときは start_cycle か start_role を呼び、返ってきた run_id を覚える
- 進捗を聞かれたら get_run を呼んで、要点だけを日本語で報告する（ツール出力を丸写ししない）
- 会社の中身を聞かれたら read_file で実際に読んでから答える
- 自分の推測を事実のように言わない。分からなければ関数で確かめる"""


def call_company(app_url: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{app_url}/api/gemini/call", data=json.dumps({"name": name, "args": args}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise SystemExit(f"アプリに接続できません（{app_url}）。先に `python app.py` を起動してください。{exc.reason}") from exc


def chat_turn(api_key: str, model: str, history: list[dict], app_url: str,
              max_rounds: int = 12, base_url: str = BASE_URL) -> str:
    """ユーザー発話が history 末尾にある状態で呼ぶ。関数呼び出しを解決しきって本文を返す。"""
    tools = [{"functionDeclarations": COMPANY_FUNCTIONS}]
    for _ in range(max_rounds):
        data = _request("POST", f"/models/{model}:generateContent", api_key, {
            "system_instruction": {"parts": [{"text": SYSTEM}]},
            "contents": history, "tools": tools, "generationConfig": {"temperature": 0.2},
        }, base_url=base_url)
        parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts") or [{"text": ""}]
        history.append({"role": "model", "parts": parts})
        calls = [p["functionCall"] for p in parts if "functionCall" in p]
        if not calls:
            return "\n".join(p.get("text", "") for p in parts).strip()
        responses = []
        for fc in calls:
            print(f"  → {fc['name']} {json.dumps(fc.get('args') or {}, ensure_ascii=False)}", flush=True)
            result = call_company(app_url, fc["name"], fc.get("args") or {})
            responses.append({"functionResponse": {"name": fc["name"], "response": result}})
        history.append({"role": "user", "parts": responses})
    return "（関数呼び出しが多すぎたので打ち切りました）"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    api_key = os.environ.get("GEMINI_API_KEY") or ""
    if not api_key:
        try:
            from .runner import load_settings  # noqa: PLC0415
            s = load_settings()
            api_key = s.get("api_key", "") if s.get("provider") == "gemini" else ""
        except Exception:
            pass
    if not api_key:
        print("GEMINI_API_KEY を設定するか、アプリの右パネルで AI Studio のキーを保存してください。", file=sys.stderr)
        return 1
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    app_url = os.environ.get("COMPANY_URL", "http://127.0.0.1:8765")
    history: list[dict] = []

    def ask(text: str) -> None:
        history.append({"role": "user", "parts": [{"text": text}]})
        try:
            print(chat_turn(api_key, model, history, app_url))
        except ProviderError as exc:
            print(str(exc), file=sys.stderr)

    if argv:
        ask(" ".join(argv))
        return 0
    print(f"Gemini ({model}) と会話して会社を操作します。終了は Ctrl+C。")
    try:
        while True:
            text = input("\n> ").strip()
            if text:
                ask(text)
    except (KeyboardInterrupt, EOFError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
