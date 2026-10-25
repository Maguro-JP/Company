"""HTTPサーバー。UIを配信し、実行を開始し、進捗をSSEで流す。

ブラウザからも、pywebview のウィンドウからも同じものを見る。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import controls, gemini, local_llm, mcp_http, runner
from .config import ROOT, load_config
from .providers import ProviderError, make_provider

UI_DIR = ROOT / "ui"

app = FastAPI(title="Company", docs_url=None, redoc_url=None)
app.include_router(mcp_http.router)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


@app.get("/api/state")
def state() -> dict[str, Any]:
    config = load_config()
    return {
        "company": config.company,
        "autonomy": config.autonomy,
        "cadences": {k: v for k, v in config.raw.get("cadence", {}).items()},
        "roles": runner.list_roles(),
        "settings": runner.load_settings(),
        "runs": [r.summary() for r in runner.REGISTRY.all()],
    }


class SettingsPatch(BaseModel):
    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    temperature: float | None = None
    max_turns: int | None = None
    max_tokens_per_run: int | None = None
    context: int | None = None
    gpu_layers: int | None = None


@app.post("/api/settings")
def update_settings(patch: SettingsPatch) -> dict[str, Any]:
    return runner.save_settings(patch.model_dump(exclude_none=True))


@app.get("/api/models")
def models(provider: str, base_url: str = "", api_key: str = "") -> dict[str, Any]:
    if provider == "builtin":
        return {"models": [m["id"] for m in local_llm.list_models() if m["downloaded"]],
                "catalog": local_llm.list_models()}
    try:
        # モデル名は一覧取得に不要なのでダミーを入れる
        p = make_provider({"provider": provider, "base_url": base_url, "api_key": api_key, "model": "-"})
        return {"models": p.list_models()}
    except ProviderError as exc:
        return {"models": [], "error": str(exc)}


class DownloadRequest(BaseModel):
    model_id: str


@app.post("/api/download")
def download(req: DownloadRequest) -> dict[str, Any]:
    try:
        return local_llm.DOWNLOADS.start(req.model_id).as_dict()
    except ProviderError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/download/{model_id}")
def download_status(model_id: str) -> dict[str, Any]:
    d = local_llm.DOWNLOADS._active.get(model_id)
    if d is None:
        raise HTTPException(404, "ダウンロード中ではありません")
    return d.as_dict()


class StartRequest(BaseModel):
    kind: str            # cycle | role
    target: str          # cadence 名 or 役職名
    instruction: str | None = None


@app.post("/api/runs")
def start(req: StartRequest) -> dict[str, Any]:
    try:
        if req.kind == "cycle":
            run = runner.start_cycle(req.target, req.instruction)
        elif req.kind == "role":
            run = runner.start_role(req.target, req.instruction)
        else:
            raise HTTPException(400, f"kind は cycle か role: {req.kind}")
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return run.summary()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run = runner.REGISTRY.get(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return {**run.summary(), "events": run.events}


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: str) -> dict[str, Any]:
    run = runner.REGISTRY.get(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    run.stop.set()
    return run.summary()


@app.get("/api/runs/{run_id}/events")
def events(run_id: str) -> StreamingResponse:
    run = runner.REGISTRY.get(run_id)
    if run is None:
        raise HTTPException(404, "run not found")

    def stream():
        index = 0
        while True:
            batch = run.wait_for(index, timeout=15)
            for event in batch:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            index += len(batch)
            if run.status != "running" and index >= len(run.events):
                yield f"event: end\ndata: {json.dumps(run.summary())}\n\n"
                return
            if not batch:
                yield ": keepalive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/files/{path:path}")
def read_repo_file(path: str) -> dict[str, Any]:
    target = (ROOT / path).resolve()
    try:
        target.relative_to(ROOT)
    except ValueError:
        raise HTTPException(403, "リポジトリの外です")
    if not target.is_file():
        raise HTTPException(404, "not found")
    return {"path": path, "content": target.read_text(encoding="utf-8", errors="replace")}


# --------------------------------------------------------------------------- #
# AI Studio / Gemini 側から会社を操作するための入口
# --------------------------------------------------------------------------- #


@app.get("/api/gemini/functions")
def gemini_functions() -> dict[str, Any]:
    """Gemini API の tools にそのまま渡せる関数定義。AI Studio の Function calling 設定にも貼れる。"""
    return gemini.company_tool_declarations()


class FunctionCall(BaseModel):
    name: str
    args: dict[str, Any] = {}


@app.post("/api/gemini/call")
def gemini_call(call: FunctionCall) -> dict[str, Any]:
    """関数名と引数で会社を操作する。Gemini の functionCall をそのまま中継できる。"""
    return controls.dispatch(call.name, call.args)
