"""会社の形（親会社 → 子会社 → 部門 → 部署 → 課）。

親会社は Company（AI の役職が経営する）、子会社はオーナーの各ツール、部門はツールの機能別、
その下は機能・道具（partners の席 = seats）・作業用エージェント（roles は Company の役職、agents は相手側の担当）。
Company は、どの課がどのツールの口を持つかをこの木で管理する。実際にツールを作るのはオーナーで、
Company は「席」を持っておき、相手ができたら口を当てる。

階層を固定の 5 段にしているのは、ツールが増えたときに「どこに置くか」を迷わせないため。
段を飛ばした書き方（部門の直下に課）も読めるが、画面の出し方は段で決める。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config import ROOT

ORG_PATH = ROOT / "org.yaml"
KINDS = ("holding", "company", "division", "department", "section")
KIND_LABEL = {"holding": "親会社", "company": "子会社", "division": "部門", "department": "部署", "section": "課"}


def load(path: Path = ORG_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"name": "Company", "kind": "company", "children": []}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {"name": "Company", "kind": "company", "children": []}


def save(tree: dict[str, Any], path: Path = ORG_PATH) -> None:
    path.write_text(yaml.safe_dump(tree, allow_unicode=True, sort_keys=False), encoding="utf-8")


def walk(node: dict[str, Any], trail: tuple[str, ...] = ()):
    """(道, 節) を上から順に。道は名前の並び。"""
    here = (*trail, str(node.get("name", "")))
    yield here, node
    for child in node.get("children") or []:
        if isinstance(child, dict):
            yield from walk(child, here)


def sections(tree: dict[str, Any]) -> list[tuple[tuple[str, ...], dict[str, Any]]]:
    return [(trail, n) for trail, n in walk(tree) if n.get("kind") == "section"]


def seat_index(tree: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """席の id → その席がある課の道。"""
    out: dict[str, tuple[str, ...]] = {}
    for trail, node in sections(tree):
        for seat in node.get("seats") or []:
            out[str(seat)] = trail
    return out


def validate(tree: dict[str, Any], known_seats: set[str], known_roles: set[str]) -> list[str]:
    """木の誤り。kind が 5 段のどれか、席と役職が実在するか、同じ席が 2 つの課に無いか。"""
    errors: list[str] = []
    seen: dict[str, tuple[str, ...]] = {}
    for trail, node in walk(tree):
        kind = node.get("kind")
        if kind not in KINDS:
            errors.append(f"{'/'.join(trail)}: kind が {KINDS} のどれでもありません: {kind!r}")
        for seat in node.get("seats") or []:
            if str(seat) not in known_seats:
                errors.append(f"{'/'.join(trail)}: 席 {seat} が partners にありません")
            if str(seat) in seen:
                errors.append(f"{'/'.join(trail)}: 席 {seat} は {'/'.join(seen[str(seat)])} にもあります")
            seen[str(seat)] = trail
        for role in node.get("roles") or []:
            if str(role) not in known_roles:
                errors.append(f"{'/'.join(trail)}: 役職 {role} が agents にありません")
    return errors


def annotate(tree: dict[str, Any], probes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """画面用。各節に、下にある席の数・つながった数・予定の数を足した写しを返す。元は変えない。"""
    def rec(node: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in node.items() if k != "children"}
        out["label"] = KIND_LABEL.get(str(node.get("kind")), str(node.get("kind")))
        out["children"] = [rec(c) for c in node.get("children") or [] if isinstance(c, dict)]
        seats = [probes.get(str(s)) for s in node.get("seats") or []]
        out["seat_states"] = [s for s in seats if s]
        total = len(out["seat_states"]) + sum(c["total"] for c in out["children"])
        alive = sum(1 for s in out["seat_states"] if s.get("alive")) + sum(c["alive"] for c in out["children"])
        planned = sum(1 for s in out["seat_states"] if s.get("planned")) + sum(c["planned"] for c in out["children"])
        out.update({"total": total, "alive": alive, "planned": planned})
        return out
    return rec(tree)
