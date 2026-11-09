"""オーナーのリポジトリを子会社として取り込み、業種ごとに束ねる。

Company は「現実の仕事は全部ツール化して AI 化して会社化できる」という前提で、オーナーの
リポジトリ 1 つを子会社 1 つとして扱う。GitHub からリポジトリの一覧を取り、名前・説明・topics
から業種を当て、作業場の data/org.generated.yaml に子会社の木を書く。手で書いた org.yaml
（公開のツール）と合わせて 1 本の木にする。

生成した木を org.yaml に混ぜないのは、このリポジトリが public で、非公開のリポジトリの名前を
コードや公開の設定に残せないため。生成分は作業場の data/ にだけ置く。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .config import DATA_DIR

GENERATED_PATH = DATA_DIR / "org.generated.yaml"
CACHE_PATH = DATA_DIR / "repos.json"
API = "https://api.github.com"

# 業種の当て方。上から順に、名前・説明・topics の語で決める。当たらなければ「その他」
INDUSTRIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ゲーム", ("game", "ゲーム", "play", "puzzle", "rpg", "dungeon", "将棋", "chess")),
    ("クリエイティブ", ("character", "draw", "illust", "design", "sprite", "art", "sound", "music", "voice", "video", "映像", "絵", "image")),
    ("メディア", ("news", "portfolio", "site", "blog", "sns", "summary", "translat", "typing", "learning", "study", "movie", "mock")),
    ("金融", ("fx", "trade", "finance", "invest", "株", "為替", "money", "stock")),
    ("経営", ("company", "会社", "inventory", "棚卸", "management", "crm", "erp")),
    ("開発基盤", ("studio", "skill", "agent", "template", "llm", "idea", "os", "boilerplate", "starter", "default")),
    ("インフラ", ("network", "nms", "vision", "weather", "typhoon", "monitor", "server", "iot")),
)


def classify(name: str, description: str = "", topics: list[str] | None = None) -> str:
    hay = " ".join([name, description or "", " ".join(topics or [])]).lower()
    for industry, words in INDUSTRIES:
        if any(w in hay for w in words):
            return industry
    return "その他"


def _get(url: str, token: str = "") -> list[dict[str, Any]]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "company-app"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch(owner: str, token: str = "") -> list[dict[str, Any]]:
    """GitHub からリポジトリの一覧。トークンがあれば非公開も含む（/user/repos）。無ければ公開だけ。"""
    out: list[dict[str, Any]] = []
    page = 1
    while True:
        url = (f"{API}/user/repos?affiliation=owner&per_page=100&page={page}" if token
               else f"{API}/users/{owner}/repos?per_page=100&page={page}")
        try:
            batch = _get(url, token)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"GitHub が HTTP {exc.code} を返しました（{url}）") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(f"GitHub につながりません: {getattr(exc, 'reason', exc)}") from exc
        if not batch:
            break
        for r in batch:
            out.append({
                "name": r.get("name", ""), "full_name": r.get("full_name", ""),
                "private": bool(r.get("private")), "archived": bool(r.get("archived")), "fork": bool(r.get("fork")),
                "description": r.get("description") or "", "topics": r.get("topics") or [],
                "pushed_at": r.get("pushed_at") or "", "html_url": r.get("html_url", ""),
                "language": r.get("language") or "",
            })
        if len(batch) < 100:
            break
        page += 1
    return out


def save_cache(repos: list[dict[str, Any]], path: Path | None = None) -> None:
    path = path or CACHE_PATH  # 置き場は呼ばれたときに見る（テストで差し替えるため）
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "repos": repos},
                               ensure_ascii=False, indent=1), encoding="utf-8")


def load_cache(path: Path | None = None) -> dict[str, Any]:
    path = path or CACHE_PATH
    if not path.exists():
        return {"fetched_at": "", "repos": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"fetched_at": "", "repos": []}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def build_tree(repos: list[dict[str, Any]], manual_names: set[str]) -> list[dict[str, Any]]:
    """業種ごとの部門の下に、リポジトリを子会社として並べる。手で書いた子会社（manual_names）は入れない。

    形は 親会社 → 業種（division）→ 子会社（company）。org.yaml の手書きの子会社は親会社の直下に残る。"""
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in repos:
        if r.get("archived") or r.get("fork"):
            continue
        if r["name"].lower() in {m.lower() for m in manual_names}:
            continue
        industry = classify(r["name"], r.get("description", ""), r.get("topics"))
        node = {
            "name": r["name"], "kind": "company", "repo": r.get("full_name", r["name"]),
            "about": (r.get("description") or "（説明なし）"), "private": bool(r.get("private")),
            "pushed_at": r.get("pushed_at", ""), "industry": industry, "seats": [], "children": [],
        }
        groups.setdefault(industry, []).append(node)
    order = [i for i, _ in INDUSTRIES] + ["その他"]
    out = []
    for industry in order:
        if industry not in groups:
            continue
        members = sorted(groups[industry], key=lambda n: n.get("pushed_at", ""), reverse=True)
        out.append({"name": f"{industry}グループ", "kind": "division", "about": f"業種で束ねた子会社（{len(members)}）",
                    "generated": True, "children": members})
    return out


def write_generated(nodes: list[dict[str, Any]], path: Path | None = None) -> None:
    path = path or GENERATED_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"children": nodes}, allow_unicode=True, sort_keys=False), encoding="utf-8")


def load_generated(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or GENERATED_PATH
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("children") or []) if isinstance(data, dict) else []


def sync(owner: str, token: str = "", manual_names: set[str] | None = None) -> dict[str, Any]:
    repos = fetch(owner, token)
    save_cache(repos)
    nodes = build_tree(repos, manual_names or set())
    write_generated(nodes)
    counts = {n["name"]: len(n["children"]) for n in nodes}
    return {"repos": len(repos), "subsidiaries": sum(counts.values()), "groups": counts,
            "private_included": bool(token), "file": str(GENERATED_PATH)}


def stale(repos: list[dict[str, Any]], days: int = 90) -> list[str]:
    """この日数より長く push の無いリポジトリ。助言の材料。"""
    now = datetime.now(timezone.utc)
    out = []
    for r in repos:
        if r.get("archived") or r.get("fork") or not r.get("pushed_at"):
            continue
        try:
            pushed = datetime.fromisoformat(r["pushed_at"].replace("Z", "+00:00"))
        except ValueError:
            continue
        if (now - pushed).days > days:
            out.append(r["name"])
    return out


def owner_from_env() -> str:
    return os.environ.get("COMPANY_GITHUB_OWNER", "") or "Maguro-JP"


def token_from_env() -> str:
    return os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", "")
