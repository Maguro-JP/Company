"""内蔵の推論エンジン。外部サーバーに依存せず、アプリ単体でモデルを動かす。

llama.cpp（llama-cpp-python）で GGUF を読み込む。ツール呼び出しはチャットテンプレートに
頼らず、プロンプトで形式を指定して自前で解釈する。モデルを変えても同じ経路で動かすため。
"""

from __future__ import annotations

import json
import re
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .providers import ProviderError, ToolCall, ToolSpec, Turn

MODELS_DIR = DATA_DIR / "models"

# 初回にアプリ内からダウンロードできるモデル。全て Qwen 公式の GGUF。
CATALOG: list[dict[str, Any]] = [
    {
        "id": "qwen2.5-coder-7b",
        "label": "Qwen2.5 Coder 7B（推奨・要メモリ 8GB）",
        "file": "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        "bytes": 4683073536,
    },
    {
        "id": "qwen2.5-coder-3b",
        "label": "Qwen2.5 Coder 3B（軽量・要メモリ 4GB）",
        "file": "qwen2.5-coder-3b-instruct-q4_k_m.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-Coder-3B-Instruct-GGUF/resolve/main/qwen2.5-coder-3b-instruct-q4_k_m.gguf",
        "bytes": 2104932800,
    },
    {
        "id": "qwen2.5-coder-1.5b",
        "label": "Qwen2.5 Coder 1.5B（最軽量）",
        "file": "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF/resolve/main/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf",
        "bytes": 1117320768,
    },
    {
        "id": "qwen2.5-0.5b",
        "label": "Qwen2.5 0.5B（動作確認用。実用には弱い）",
        "file": "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "bytes": 491400032,
    },
]

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*(?:</tool_call>|$)", re.DOTALL)
JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


# --------------------------------------------------------------------------- #
# モデルファイルの管理
# --------------------------------------------------------------------------- #


def model_path(model_id: str) -> Path:
    for entry in CATALOG:
        if entry["id"] == model_id:
            return MODELS_DIR / entry["file"]
    # カタログ外: data/models に置かれた任意の .gguf
    candidate = MODELS_DIR / model_id
    if candidate.suffix == ".gguf":
        return candidate
    return MODELS_DIR / f"{model_id}.gguf"


def list_models() -> list[dict[str, Any]]:
    """カタログと data/models 内の GGUF を、ダウンロード状況付きで返す。"""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    seen = set()
    out = []
    for entry in CATALOG:
        path = MODELS_DIR / entry["file"]
        seen.add(path.name)
        out.append({
            "id": entry["id"], "label": entry["label"], "bytes": entry["bytes"],
            "downloaded": path.exists() and path.stat().st_size >= entry["bytes"],
            "downloading": DOWNLOADS.status(entry["id"]),
        })
    for path in sorted(MODELS_DIR.glob("*.gguf")):
        if path.name in seen:
            continue
        out.append({"id": path.name, "label": path.name, "bytes": path.stat().st_size,
                    "downloaded": True, "downloading": None})
    return out


@dataclass
class Download:
    model_id: str
    total: int
    done: int = 0
    error: str | None = None
    finished: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"model_id": self.model_id, "total": self.total, "done": self.done,
                "percent": round(self.done * 100 / self.total, 1) if self.total else 0,
                "error": self.error, "finished": self.finished}


class DownloadManager:
    def __init__(self):
        self._active: dict[str, Download] = {}
        self._lock = threading.Lock()

    def status(self, model_id: str) -> dict[str, Any] | None:
        d = self._active.get(model_id)
        return d.as_dict() if d and not d.finished else None

    def start(self, model_id: str) -> Download:
        entry = next((e for e in CATALOG if e["id"] == model_id), None)
        if entry is None:
            raise ProviderError(f"カタログにないモデルです: {model_id}")
        with self._lock:
            existing = self._active.get(model_id)
            if existing and not existing.finished:
                return existing
            d = Download(model_id=model_id, total=entry["bytes"])
            self._active[model_id] = d
        threading.Thread(target=self._fetch, args=(d, entry), daemon=True).start()
        return d

    def _fetch(self, d: Download, entry: dict[str, Any]) -> None:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        final = MODELS_DIR / entry["file"]
        partial = final.with_suffix(".part")
        try:
            start = partial.stat().st_size if partial.exists() else 0
            req = urllib.request.Request(entry["url"], headers={"Range": f"bytes={start}-"} if start else {})
            with urllib.request.urlopen(req, timeout=60) as resp, partial.open("ab" if start else "wb") as fh:
                d.done = start if resp.status == 206 else 0
                if resp.status != 206 and start:
                    fh.seek(0); fh.truncate()
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
                    d.done += len(chunk)
            if d.done < d.total:
                raise ProviderError(f"途中で切れました（{d.done}/{d.total} bytes）。もう一度押すと続きから再開します。")
            partial.replace(final)
        except Exception as exc:  # ネットワーク断など。UIに見せる
            d.error = str(exc)
        finally:
            d.finished = True


DOWNLOADS = DownloadManager()


# --------------------------------------------------------------------------- #
# 推論
# --------------------------------------------------------------------------- #

_ENGINES: dict[tuple[str, int, int], Any] = {}
_ENGINE_LOCK = threading.Lock()   # llama.cpp は同時に1つしか動かせない


def _engine(path: Path, n_ctx: int, n_gpu_layers: int):
    try:
        from llama_cpp import Llama  # noqa: PLC0415
    except ImportError as exc:
        raise ProviderError(
            "内蔵エンジン（llama-cpp-python）が入っていません。run.bat を実行し直すか、"
            "pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu"
        ) from exc
    key = (str(path), n_ctx, n_gpu_layers)
    with _ENGINE_LOCK:
        if key not in _ENGINES:
            _ENGINES.clear()  # メモリを食うので同時に1つだけ保持する
            _ENGINES[key] = Llama(model_path=str(path), n_ctx=n_ctx, n_gpu_layers=n_gpu_layers, verbose=False)
        return _ENGINES[key]


def _count_tokens(engine, text: str) -> int:
    """エンジンの字句器で数える。無ければ 4 文字 1 トークンで概算する。"""
    tokenize = getattr(engine, "tokenize", None)
    if tokenize is not None:
        try:
            return len(tokenize(text.encode("utf-8"), add_bos=False))
        except Exception:
            pass
    return max(1, len(text) // 4) if text else 0


def tool_instructions(tools: list[ToolSpec]) -> str:
    if not tools:
        return ""
    lines = [
        "",
        "# ツールの使い方",
        "ツールを使うときは、次の形式**だけ**を返してください（前後に文章を付けない）:",
        '<tool_call>{"name": "ツール名", "arguments": {"引数": "値"}}</tool_call>',
        "1回の返答で呼べるツールは1つです。結果はユーザーのメッセージとして返ってきます。",
        "ツールが不要になったら、タグを使わず普通の文章で答えてください。",
        "",
        "使えるツール:",
    ]
    for t in tools:
        props = t.parameters.get("properties", {})
        required = set(t.parameters.get("required", []))
        args = ", ".join(f"{k}{'' if k in required else '?'}: {v.get('description', v.get('type', ''))}" for k, v in props.items())
        lines.append(f"- {t.name}({args}) — {t.description}")
    # 小さいモデルは説明だけだと書式を落とす。1つ例を見せると安定する。
    first = tools[0]
    example_args = {k: "..." for k in first.parameters.get("required", [])}
    lines += [
        "",
        "例:",
        f"ユーザー: {first.description.split('。')[0]}",
        f'あなた: <tool_call>{json.dumps({"name": first.name, "arguments": example_args}, ensure_ascii=False)}</tool_call>',
    ]
    return "\n".join(lines)


def parse_tool_call(text: str) -> tuple[str, ToolCall | None]:
    """返答からツール呼び出しを取り出す。無ければ (本文, None)。"""
    m = TOOL_CALL_RE.search(text) or JSON_BLOCK_RE.search(text)
    if not m:
        return text.strip(), None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return text.strip(), None
    name = data.get("name")
    if not isinstance(name, str):
        return text.strip(), None
    args = data.get("arguments") or data.get("parameters") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"_raw": args}
    before = text[: m.start()].strip()
    return before, ToolCall(id=f"call_{abs(hash(m.group(1))) % 10**8}", name=name, arguments=dict(args))


class BuiltinProvider:
    """アプリ内で llama.cpp を動かす。履歴は OpenAI 形式の dict。"""

    name = "builtin"

    def __init__(self, model: str, n_ctx: int = 8192, n_gpu_layers: int = -1, max_tokens: int = 2048):
        self.model_id = model
        self.path = model_path(model)
        self.n_ctx = n_ctx
        self.n_gpu_layers = n_gpu_layers
        self.max_tokens = max_tokens

    def list_models(self) -> list[str]:
        return [m["id"] for m in list_models() if m["downloaded"]]

    def user_message(self, text: str) -> Any:
        return {"role": "user", "content": text}

    def tool_results(self, results: list[tuple[ToolCall, str]]) -> list[Any]:
        # tool ロールを知らないテンプレートが多いので、ユーザー発話として渡す
        body = "\n\n".join(f"<tool_result name=\"{call.name}\">\n{output}\n</tool_result>" for call, output in results)
        return [{"role": "user", "content": body}]

    def complete(self, system, history, tools, temperature) -> Turn:
        if not self.path.exists():
            raise ProviderError(f"モデルがまだダウンロードされていません: {self.model_id}。右パネルから取得してください。")
        engine = _engine(self.path, self.n_ctx, self.n_gpu_layers)
        messages = [{"role": "system", "content": system + tool_instructions(tools)}, *history]
        on_delta = getattr(self, "on_delta", None)
        try:
            with _ENGINE_LOCK:
                if on_delta is None:
                    resp = engine.create_chat_completion(
                        messages=messages, temperature=temperature, max_tokens=self.max_tokens,
                        stop=["</tool_call>", "<tool_result"],
                    )
                else:
                    # 生成中の文字を流す。使い終わるまで _ENGINE_LOCK を持つ（llama.cpp は同時に1つ）
                    parts: list[str] = []
                    usage_seen: dict = {}
                    for chunk in engine.create_chat_completion(
                        messages=messages, temperature=temperature, max_tokens=self.max_tokens,
                        stop=["</tool_call>", "<tool_result"], stream=True,
                    ):
                        delta = ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
                        if delta:
                            parts.append(delta)
                            on_delta(delta)
                        if chunk.get("usage"):
                            usage_seen = chunk["usage"]
                    text_out = "".join(parts)
                    if not usage_seen:
                        # llama.cpp のストリームは usage を返さない。字句器で数える（トークン上限の判定に使うため）
                        usage_seen = {
                            "prompt_tokens": _count_tokens(engine, "\n".join(m.get("content") or "" for m in messages)),
                            "completion_tokens": _count_tokens(engine, text_out),
                        }
                    resp = {"choices": [{"message": {"content": text_out}}], "usage": usage_seen}
        except Exception as exc:
            msg = str(exc)
            if "exceed" in msg or "context" in msg.lower():
                raise ProviderError(f"コンテキスト長（{self.n_ctx}）を超えました。右パネルで増やすか、ターン数を減らしてください。") from exc
            raise ProviderError(f"内蔵エンジンのエラー: {exc}") from exc

        raw = (resp["choices"][0]["message"].get("content") or "")
        usage = resp.get("usage") or {}
        text, call = parse_tool_call(raw)
        return Turn(
            text=text,
            tool_calls=[call] if call else [],
            assistant_message={"role": "assistant", "content": raw.strip() + ("</tool_call>" if call and "</tool_call>" not in raw else "")},
            usage={"input": int(usage.get("prompt_tokens", 0)), "output": int(usage.get("completion_tokens", 0))},
        )
