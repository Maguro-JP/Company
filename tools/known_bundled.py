"""同梱物（作業場に写す物）の、これまで配った全部の版のハッシュを company/known_bundled.txt に書く。

    python tools/known_bundled.py

更新のとき、作業場の物がこの表にあれば「ユーザーが直していない」と分かるので、新しい同梱物で上書きできる。
表に無い物はユーザーの手が入っているので触らない。コミットの前に 1 回走らせる（tests/test_paths.py が最新かを見る）。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from company import paths  # noqa: E402

OUT = ROOT / "company" / "known_bundled.txt"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def main() -> int:
    blobs: set[str] = set()
    for commit in git("rev-list", "HEAD").split():
        for line in git("ls-tree", "-r", commit, "--", *paths.WORKSPACE_ITEMS).splitlines():
            meta, _, _name = line.partition("\t")
            parts = meta.split()
            if len(parts) >= 3 and parts[1] == "blob":
                blobs.add(parts[2])
    hashes = set(OUT.read_text(encoding="utf-8").split()) if OUT.exists() else set()
    for blob in sorted(blobs):
        data = subprocess.run(["git", "cat-file", "blob", blob], cwd=ROOT, check=True, capture_output=True).stdout
        hashes.add(hashlib.sha256(data).hexdigest())
    # 作業ツリーの今の中身（まだコミットしていない物）も入れる
    for name in paths.WORKSPACE_ITEMS:
        src = ROOT / name
        files = [src] if src.is_file() else [p for p in src.rglob("*") if p.is_file()] if src.is_dir() else []
        for p in files:
            if "__pycache__" in p.parts:
                continue
            hashes.add(hashlib.sha256(p.read_bytes()).hexdigest())
    OUT.write_text("\n".join(sorted(hashes)) + "\n", encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: {len(hashes)} 件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
