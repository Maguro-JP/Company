"""同じ作者のツールどうしの連携。

Microsoft のソフトどうしのように、自作のツールは互いに呼べるのが普通。Company は
相手が得意なことを相手に任せ（台帳は NetVision、絵は CharacterDesigner、画面での AI 作業は
AdaptiveAIStudio、スキルは Ciel）、自分は会社の運営だけを持つ。境界で責任を分けるため、
相手の中身には触らず、相手が公開している口（HTTP、MCP、ファイル）だけを使う。

相手の一覧は、ここに書いた既定（公開の物）と、作業場の partners.json（非公開の物や置き場の違い）
を合わせたもの。このリポジトリは public なので、非公開のツールの名前はコードに書かず、
partners.json にだけ置く。

相手の口の種類:
  http       base_url を GET して生きているかを見る（probe）。
  mcp        MCP サーバー。url（Streamable HTTP）か command（標準入出力）。tools/list と tools/call を呼ぶ
  files      ディレクトリ。あるかどうかと中身の数だけを見る（CharacterDesigner の絵、Ciel のスキル）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import paths
from .config import ROOT
from .providers import ToolSpec

PROBE_TIMEOUT = 2.0
CALL_TIMEOUT = 120.0
MCP_PROTOCOL = "2025-06-18"

PARTNERS_FILE = ROOT / "data" / "partners.json"


def _pictures() -> str:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    return str(home / "Pictures" / "CharacterDesigner")


def _reg_install_dir(app: str) -> str:
    """同じ作者のアプリは Setup.exe が HKLM\\Software\\<App>\\InstallDir に置き場を書く。そこから探す。"""
    if sys.platform != "win32":
        return ""
    try:
        import winreg  # noqa: PLC0415
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"Software\\{app}") as key:
            return str(winreg.QueryValueEx(key, "InstallDir")[0])
    except OSError:
        return ""


def _first_existing(cands: list[str]) -> str:
    for c in cands:
        if c and Path(c).exists():
            return c
    return ""


def find_netvision_mcp() -> dict[str, Any] | None:
    """NetVision の MCP の起こし方を、ユーザーに聞かずに探す。
    配布版は NetVision-mcp.bat、開発中は backend で python -m app.mcp_server。"""
    env = os.environ
    pf = env.get("ProgramFiles", "C:\\Program Files")
    drive = (env.get("SystemDrive") or "C:") + "\\"
    roots = [env.get("NETVISION_HOME", ""), _reg_install_dir("NetVision"), str(Path(pf) / "NetVision"),
             str(Path(drive) / "NetVision"), str(Path.home() / "NetVision"), str(Path.home() / "maguro-jp" / "netvision")]
    for root in roots:
        if not root:
            continue
        bat = Path(root) / "NetVision-mcp.bat"
        if bat.exists():
            return {"command": [str(bat)], "cwd": root}
        backend = Path(root) / "backend"
        if (backend / "app" / "mcp_server.py").exists():
            py = _first_existing([str(backend / ".venv" / "Scripts" / "python.exe"), str(backend / ".venv" / "bin" / "python")]) or sys.executable
            return {"command": [py, "-m", "app.mcp_server"], "cwd": str(backend)}
    return None


def aas_installed() -> bool:
    """AdaptiveAIStudio が入っているか（置き場の鍵か、設定の置き場があるか）。"""
    return bool(_reg_install_dir("AdaptiveAIStudio")) or aas_config_path().parent.exists()


# 部門 → 部署の席。役職の AI からは <id>__<道具> で見える。部署が増えたらここに 1 行足す
PLANNED_SEATS: tuple[tuple[str, str, str, str], ...] = (
    # (id, 部門, 部署, 仕事の単位)
    ("character_design", "ビジュアル", "キャラクターデザイン", "設定画、色指定、三面図"),
    ("illustration", "ビジュアル", "作画", "チャットで頼んで描く、描き直し"),
    ("art_check", "ビジュアル", "検品（絵）", "崩れを数字で見る、通す・戻す"),
    ("sprite", "ビジュアル", "スプライト・アニメ", "ドット絵、歩き・待機のコマ"),
    ("background_art", "ビジュアル", "背景・美術", "背景、小物、舞台"),
    ("ui_art", "ビジュアル", "UI・アイコン", "アプリの画面の絵、アイコン"),
    ("storyboard", "映像", "企画・絵コンテ", "台本から絵コンテ"),
    ("video_gen", "映像", "動画生成", "絵と文から動画"),
    ("video_edit", "映像", "編集", "切る、つなぐ、テロップ"),
    ("subtitle", "映像", "字幕・翻訳", "字幕、多言語"),
    ("video_publish", "映像", "書き出し・配信", "形式、サイズ、配信先"),
    ("compose", "音響", "作曲", "曲の生成、BGM"),
    ("sfx", "音響", "効果音", "SE の生成、当て込み"),
    ("ambient", "音響", "環境音・集中音", "眠りと集中の音"),
    ("mix", "音響", "ミックス・マスタリング", "音量、整音、書き出し"),
    ("tts", "ボイス", "合成音声", "文を声にする"),
    ("voice_cast", "ボイス", "声のキャスト", "キャラクターごとの声の管理"),
    ("dubbing", "ボイス", "吹き替え・字幕合わせ", "動画に声を当てる"),
    ("transcribe", "ボイス", "文字起こし", "音声を文に"),
    ("inventory", "情報システム", "備品", "棚卸、周期"),
)


def _planned_seats() -> list[dict[str, Any]]:
    return [{"id": i, "name": f"{dept}（{div}）", "about": job, "dept": div, "planned": True}
            for i, div, dept, job in PLANNED_SEATS]


def builtin() -> list[dict[str, Any]]:
    """公開のツールの既定。置き場は自動で探す。違うときだけ partners.json で同じ id を上書きする。"""
    return [
        {
            "id": "aas", "name": "AdaptiveAIStudio", "dept": "開発",
            "about": "画面で AI に作業を頼む土台。Company を MCP として登録すると、向こうから会社を動かせる",
            "http": {"base_url": "http://127.0.0.1:4174", "probe": "/api/state"},
            "mcp": None,
            "give": "register_mcp",
        },
        {
            "id": "netvision", "name": "NetVision", "dept": "情報システム",
            "about": "ネットワーク機器の台帳。機器・拠点・保守期限を MCP で引ける（読むだけ）",
            "http": {"base_url": "http://127.0.0.1:8000", "probe": "/api/v1/dashboard/summary"},
            "mcp": find_netvision_mcp() or {"command": ["python", "-m", "app.mcp_server"], "cwd": ""},
        },
        {
            "id": "characterdesigner", "name": "CharacterDesigner", "dept": "ビジュアル",
            "about": "チャットで頼むと AI が絵を描く。描いた絵はピクチャの CharacterDesigner に出る",
            "files": {"dir": _pictures(), "glob": "*.png"},
        },
        # 部署の単位の席（docs/knowledge/creative-series.md）。相手ができたら planned を外し、検出と口を足す
        *_planned_seats(),
        {
            "id": "ciel", "name": "Ciel", "dept": "開発",
            "about": "Claude Code のスキルを作り・配る。入っているスキルは .claude/skills",
            "files": {"dir": str(ROOT / ".claude" / "skills"), "glob": "*/SKILL.md"},
        },
    ]


# ---------- 自社のツールの登録簿（自動で連結するための共有の帳面） ----------
#
# シリーズの各ツールは、起動したときに自分の口（HTTP の URL、MCP の起こし方、出来た物の置き場）を
# この 1 つのファイルに書き、他のツールの口をここから読む。ユーザーは何も設定しない。
# 置き場は Windows では %ProgramData%\Maguro\services.json（どのアプリも書ける。icacls は Setup.exe が付ける）、
# それ以外では ~/.local/share/maguro/services.json。環境変数 MAGURO_SERVICES で移せる。
# 形: {"<id>": {"name", "about", "dept", "http": {...}, "mcp": {...}, "files": {...}, "pid", "updated_at"}}

def registry_path(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get("MAGURO_SERVICES"):
        return Path(env["MAGURO_SERVICES"])
    if sys.platform == "win32":
        return Path(env.get("ProgramData") or "C:\\ProgramData") / "Maguro" / "services.json"
    return Path(env.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "maguro" / "services.json"


def read_registry(path: Path | None = None) -> dict[str, dict[str, Any]]:
    path = path or registry_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def register_self(entry: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    """自分の口を登録簿に書く（他のツールの項目には触らない）。起動のたびに呼ぶ。"""
    import time  # noqa: PLC0415
    path = path or registry_path()
    current = read_registry(path)
    current[entry["id"]] = {**current.get(entry["id"], {}), **entry, "pid": os.getpid(), "updated_at": int(time.time())}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        return {"error": f"登録簿に書けません（{path}）: {exc}"}
    return {"path": str(path), "entries": len(current)}


def company_entry(base_url: str) -> dict[str, Any]:
    """Company 自身の口。他のツールはこれを読んで Company の /mcp を使う。"""
    return {"id": "company", "name": "Company", "dept": "経営",
            "about": "AI の役職が経営する親会社。/mcp を 1 つ繋げば、つながっている全ツールの道具が使える",
            "http": {"base_url": base_url, "probe": "/api/state"}, "mcp": {"url": f"{base_url}/mcp"}}


def load(path: Path | None = None, registry: Path | None = None) -> list[dict[str, Any]]:
    """既定に、登録簿（他のツールが自分で書いた口）と partners.json を重ねる。同じ id は後が勝つ。
    置き場は呼ばれたときに見る（テストで差し替えるため。既定引数に書くと import の時に固定される）。"""
    path = path or PARTNERS_FILE
    out = {p["id"]: p for p in builtin()}
    for pid, entry in read_registry(registry).items():
        if pid == "company":
            continue  # 自分は相手ではない
        out[pid] = {**out.get(pid, {}), **entry, "id": pid, "registered": True}
    if path.exists():
        try:
            extra = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            extra = []
        for p in extra if isinstance(extra, list) else []:
            if isinstance(p, dict) and p.get("id"):
                out[p["id"]] = {**out.get(p["id"], {}), **p}
    return list(out.values())


def save_extra(entries: list[dict[str, Any]], path: Path | None = None) -> None:
    path = path or PARTNERS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def get(partner_id: str) -> dict[str, Any] | None:
    return next((p for p in load() if p["id"] == partner_id), None)


# ---------- 疎通 ----------

def _http_get(url: str, timeout: float = PROBE_TIMEOUT) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def probe(partner: dict[str, Any]) -> dict[str, Any]:
    """生きているかを 1 回だけ見る。待つのは短く（画面の一覧で全部を見るため）。"""
    out: dict[str, Any] = {"id": partner["id"], "name": partner.get("name", partner["id"]),
                           "about": partner.get("about", ""), "alive": False, "detail": "", "tools": 0,
                           "give": partner.get("give", ""), "planned": bool(partner.get("planned")),
                           "dept": partner.get("dept", ""), "registered": bool(partner.get("registered"))}
    if out["planned"]:
        out["detail"] = "まだ無い（予定）"
        return out
    http = partner.get("http")
    files = partner.get("files")
    mcp = partner.get("mcp")
    if http and http.get("base_url"):
        url = http["base_url"].rstrip("/") + (http.get("probe") or "/")
        try:
            status, _ = _http_get(url)
            out["alive"] = status < 500
            out["detail"] = f"HTTP {status} {url}"
        except urllib.error.HTTPError as exc:
            out["alive"] = exc.code < 500
            out["detail"] = f"HTTP {exc.code} {url}"
        except (urllib.error.URLError, OSError) as exc:
            out["detail"] = f"つながりません（{url}）: {getattr(exc, 'reason', exc)}。起動しているか確かめてください"
    if files and files.get("dir"):
        d = Path(files["dir"])
        if d.is_dir():
            n = len(list(d.glob(files.get("glob") or "*")))
            out["alive"] = True
            out["detail"] = (out["detail"] + " / " if out["detail"] else "") + f"{d}（{n} 件）"
            out["files"] = n
        else:
            out["detail"] = (out["detail"] + " / " if out["detail"] else "") + f"ディレクトリがありません: {d}"
    if mcp and (mcp.get("url") or (mcp.get("command") and mcp.get("cwd"))):
        try:
            tools = list_tools(partner)
            out["tools"] = len(tools)
            out["tool_names"] = [t["name"] for t in tools]
            out["alive"] = True
        except PartnerError as exc:
            out["mcp_error"] = str(exc)
    elif mcp and mcp.get("command") and not mcp.get("cwd"):
        out["mcp_error"] = "入っている場所が見つかりません（Setup.exe で入れた物、C:\\NetVision、Program Files を探しました）"
    return out


# ---------- MCP の呼び手 ----------

class PartnerError(Exception):
    pass


class McpClient:
    """相手の MCP サーバーを 1 回の用事（初期化、一覧か呼び出し）で使う。常駐させない。

    常駐させないのは、相手を Company の寿命に巻き込まないため。NetVision の MCP は呼ばれた
    ときだけ動く作りで、こちらもそれに合わせる。"""

    def __init__(self, spec: dict[str, Any]):
        self.spec = spec
        self._id = 0
        self._proc: subprocess.Popen | None = None
        self._session = ""

    def __enter__(self) -> "McpClient":
        if not self.spec.get("url"):
            cmd = self.spec.get("command")
            if not cmd:
                raise PartnerError("MCP の url か command がありません")
            try:
                self._proc = subprocess.Popen(
                    cmd if isinstance(cmd, list) else str(cmd), shell=not isinstance(cmd, list),
                    cwd=self.spec.get("cwd") or None, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, encoding="utf-8",
                    env={**os.environ, "PYTHONUTF8": "1", **(self.spec.get("env") or {})},
                )
            except OSError as exc:
                raise PartnerError(f"MCP を起こせません（{cmd}）: {exc}") from exc
        init = self._request("initialize", {"protocolVersion": MCP_PROTOCOL, "capabilities": {},
                                            "clientInfo": {"name": "company", "version": "0.2"}})
        self._notify("notifications/initialized")
        self.server_info = init.get("serverInfo", {})
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._proc:
            try:
                self._proc.stdin.close()  # type: ignore[union-attr]
                self._proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                self._proc.kill()
                self._proc.wait(timeout=5)
            for pipe in (self._proc.stdout, self._proc.stderr):
                if pipe:
                    pipe.close()

    def _send(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if self._proc:
            assert self._proc.stdin and self._proc.stdout
            try:
                self._proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise PartnerError(f"MCP に書けません: {exc}") from exc
            if "id" not in message:
                return None
            line = self._proc.stdout.readline()
            if not line:
                err = (self._proc.stderr.read() if self._proc.stderr else "")[-800:]
                raise PartnerError(f"MCP が答えずに終わりました: {err.strip() or '（標準エラーは空）'}")
            try:
                return json.loads(line)
            except json.JSONDecodeError as exc:
                raise PartnerError(f"MCP の返事が JSON ではありません: {line[:200]}") from exc
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   **(self.spec.get("headers") or {})}
        if self._session:
            headers["mcp-session-id"] = self._session
        req = urllib.request.Request(self.spec["url"], data=json.dumps(message).encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=CALL_TIMEOUT) as resp:
                self._session = resp.headers.get("mcp-session-id") or self._session
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise PartnerError(f"MCP が HTTP {exc.code} を返しました: {exc.read()[:200]!r}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise PartnerError(f"MCP につながりません（{self.spec['url']}）: {getattr(exc, 'reason', exc)}") from exc
        if "id" not in message or not body:
            return None
        if body.lstrip().startswith("event:") or body.lstrip().startswith("data:"):
            # SSE で返す相手。data: の行を拾う
            for raw in body.splitlines():
                if raw.startswith("data:"):
                    body = raw[5:].strip()
                    break
        return json.loads(body)

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._id += 1
        reply = self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        if reply is None:
            raise PartnerError(f"{method} に返事がありません")
        if "error" in reply:
            err = reply["error"]
            raise PartnerError(f"{method} が断られました: {err.get('message', err)}")
        return reply.get("result") or {}

    def _notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def list_tools(self) -> list[dict[str, Any]]:
        return list(self._request("tools/list", {}).get("tools") or [])

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return self._request("tools/call", {"name": name, "arguments": args})


def list_tools(partner: dict[str, Any]) -> list[dict[str, Any]]:
    with McpClient(partner["mcp"]) as c:
        return c.list_tools()


def call_tool(partner: dict[str, Any], name: str, args: dict[str, Any]) -> dict[str, Any]:
    with McpClient(partner["mcp"]) as c:
        return c.call(name, args)


def _text_of(result: dict[str, Any]) -> str:
    parts = [c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text"]
    text = "\n".join(p for p in parts if p) or json.dumps(result, ensure_ascii=False)
    return ("（相手が失敗と言っています）\n" + text) if result.get("isError") else text


# ---------- 役職の AI に渡す道具 ----------

def toolset() -> dict[str, tuple[ToolSpec, Any]]:
    """つながっている相手の MCP の道具を、役職の AI の道具にする。名前は <相手>__<道具>。

    一覧が取れない相手は黙って外す（相手が落ちていても会社は回る）。"""
    out: dict[str, tuple[ToolSpec, Any]] = {}
    for partner in load():
        mcp = partner.get("mcp")
        if not mcp or not (mcp.get("url") or (mcp.get("command") and mcp.get("cwd"))):
            continue
        try:
            tools = list_tools(partner)
        except PartnerError:
            continue
        for t in tools:
            full = f"{partner['id']}__{t['name']}"
            spec = ToolSpec(name=full, description=f"[{partner.get('name', partner['id'])}] {t.get('description', '')}",
                            parameters=t.get("inputSchema") or {"type": "object", "properties": {}})

            def fn(args: dict[str, Any], _p=partner, _n=t["name"]) -> str:
                try:
                    return _text_of(call_tool(_p, _n, args))
                except PartnerError as exc:
                    return f"{_p.get('name', _p['id'])} の {_n} が失敗しました: {exc}"

            out[full] = (spec, fn)
    return out


# ---------- AdaptiveAIStudio に Company を登録する ----------

def aas_config_path(env: dict[str, str] | None = None) -> Path:
    """AdaptiveAIStudio の設定（aas.config.json）。AAS_CONFIG > AAS_HOME/aas.config.json > ~/.aas/aas.config.json。
    向こうの src/config.ts の探索順と同じ。"""
    env = os.environ if env is None else env
    if env.get("AAS_CONFIG"):
        return Path(env["AAS_CONFIG"])
    home = Path(env["AAS_HOME"]) if env.get("AAS_HOME") else Path(env.get("USERPROFILE") or Path.home()) / ".aas"
    return home / "aas.config.json"


def register_with_aas(company_mcp_url: str, config_path: Path | None = None) -> dict[str, Any]:
    """aas.config.json の mcp.servers.company に Company の /mcp を書く。他の項目には触らない。
    AAS は次に立ち上がったとき（か画面の「繋ぎ直す」）に読む。"""
    path = config_path or aas_config_path()
    current: dict[str, Any] = {}
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as exc:
            return {"error": f"{path} が JSON として読めません: {exc}。手で直してから、もう一度押してください"}
        if not isinstance(current, dict):
            return {"error": f"{path} の先頭がオブジェクトではありません"}
    mcp = current.get("mcp") if isinstance(current.get("mcp"), dict) else {}
    servers = mcp.get("servers") if isinstance(mcp.get("servers"), dict) else {}
    before = servers.get("company")
    servers = {**servers, "company": {**(before if isinstance(before, dict) else {}), "url": company_mcp_url, "enabled": True}}
    current = {**current, "mcp": {**mcp, "servers": servers}}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        return {"error": f"{path} に書けません: {exc}"}
    return {"path": str(path), "url": company_mcp_url, "changed": before != servers["company"]}


def auto_register_aas(company_mcp_url: str) -> dict[str, Any] | None:
    """起動のたびに、AdaptiveAIStudio が入っていれば黙って Company を登録する。ユーザーにボタンを押させない。
    入っていなければ何もしない（設定の置き場を勝手に作らない）。"""
    if not aas_installed():
        return None
    return register_with_aas(company_mcp_url)
