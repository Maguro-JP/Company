"""MCP（Streamable HTTP）の口。AdaptiveAIStudio がここに繋いで、会社を道具として使う。

AdaptiveAIStudio の MCP クライアント（src/mcp/client.ts）が話す形に合わせる:
  - POST に JSON-RPC を1つ。返事は JSON（SSE でなくてよい）
  - 通知（id なし）には 202 を返す
  - 繋ぎの名前は mcp-session-id ヘッダで渡す。DELETE で切る
  - tools/list は cursor で頁送り（こちらは1頁で全部返す）
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Request, Response

from . import controls

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "company", "version": "0.1.0"}

router = APIRouter()
_sessions: set[str] = set()


def _tools() -> list[dict[str, Any]]:
    return [
        {"name": f["name"], "description": f["description"], "inputSchema": f["parameters"]}
        for f in controls.FUNCTIONS
    ]


def _result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    """1つの JSON-RPC メッセージを処理する。通知なら None。"""
    method = message.get("method")
    req_id = message.get("id")
    params = message.get("params") or {}
    if req_id is None:
        return None  # 通知（notifications/initialized など）。返事は無い
    if method == "initialize":
        return _result(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": "AIが経営する会社を操作する道具。start_cycle / start_role で動かし、get_run で進捗を見る。",
        })
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {"tools": _tools()})
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _error(req_id, -32602, "arguments はオブジェクトで渡してください")
        out = controls.dispatch(name, args)
        is_error = "error" in out and len(out) == 1
        return _result(req_id, {
            "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=1)}],
            "isError": is_error,
        })
    return _error(req_id, -32601, f"未対応のメソッドです: {method}")


@router.post("/mcp")
async def mcp_post(request: Request) -> Response:
    try:
        payload = json.loads(await request.body() or b"null")
    except json.JSONDecodeError:
        return Response(json.dumps(_error(None, -32700, "JSON として読めません")), status_code=400, media_type="application/json")

    session = request.headers.get("mcp-session-id") or ""
    messages = payload if isinstance(payload, list) else [payload]
    if not all(isinstance(m, dict) for m in messages):
        return Response(json.dumps(_error(None, -32600, "JSON-RPC の形ではありません")), status_code=400, media_type="application/json")

    headers: dict[str, str] = {}
    if any(m.get("method") == "initialize" for m in messages):
        session = uuid.uuid4().hex
        _sessions.add(session)
    if session:
        headers["mcp-session-id"] = session

    replies = [r for r in (handle(m) for m in messages) if r is not None]
    if not replies:
        return Response(status_code=202, headers=headers)
    body = replies[0] if not isinstance(payload, list) else replies
    return Response(json.dumps(body, ensure_ascii=False), media_type="application/json", headers=headers)


@router.get("/mcp")
async def mcp_get() -> Response:
    # 相手発の流れ（サーバーからの通知）は使わない。開かせない
    return Response(status_code=405)


@router.delete("/mcp")
async def mcp_delete(request: Request) -> Response:
    _sessions.discard(request.headers.get("mcp-session-id") or "")
    return Response(status_code=204)
