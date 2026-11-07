"""役職の AI の働き方を、Ciel のスキル（.claude/skills）から組み立てる。

オーナーは Ciel で「理想の働き方」をスキルとして育てている。会社の役職もその働き方で動くべきで、
役職ごとの指示（agents/*.md）とは別に、毎回の指示の先頭に同じ決まりを差し込む。
スキルの本文は長いので、要る物だけを順に、大きさの上限つきで入れる。全文は作業場の
.claude/skills/<name>/SKILL.md にあり、役職は read_file で読める。

失敗の記録（lessons の LESSONS.md）は「次から」の行だけを差し込む。人が読むときと同じ形。
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import ROOT

SKILLS_DIR = ROOT / ".claude" / "skills"

# 役職の AI に効く順。前が優先。開発の手順（PR、CI、配布）は役職の道具に無いので入れない
CORE_SKILLS = (
    "work-style",          # 確認してから動く、1 つずつ、動かしてから完了、確かめていないと言う
    "follow-instructions", # オーナーの指示に反抗しない
    "finish-first",        # 完成条件を先に書き、1 つずつ完成させる
    "verify-before-done",  # 動かした出力で完了を判定する
    "token-efficiency",    # 読む前に探す、要る範囲だけ読む
    "ja-proofread",        # 日本語の書き方
    "user-terms",          # オーナーの語で書く
    "tidy",                # 散らかさない
)
PER_SKILL_CHARS = 1400
LESSONS_LINES = 12


def _body(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    # frontmatter（--- ... ---）を外す
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    return text.strip()


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # 行の途中で切らない
    nl = cut.rfind("\n")
    return (cut[:nl] if nl > limit // 2 else cut) + "\n（続きは SKILL.md）"


def skill_digest(name: str, skills_dir: Path = SKILLS_DIR) -> str | None:
    path = skills_dir / name / "SKILL.md"
    if not path.is_file():
        return None
    return _clip(_body(path), PER_SKILL_CHARS)


def lessons_next(skills_dir: Path = SKILLS_DIR, limit: int = LESSONS_LINES) -> list[str]:
    """LESSONS.md の「次から:」の行。新しい物（末尾）から。"""
    path = skills_dir / "lessons" / "LESSONS.md"
    if not path.is_file():
        return []
    lines = [m.group(1).strip() for m in re.finditer(r"^次から:\s*(.+)$", path.read_text(encoding="utf-8", errors="replace"), re.M)]
    return list(reversed(lines))[:limit]


def user_decisions(root: Path = ROOT, limit: int = 40) -> list[str]:
    """CLAUDE.md の「ユーザーの決めたこと」。オーナーが訂正して決めた働き方で、スキルより上。"""
    path = root / "CLAUDE.md"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^## ユーザーの決めたこと\n(.*?)(?=^## )", text, re.M | re.S)
    if not m:
        return []
    items = [ln[2:].strip() for ln in m.group(1).splitlines() if ln.startswith("- ")]
    return items[:limit]


def budget_for(context: int) -> int:
    """模型のコンテキスト長に合わせた文字数。小さい模型（4096）では決めたことと work-style だけが入る。"""
    if context <= 4096:
        return 1800
    if context <= 8192:
        return 5000
    return 12000


def build(skills_dir: Path = SKILLS_DIR, root: Path = ROOT, max_chars: int = 12000) -> str:
    """毎回の指示の先頭に入れる「働き方」。上限の文字数まで、優先の高い順に入れる。
    スキルが無い作業場では短い既定だけを返す。"""
    lines = ["# 働き方（オーナーが Ciel で決めたもの。役職の指示より上）", ""]
    used = 0

    def room(text: str) -> bool:
        nonlocal used
        if used + len(text) > max_chars:
            return False
        used += len(text)
        return True

    decisions = [d if len(d) <= 160 else d[:160] + "…" for d in user_decisions(root)]
    if decisions:
        block = "\n".join(f"- {d}" for d in decisions)
        if not room(block):
            block = "\n".join(f"- {d}" for d in decisions[:8])
            room(block)
        lines += ["## オーナーが決めたこと", block, ""]
    nexts = lessons_next(skills_dir)
    if nexts:
        block = "\n".join(f"- {n}" for n in nexts)
        if room(block):
            lines += ["## 失敗の記録（次から）", block, ""]
    found = 0
    for name in CORE_SKILLS:
        digest = skill_digest(name, skills_dir)
        if digest is None or not room(digest):
            continue
        found += 1
        lines += [f"## {name}", digest, ""]
    if found == 0 and not decisions:
        lines += [
            "- 確認してから動く。1 つずつやる。動かしてから完了と言う。確かめていないことは確かめていないと言う",
            "- 完成条件を先に docs/reports/deliverables.md に書き、未完成があるうちは新しい成果物を始めない",
            "- 日本語で、です・ます体で、オーナーの語で書く",
            "",
        ]
    return "\n".join(lines)
