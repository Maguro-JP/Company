"""HTTPサーバー。UIを配信し、実行を開始し、進捗をSSEで流す。

ブラウザからも、pywebview のウィンドウからも同じものを見る。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import controls, gemini, local_llm, mcp_http, partners, runner, updater
from . import paths
from .config import ROOT, load_config
from .providers import ProviderError, answer_check, make_provider, provider_catalog

UI_DIR = paths.APP_ROOT / "ui"   # 画面はプログラム側。作業場（ROOT）ではない

app = FastAPI(title="Company", docs_url=None, redoc_url=None)
app.include_router(mcp_http.router)
# キャラクターの絵とアイコン。画面は依存なしの 1 ファイルだが、絵だけは別に置く（HTML に埋めると 1 MB を超える）
app.mount("/characters", StaticFiles(directory=UI_DIR / "characters"), name="characters")
app.mount("/icon", StaticFiles(directory=UI_DIR / "icon"), name="icon")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


@app.get("/api/state")
def state() -> dict[str, Any]:
    config = load_config()
    return {
        "version": updater.current(),
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
    roles: dict[str, dict[str, Any]] | None = None
    webhook_url: str | None = None
    notify_on: str | None = None
    context: int | None = None
    gpu_layers: int | None = None


@app.post("/api/settings")
def update_settings(patch: SettingsPatch) -> dict[str, Any]:
    return runner.save_settings(patch.model_dump(exclude_none=True))


@app.get("/api/providers")
def providers() -> dict[str, Any]:
    return {"providers": provider_catalog()}


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


class CheckRequest(BaseModel):
    provider: str
    model: str
    base_url: str = ""
    api_key: str = ""
    context: int | None = None
    gpu_layers: int | None = None
    role: str | None = None   # 役職の上書きで確かめるとき


@app.post("/api/check")
def check(req: CheckRequest) -> dict[str, Any]:
    """選んだ AI に 1 問送って、本当に答えるかを見る。設定の正しさはこれでしか分からない。"""
    settings = {**runner.load_settings(), **req.model_dump(exclude_none=True)}
    if req.role:
        settings = runner.settings_for_role(settings, req.role)
    try:
        return answer_check(settings)
    except ProviderError as exc:
        return {"error": str(exc), "kind": exc.kind}


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
        target.relative_to(ROOT.resolve())
    except ValueError:
        raise HTTPException(403, "リポジトリの外です")
    if not target.is_file():
        raise HTTPException(404, "not found")
    return {"path": path, "content": target.read_text(encoding="utf-8", errors="replace")}


# --------------------------------------------------------------------------- #
# アプリ本体の更新（CharacterDesigner の「更新を確かめる」と同じ）
# --------------------------------------------------------------------------- #


@app.get("/api/update")
def update_check(quiet: bool = True) -> dict[str, Any]:
    """新しい版が出ていないか見るだけ。quiet=false はユーザーが押したときで、確かめられない理由も返す。"""
    try:
        found = updater.check(quiet=quiet)
    except Exception as exc:
        return {"current": updater.current(), "error": f"確かめられませんでした: {exc}"}
    return {"current": updater.current(), "newer": found}


@app.post("/api/update")
def update_apply() -> dict[str, Any]:
    """新しい Setup.exe を取って起こす。入れるかどうかはその案内でユーザーが決める。"""
    if any(r.status == "running" for r in runner.REGISTRY.all()):
        raise HTTPException(409, "いま実行中です。終わってから更新してください")
    try:
        found = updater.check(quiet=False)
    except Exception as exc:
        raise HTTPException(502, f"確かめられませんでした: {exc}") from exc
    if not found or not found.get("setup"):
        return {"current": updater.current(), "newer": None}
    try:
        path = updater.download(found["setup"])
        updater.launch(path)
    except Exception as exc:
        raise HTTPException(500, f"更新を取れませんでした: {exc}") from exc
    return {"current": updater.current(), "newer": found, "setup": str(path)}


# --------------------------------------------------------------------------- #
# AI Studio / Gemini 側から会社を操作するための入口
# --------------------------------------------------------------------------- #


# ---------- 同じ作者のツールとの連携 ----------

@app.get("/api/partners")
def partners_list() -> dict[str, Any]:
    """相手ごとに生きているかを短く見る。落ちている相手は理由つきで出す。"""
    return {"partners": [partners.probe(p) for p in partners.load()],
            "file": str(partners.PARTNERS_FILE)}


@app.get("/api/partners/{partner_id}/tools")
def partner_tools(partner_id: str) -> dict[str, Any]:
    p = partners.get(partner_id)
    if p is None:
        raise HTTPException(404, f"相手がありません: {partner_id}")
    if not p.get("mcp"):
        return {"tools": [], "note": "この相手は MCP の口を持ちません"}
    try:
        return {"tools": partners.list_tools(p)}
    except partners.PartnerError as exc:
        return {"tools": [], "error": str(exc)}


class PartnerCall(BaseModel):
    tool: str
    args: dict[str, Any] = {}


@app.post("/api/partners/{partner_id}/call")
def partner_call(partner_id: str, req: PartnerCall) -> dict[str, Any]:
    p = partners.get(partner_id)
    if p is None or not p.get("mcp"):
        raise HTTPException(404, f"MCP の口を持つ相手がありません: {partner_id}")
    try:
        return partners.call_tool(p, req.tool, req.args)
    except partners.PartnerError as exc:
        return {"error": str(exc)}


@app.post("/api/partners/aas/register")
def partner_aas_register(request: Request) -> dict[str, Any]:
    """AdaptiveAIStudio の設定に、この Company の /mcp を書く。向こうから会社を動かせるようにする。"""
    base = str(request.base_url).rstrip("/")
    return partners.register_with_aas(f"{base}/mcp")


class PartnersPatch(BaseModel):
    entries: list[dict[str, Any]]


@app.post("/api/partners")
def partners_save(patch: PartnersPatch) -> dict[str, Any]:
    """partners.json を丸ごと置き換える（非公開のツールや置き場の違いはここに書く）。"""
    partners.save_extra(patch.entries)
    return {"file": str(partners.PARTNERS_FILE), "count": len(patch.entries)}


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
