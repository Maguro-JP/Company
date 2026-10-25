"""LLMプロバイダの抽象化。

クラウド (Anthropic) とローカル (Ollama / LM Studio などの OpenAI 互換API) を
同じインターフェースで扱う。会話履歴はプロバイダ固有の形式のまま持ち、
ループ側は Turn だけを見る。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Turn:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    assistant_message: Any = None  # 履歴に積むプロバイダ固有の形
    usage: dict[str, int] = field(default_factory=dict)


class Provider(Protocol):
    name: str

    def complete(
        self, system: str, history: list[Any], tools: list[ToolSpec], temperature: float
    ) -> Turn: ...

    def user_message(self, text: str) -> Any: ...

    def tool_results(self, results: list[tuple[ToolCall, str]]) -> list[Any]: ...

    def list_models(self) -> list[str]: ...


class ProviderError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# OpenAI 互換 (Ollama, LM Studio, vLLM, llama.cpp server など)
# --------------------------------------------------------------------------- #


class OpenAICompatibleProvider:
    """ローカルLLM向け。/v1/chat/completions を叩く。依存ライブラリ無し。"""

    name = "openai_compatible"

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 600):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def _request(self, path: str, payload: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            raise ProviderError(f"{url} -> HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(
                f"{url} に接続できません: {exc.reason}。ローカルLLMサーバーが起動しているか確認してください。"
            ) from exc

    def list_models(self) -> list[str]:
        data = self._request("/models")
        return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))

    def user_message(self, text: str) -> Any:
        return {"role": "user", "content": text}

    def tool_results(self, results: list[tuple[ToolCall, str]]) -> list[Any]:
        return [
            {"role": "tool", "tool_call_id": call.id, "content": output}
            for call, output in results
        ]

    def complete(self, system, history, tools, temperature) -> Turn:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *history],
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        data = self._request("/chat/completions", payload)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        calls: list[ToolCall] = []
        for i, tc in enumerate(message.get("tool_calls") or []):
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                args = {"_raw": raw_args}
            calls.append(ToolCall(id=tc.get("id") or f"call_{i}", name=fn.get("name", ""), arguments=args))
        usage = data.get("usage") or {}
        return Turn(
            text=(message.get("content") or "").strip(),
            tool_calls=calls,
            assistant_message=message,
            usage={
                "input": int(usage.get("prompt_tokens", 0)),
                "output": int(usage.get("completion_tokens", 0)),
            },
        )


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, max_tokens: int = 16000):
        try:
            from anthropic import Anthropic  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("anthropic パッケージが必要です: pip install anthropic") from exc
        self.client = Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def list_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self.client.models.list())
        except Exception as exc:  # 認証エラーなど
            raise ProviderError(f"モデル一覧を取得できません: {exc}") from exc

    def user_message(self, text: str) -> Any:
        return {"role": "user", "content": text}

    def tool_results(self, results: list[tuple[ToolCall, str]]) -> list[Any]:
        # Anthropic は複数の tool_result を1つの user メッセージにまとめる
        return [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": call.id, "content": output}
                    for call, output in results
                ],
            }
        ]

    def complete(self, system, history, tools, temperature) -> Turn:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": history,
        }
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        try:
            response = self.client.messages.create(**kwargs)
        except TypeError as exc:
            if "authentication" in str(exc):
                raise ProviderError(
                    "Anthropic の認証情報がありません。ANTHROPIC_API_KEY を設定するか、"
                    "プロバイダをローカルLLMに切り替えてください。"
                ) from exc
            raise
        except Exception as exc:
            raise ProviderError(f"Anthropic API エラー: {exc}") from exc

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
        return Turn(
            text="\n".join(text_parts).strip(),
            tool_calls=calls,
            assistant_message={"role": "assistant", "content": response.content},
            usage={
                "input": response.usage.input_tokens,
                "output": response.usage.output_tokens,
            },
        )


# --------------------------------------------------------------------------- #


def make_provider(settings: dict[str, Any]) -> Provider:
    """settings: {provider, model, base_url, api_key, context, gpu_layers}"""
    kind = settings.get("provider", "builtin")
    model = settings.get("model") or ""
    if not model:
        raise ProviderError("モデルが選択されていません。")
    if kind == "builtin":
        from .local_llm import BuiltinProvider  # noqa: PLC0415

        return BuiltinProvider(
            model=model,
            n_ctx=int(settings.get("context") or 8192),
            n_gpu_layers=int(settings.get("gpu_layers", -1)),
        )
    if kind == "anthropic":
        return AnthropicProvider(model=model)
    if kind == "gemini":
        from .gemini import GeminiProvider  # noqa: PLC0415

        return GeminiProvider(model=model, api_key=settings.get("api_key") or os.environ.get("GEMINI_API_KEY", ""))
    if kind == "aas":
        from .aas import AasProvider, DEFAULT_URL  # noqa: PLC0415

        return AasProvider(base_url=settings.get("base_url") or DEFAULT_URL, model=model)
    if kind == "openai_compatible":
        return OpenAICompatibleProvider(
            base_url=settings.get("base_url") or "http://localhost:11434/v1",
            model=model,
            api_key=settings.get("api_key") or "",
        )
    raise ProviderError(f"未対応のプロバイダ: {kind}")
