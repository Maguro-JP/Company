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


# OpenAI 互換の口を持つサービス。URL と鍵の環境変数名だけが違うので、1つの実装で全部を扱う。
# 新しいサービスを足すときは、ここに1行足すだけでよい（画面の一覧もここから作る）。
PRESETS: list[dict[str, Any]] = [
    {"id": "openai", "label": "OpenAI", "base_url": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY"},
    {"id": "openrouter", "label": "OpenRouter（多数のモデルを1つの鍵で）", "base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY"},
    {"id": "groq", "label": "Groq", "base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY"},
    {"id": "deepseek", "label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "key_env": "DEEPSEEK_API_KEY"},
    {"id": "xai", "label": "xAI（Grok）", "base_url": "https://api.x.ai/v1", "key_env": "XAI_API_KEY"},
    {"id": "mistral", "label": "Mistral", "base_url": "https://api.mistral.ai/v1", "key_env": "MISTRAL_API_KEY"},
    {"id": "together", "label": "Together AI", "base_url": "https://api.together.xyz/v1", "key_env": "TOGETHER_API_KEY"},
    {"id": "perplexity", "label": "Perplexity", "base_url": "https://api.perplexity.ai", "key_env": "PERPLEXITY_API_KEY"},
    {"id": "ollama", "label": "Ollama（手元）", "base_url": "http://localhost:11434/v1", "key_env": "", "local": True},
    {"id": "lmstudio", "label": "LM Studio（手元）", "base_url": "http://localhost:1234/v1", "key_env": "", "local": True},
]
PRESET_BY_ID = {p["id"]: p for p in PRESETS}

# 専用の実装を持つもの。画面の一覧の先頭に並ぶ。
NATIVE: list[dict[str, Any]] = [
    {"id": "builtin", "label": "内蔵（単体で動く）", "key_env": "", "local": True},
    {"id": "aas", "label": "AdaptiveAIStudio（起動中の本体に任せる）", "key_env": "", "local": True, "base_url": "http://127.0.0.1:4174"},
    {"id": "gemini", "label": "Google AI Studio（Gemini）", "key_env": "GEMINI_API_KEY"},
    {"id": "anthropic", "label": "Anthropic", "key_env": "ANTHROPIC_API_KEY"},
    {"id": "openai_compatible", "label": "OpenAI 互換（URL を自分で書く）", "key_env": "", "local": True, "custom": True},
]


def provider_catalog() -> list[dict[str, Any]]:
    """画面に出す一覧。鍵が環境変数に入っているかも返す（値は返さない）。"""
    out = []
    for entry in [*NATIVE, *PRESETS]:
        env = entry.get("key_env", "")
        out.append({
            "id": entry["id"], "label": entry["label"], "base_url": entry.get("base_url", ""),
            "needs_key": bool(env), "key_env": env, "key_in_env": bool(env and os.environ.get(env)),
            "local": bool(entry.get("local")), "custom": bool(entry.get("custom")),
        })
    return out


def make_provider(settings: dict[str, Any]) -> Provider:
    """settings: {provider, model, base_url, api_key, context, gpu_layers}"""
    kind = settings.get("provider", "builtin")
    model = settings.get("model") or ""
    if not model:
        raise ProviderError("モデルが選択されていません。")
    if kind in PRESET_BY_ID:
        preset = PRESET_BY_ID[kind]
        key = settings.get("api_key") or (os.environ.get(preset["key_env"]) if preset["key_env"] else "") or ""
        if preset["key_env"] and not key:
            raise ProviderError(
                f"{preset['label']} の API キーがありません。右パネルに入れるか、環境変数 {preset['key_env']} を設定してください。"
            )
        return OpenAICompatibleProvider(base_url=preset["base_url"], model=model, api_key=key)
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
