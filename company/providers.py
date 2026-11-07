"""LLMプロバイダの抽象化。

クラウド (Anthropic) とローカル (Ollama / LM Studio などの OpenAI 互換API) を
同じインターフェースで扱う。会話履歴はプロバイダ固有の形式のまま持ち、
ループ側は Turn だけを見る。
"""

from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
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
    """kind は失敗の種類。画面と記録に出す文は人の言葉にそろえる（AdaptiveAIStudio の errors.ts と同じ分け方）。"""

    def __init__(self, message: str, kind: str = "invalid_request"):
        super().__init__(message)
        self.kind = kind


# 失敗の種類と、人に見せる一文。生の HTTP ステータスや errno だけを出すと、次の一手が分からない。
KIND_TEXT = {
    "auth": "API キーが拒否されました。右パネルのキーか、環境変数の値を確かめてください。",
    "rate_limited": "利用制限に当たりました。少し待ってからもう一度実行してください。",
    "quota_exhausted": "残高か利用枠を使い切っています。サービス側の課金画面を確かめてください。",
    "model_missing": "そのモデルは相手に無いと言われました。「↻」で一覧を取り直して選んでください。",
    "context_too_long": "依頼が長すぎてモデルに収まりません。最大ターン数かコンテキスト長を見直してください。",
    "unavailable": "相手のサーバーが応答できない状態です（5xx）。時間をおいて再実行してください。",
    "timeout": "時間切れです。相手が遅いか、途中で止まっています。",
}


def explain_network_error(reason: object) -> str:
    """接続の失敗を人の言葉で言う。errno の名前では何が起きたか分からない（AdaptiveAIStudio の net-why.ts と同じ分け方）。"""
    text = str(reason)
    # Windows は WinError の番号と英語の文で来る（10061 拒否、11001 名前、10060 時間切れ）
    if re.search(r"Connection refused|ECONNREFUSED|errno 111|actively refused|10061", text, re.I):
        return "繋ぐ先に断られました。サーバーが起動していないか、ポートが違います。"
    if re.search(r"Name or service not known|nodename nor servname|getaddrinfo|ENOTFOUND|errno -2|11001", text, re.I):
        return "相手の名前を引けません。回線が繋がっていないか、URL の綴りが違います。"
    if re.search(r"timed out|timeout|10060", text, re.I):
        return "時間切れです。相手が応答しません。"
    if re.search(r"CERTIFICATE|certificate|SSL|TLS", text):
        return "証明書か暗号化のやりとりで断られました。会社の回線（プロキシ）で起きることがあります。"
    if re.search(r"reset|ECONNRESET|Broken pipe", text, re.I):
        return "途中で切れました。"
    return text


def classify_http_error(status: int, body: str) -> str:
    """HTTP ステータスと本文から失敗の種類を決める。429 は制限と枠切れが同じ番号なので本文で見分ける。"""
    if status == 402:
        return "quota_exhausted"
    if status == 429:
        return "quota_exhausted" if re.search(r"insufficient_quota|billing_hard_limit|per[- ]?day|credit balance|out of credits|spending limit", body, re.I) else "rate_limited"
    if status in (401, 403):
        return "auth"
    if status == 413:
        return "context_too_long"
    if status == 404 and re.search(r"model_not_found|model\b[^\n]{0,80}?(not found|does not exist|not exist|unknown)|(unknown|no such) model", body, re.I):
        return "model_missing"
    if status in (408, 504):
        return "timeout"
    if status >= 500:
        return "unavailable"
    if status == 400 and re.search(r"context_length_exceeded|request_too_large|maximum context|too many tokens|context[ _-]?(length|window)", body, re.I):
        return "context_too_long"
    if status == 400 and re.search(r"does not support tools|tools? (is|are) not supported", body, re.I):
        return "model_missing"
    return "invalid_request"


def http_error(url: str, status: int, body: str) -> ProviderError:
    kind = classify_http_error(status, body)
    head = KIND_TEXT.get(kind, "")
    return ProviderError(f"{head} ({url} -> HTTP {status}: {body[:300]})".strip(), kind)


def check_connect(url: str, timeout: float) -> None:
    """TCP で繋がるかだけを短く見る。urllib の timeout は生成の待ち時間と同じ値になるので、
    止まっている相手に対して 10 分待つことになる（AdaptiveAIStudio で実際に起きた）。繋ぐ時間と答えを待つ時間は分ける。"""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as exc:
        raise ProviderError(f"{url} に接続できません: {explain_network_error(exc)}", "unavailable") from exc


# --------------------------------------------------------------------------- #
# OpenAI 互換 (Ollama, LM Studio, vLLM, llama.cpp server など)
# --------------------------------------------------------------------------- #


class OpenAICompatibleProvider:
    """ローカルLLM向け。/v1/chat/completions を叩く。依存ライブラリ無し。"""

    name = "openai_compatible"

    # 待ち時間は 3 つに分ける。繋がるまで（短い）、一覧（短い）、生成（長い。大きなモデルの読み込みに数分かかる）。
    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 600,
                 connect_timeout: float = 10, list_timeout: float = 5):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.list_timeout = list_timeout

    def _request(self, path: str, payload: dict | None = None, timeout: float | None = None) -> dict:
        url = f"{self.base_url}{path}"
        check_connect(url, self.connect_timeout)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise http_error(url, exc.code, body) from exc
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ProviderError(f"{url} に接続できません: {explain_network_error(reason)}", "unavailable") from exc

    def list_models(self) -> list[str]:
        data = self._request("/models", timeout=self.list_timeout)
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


CHECK_QUESTION = "1+1="


def answer_check(settings: dict[str, Any], question: str = CHECK_QUESTION) -> dict[str, Any]:
    """選んだ AI が本当に答えるかを 1 問で確かめる。設定が合っているかは、ここでしか分からない
    （AdaptiveAIStudio の「本当に答えるか確かめる」と同じ）。ツールは渡さない。"""
    started = time.time()
    provider = make_provider(settings)
    turn = provider.complete("短く答えてください。", [provider.user_message(question)], [], 0.0)
    return {
        "provider": settings.get("provider", ""),
        "model": settings.get("model", ""),
        "question": question,
        "answer": turn.text,
        "seconds": round(time.time() - started, 1),
        "usage": turn.usage,
    }
