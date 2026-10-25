"""Google AI Studio（Gemini API）連携。

依存ライブラリ無しで REST を直接叩く。AI Studio で発行した API キーだけで動く。
GeminiProvider は会社を Gemini で回すため、company_tool_declarations は
逆に Gemini 側から会社を操作するための関数定義。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .providers import ProviderError, ToolCall, ToolSpec, Turn

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def _request(method: str, path: str, api_key: str, payload: dict | None = None,
             timeout: float = 600, base_url: str = BASE_URL) -> dict:
    url = f"{base_url}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "x-goog-api-key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            msg = json.loads(body)["error"]["message"]
        except Exception:
            msg = body[:400]
        if exc.code in (400, 401, 403) and "API key" in msg:
            raise ProviderError("AI Studio の API キーが無効です。https://aistudio.google.com/apikey で発行したキーを右パネルに入れてください。") from exc
        raise ProviderError(f"Gemini API エラー (HTTP {exc.code}): {msg}") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"Gemini API に接続できません: {exc.reason}") from exc


def _schema(parameters: dict[str, Any]) -> dict[str, Any]:
    """JSON Schema を Gemini が受ける OpenAPI サブセットに寄せる（additionalProperties 等を落とす）。"""
    allowed = {"type", "properties", "required", "description", "items", "enum", "nullable"}
    out = {k: v for k, v in parameters.items() if k in allowed}
    if "properties" in out:
        out["properties"] = {k: _schema(v) if isinstance(v, dict) else v for k, v in out["properties"].items()}
    if "items" in out and isinstance(out["items"], dict):
        out["items"] = _schema(out["items"])
    return out


class GeminiProvider:
    """Gemini のネイティブ function calling で、会社のループを回す。"""

    name = "gemini"

    def __init__(self, model: str, api_key: str, base_url: str = BASE_URL):
        if not api_key:
            raise ProviderError("AI Studio の API キーが設定されていません。右パネルに入れてください。")
        self.model = model.removeprefix("models/")
        self.api_key = api_key
        self.base_url = base_url

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        return _request(method, path, self.api_key, payload, base_url=self.base_url)

    def list_models(self) -> list[str]:
        data = self._call("GET", "/models?pageSize=200")
        return sorted(
            m["name"].removeprefix("models/")
            for m in data.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        )

    def user_message(self, text: str) -> Any:
        return {"role": "user", "parts": [{"text": text}]}

    def tool_results(self, results: list[tuple[ToolCall, str]]) -> list[Any]:
        return [{
            "role": "user",
            "parts": [
                {"functionResponse": {"name": call.name, "response": {"result": output}}}
                for call, output in results
            ],
        }]

    def complete(self, system, history, tools, temperature) -> Turn:
        payload: dict[str, Any] = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": history,
            "generationConfig": {"temperature": temperature},
        }
        if tools:
            payload["tools"] = [{"functionDeclarations": [
                {"name": t.name, "description": t.description, "parameters": _schema(t.parameters)} for t in tools
            ]}]
        data = self._call("POST", f"/models/{self.model}:generateContent", payload)

        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "不明")
            raise ProviderError(f"Gemini が応答を返しませんでした（{reason}）")
        content = candidates[0].get("content") or {"role": "model", "parts": []}
        parts = content.get("parts") or []
        texts: list[str] = []
        calls: list[ToolCall] = []
        for i, part in enumerate(parts):
            if "text" in part:
                texts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                calls.append(ToolCall(id=f"call_{i}", name=fc.get("name", ""), arguments=dict(fc.get("args") or {})))
        usage = data.get("usageMetadata") or {}
        return Turn(
            text="\n".join(texts).strip(),
            tool_calls=calls,
            assistant_message={"role": "model", "parts": parts or [{"text": ""}]},
            usage={"input": int(usage.get("promptTokenCount", 0)), "output": int(usage.get("candidatesTokenCount", 0))},
        )


# --------------------------------------------------------------------------- #
# 逆方向: Gemini / AI Studio から会社を操作するための関数定義
# --------------------------------------------------------------------------- #

COMPANY_FUNCTIONS: list[dict[str, Any]] = [
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
        "parameters": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
    },
    {
        "name": "stop_run",
        "description": "実行中のランを止める。",
        "parameters": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
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


def company_tool_declarations() -> dict[str, Any]:
    """AI Studio の「Function calling」や Gemini API の tools にそのまま貼れる形。"""
    return {"tools": [{"functionDeclarations": COMPANY_FUNCTIONS}]}
