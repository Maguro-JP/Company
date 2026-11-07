"""ランの終わりを外に知らせる。

Slack / Discord の incoming webhook に JSON を1つ送るだけ。Slack は "text"、Discord は "content" を
読むので両方入れる。送れなくてもランの成否は変えない（知らせは付け足しであって本体ではない）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

TIMEOUT_SEC = 10
EXCERPT = 400


def should_notify(settings: dict[str, Any], status: str) -> bool:
    url = (settings.get("webhook_url") or "").strip()
    if not url:
        return False
    mode = settings.get("notify_on", "always")
    return mode == "always" or (mode == "error" and status != "done")


def build_message(run_summary: dict[str, Any], status: str, reports: list[tuple[str, str]], usage: dict[str, int]) -> str:
    head = {"done": "完了", "error": "失敗", "stopped": "停止"}.get(status, status)
    lines = [f"[Company] {run_summary.get('kind')} {run_summary.get('target')} が{head}（run {run_summary.get('id')}）"]
    if usage:
        lines.append(f"入力 {usage.get('input', 0)} / 出力 {usage.get('output', 0)} トークン")
    for title, body in reports[-2:]:
        text = (body or "").strip().replace("\r", "")
        if len(text) > EXCERPT:
            text = text[:EXCERPT] + "…"
        lines.append(f"— {title}: {text or '（出力なし）'}")
    return "\n".join(lines)


def send(url: str, message: str) -> None:
    """失敗は例外で返す。呼ぶ側が記録する。"""
    payload = json.dumps({"text": message, "content": message}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:200]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"接続できません: {exc.reason}") from exc
