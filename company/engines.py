"""手元で動いている AI のエンジン（Strata、Ollama、LM Studio、AdaptiveAIStudio）を見つける。

大きい模型は内蔵エンジンより、GPU と CPU に分けて動かすエンジン（Strata）に任せた方が動く。ユーザーに URL を
書かせず、決まった口（localhost の既定の番号）を起動時と設定の画面で叩き、動いていれば「使う」を 1 回押すだけで選べるようにする。
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .providers import NATIVE, PRESETS

PROBE_TIMEOUT = 0.8


def candidates() -> list[dict[str, Any]]:
    out = []
    for entry in [*PRESETS, *NATIVE]:
        if entry.get("local") and entry.get("base_url") and entry["id"] != "builtin":
            out.append({"id": entry["id"], "label": entry["label"], "base_url": entry["base_url"]})
    return out


def _models_url(entry: dict[str, Any]) -> str:
    base = entry["base_url"].rstrip("/")
    return f"{base}/v1/models" if entry["id"] == "aas" else f"{base}/models"


def probe(entry: dict[str, Any], timeout: float = PROBE_TIMEOUT) -> dict[str, Any]:
    out = {**entry, "alive": False, "models": []}
    try:
        req = urllib.request.Request(_models_url(entry), headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
        items = data.get("data") if isinstance(data, dict) else data
        if isinstance(items, list):
            out["models"] = [str(m.get("id") or m.get("name") or m) if isinstance(m, dict) else str(m) for m in items][:50]
        out["alive"] = True
    except (urllib.error.URLError, socket.timeout, OSError, ValueError):
        pass
    return out


def detect(timeout: float = PROBE_TIMEOUT) -> list[dict[str, Any]]:
    """動いているエンジンだけを返す。並列に叩くので全部で 1 秒以内。"""
    cands = candidates()
    with ThreadPoolExecutor(max_workers=max(1, len(cands))) as pool:
        results = list(pool.map(lambda e: probe(e, timeout), cands))
    return [r for r in results if r["alive"]]
