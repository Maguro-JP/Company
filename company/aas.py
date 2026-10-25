"""AdaptiveAIStudio をモデル（と手）として使う。

AdaptiveAIStudio は画面の本体（/api/send）に依頼を受け取り、出来事の流れ（/api/events）で
返事を流す。ここではそれを1往復の complete() に畳む。ツールは渡さない。向こうは自分の
道具（read_file / write_file / run_command）と作業場を持っていて、こちらの道具は見ないため。
作業場を会社のリポジトリにして起動しておけば、向こうの作業担当がこのリポジトリに書く。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .providers import ProviderError, ToolCall, ToolSpec, Turn

DEFAULT_URL = "http://127.0.0.1:4174"


def _json(url: str, payload: dict | None = None, timeout: float = 30) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"error": body[:300]}
    except urllib.error.URLError as exc:
        raise ProviderError(
            f"AdaptiveAIStudio に接続できません（{url}）: {exc.reason}。"
            "`npm run ui` か start.cmd で起動しているか確認してください。"
        ) from exc


class AasProvider:
    """AdaptiveAIStudio の本体に依頼を送り、答えが出揃うまで待つ。"""

    name = "aas"

    def __init__(self, base_url: str = DEFAULT_URL, model: str = "", timeout: float = 1800):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def list_models(self) -> list[str]:
        status, state = _json(f"{self.base_url}/api/state")
        if status != 200:
            raise ProviderError(f"AdaptiveAIStudio の状態を読めません（HTTP {status}）")
        # どのモデルを使うかは向こうの自動操縦が決める。こちらは「向こうに任せる」1つだけを出す
        name = (state.get("model") or state.get("candidate") or "自動（AdaptiveAIStudio が選ぶ）")
        return [str(name)]

    def user_message(self, text: str) -> Any:
        return {"role": "user", "content": text}

    def tool_results(self, results: list[tuple[ToolCall, str]]) -> list[Any]:
        return [{"role": "user", "content": output} for _, output in results]

    def complete(self, system, history, tools, temperature) -> Turn:
        # 向こうは会話を自分で持つので、送るのは最新の依頼だけ。決まり（system）は最初の1回だけ添える
        latest = next((m for m in reversed(history) if m.get("role") == "user"), None)
        text = latest["content"] if latest else ""
        if len(history) == 1:
            text = f"{system}\n\n---\n\n{text}"

        seq = self._events_seq()
        status, ack = _json(f"{self.base_url}/api/send", {"text": text}, timeout=120)
        if status == 503:
            raise ProviderError(f"AdaptiveAIStudio が受け付けませんでした: {ack.get('error', '')}")
        if status not in (200, 202):
            raise ProviderError(f"AdaptiveAIStudio への送信に失敗しました（HTTP {status}）: {ack.get('error', ack)}")
        conversation = ack.get("conversationId")

        reply = self._collect(conversation, seq)
        return Turn(text=reply.strip(), tool_calls=[], assistant_message={"role": "assistant", "content": reply.strip()})

    def _events_seq(self) -> int:
        try:
            status, data = _json(f"{self.base_url}/api/events/seq")
            return int(data.get("seq", 0)) if status == 200 else 0
        except (ProviderError, ValueError, TypeError):
            return 0

    def _collect(self, conversation: str | None, since: int) -> str:
        """/api/events を読み、この会話の busy:false が来るまでの text を集める。"""
        url = f"{self.base_url}/api/events?since={since}"
        req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
        deadline = time.time() + self.timeout
        text: list[str] = []
        started = False
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                buf = b""
                while time.time() < deadline:
                    chunk = resp.readline()
                    if not chunk:
                        break
                    buf += chunk
                    if chunk not in (b"\n", b"\r\n"):
                        continue
                    for line in buf.decode("utf-8", errors="replace").splitlines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            ev = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        if conversation and ev.get("conversationId") not in (None, conversation):
                            continue
                        if ev.get("type") == "busy":
                            if ev.get("busy"):
                                started = True
                            elif started or text:
                                return "".join(text)
                        elif ev.get("type") == "session":
                            inner = ev.get("event") or {}
                            if inner.get("type") == "text" and isinstance(inner.get("delta"), str):
                                text.append(inner["delta"])
                            elif inner.get("type") == "failed":
                                raise ProviderError(f"AdaptiveAIStudio 側で失敗しました: {(inner.get('error') or {}).get('message', '')}")
                        elif ev.get("type") == "log" and ev.get("level") == "error":
                            text.append(f"\n[AdaptiveAIStudio] {ev.get('text', '')}")
                    buf = b""
        except urllib.error.URLError as exc:
            raise ProviderError(f"AdaptiveAIStudio の出来事の流れが切れました: {exc.reason}") from exc
        if not text:
            raise ProviderError("AdaptiveAIStudio から返事が来ませんでした（時間切れ）")
        return "".join(text)
