"""子会社ごとの社員を、CharacterDesigner の手順（parts.json → build → anim → sheet）で組む。

CharacterDesigner のドット絵の職業（学者、剣士、商人、神官…）を土台に、会社ごとの色（髪、服）だけを
変えて社員にする。土台の形を Company に写さず、CharacterDesigner の clone を指して使うのは、
絵の決まり（光、縁取り、作風）を向こうに一本化しておくため。

使い方:
    python3 tools/staff_build.py [--cd <CharacterDesigner の置き場>] [--out ui/staff]
    置き場は環境変数 CHARACTERDESIGNER_HOME でもよい。既定は ../maguro-jp/characterdesigner か ../CharacterDesigner。
出力: ui/staff/<会社の id>/<社員の id>/{parts.json, sprite.json, idle.gif, walk.gif, sheet.png}
      ui/staff/staff.json（社員の一覧。org.yaml と画面が読む）
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 会社ごとの色（髪・服の ramp）。土台の職業の形はそのまま、色で会社の制服にする
PALETTES = {
    "aas":    {"hair": ["#1c3470", "#2c4e9a", "#4a76c8"], "cloth": ["#1f2a44", "#2f4270", "#4a66a8"]},
    "ciel":   {"hair": ["#4a3a6a", "#7a5aa8", "#a98ad8"], "cloth": ["#3a2a5a", "#5a4088", "#8a6cc0"]},
    "characterdesigner": {"hair": ["#7a2a3a", "#b04050", "#e07080"], "cloth": ["#5a1e2e", "#8a3048", "#c05070"]},
    "netvision": {"hair": ["#1a4a3a", "#2a7a5a", "#4aaa7a"], "cloth": ["#163a30", "#226050", "#3a9070"]},
    "video":  {"hair": ["#3a2a1a", "#6a4a2a", "#a07a4a"], "cloth": ["#2a2a2a", "#444444", "#6a6a6a"]},
    "music":  {"hair": ["#2a1a4a", "#4a2a8a", "#7a5ac8"], "cloth": ["#1a1a3a", "#2a2a6a", "#4a4aa8"]},
    "voice":  {"hair": ["#6a3a1a", "#a86a2a", "#e0a04a"], "cloth": ["#5a3a1a", "#8a5a2a", "#c08a4a"]},
}

# 社員。(会社の id, 社員の id, 名前, 役割, 土台の職業, 服の素材名)
STAFF = [
    ("aas", "operator", "作業担当", "画面で受けた依頼を作業場で書く", "scholar", "tunic"),
    ("aas", "modeler", "モデル係", "プロバイダとモデルを選び、答えを確かめる", "apothecary", "tunic"),
    ("ciel", "skillwright", "スキル職人", "スキルを作り、直し、配る", "scholar", "tunic"),
    ("ciel", "patrol", "見回り係", "脆弱性とセッションを週に 1 回見回る", "hunter", "tunic"),
    ("characterdesigner", "painter", "作画担当", "頼まれた絵を描き、描き直す", "dancer", "hair"),
    ("characterdesigner", "director", "作画監督", "崩れを数字で見て、通す・戻す", "cleric", "robe"),
    ("netvision", "ledger", "台帳係", "機器・拠点・保守期限を引く", "merchant", "robe"),
    ("netvision", "wiring", "結線係", "結線と取り込み・書き出し", "thief", "tunic"),
    ("video", "boarder", "絵コンテ係", "台本から絵コンテを起こす", "scholar", "tunic"),
    ("video", "editor", "編集係", "切る、つなぐ、テロップ、書き出し", "swordsman", "tunic"),
    ("music", "composer", "作曲係", "曲と効果音を作る", "dancer", "hair"),
    ("music", "mixer", "ミックス係", "音量と整音、書き出し", "apothecary", "tunic"),
    ("voice", "speaker", "合成音声係", "文を声にする", "cleric", "robe"),
    ("voice", "caster", "キャスト係", "キャラクターごとの声を管理する", "merchant", "robe"),
]


def find_cd(explicit: str | None) -> Path:
    cands = [explicit, os.environ.get("CHARACTERDESIGNER_HOME"),
             ROOT.parent / "maguro-jp" / "characterdesigner", ROOT.parent / "CharacterDesigner"]
    for c in cands:
        if c and (Path(c) / "tools" / "build.py").exists():
            return Path(c)
    raise SystemExit("CharacterDesigner が見つかりません（--cd か CHARACTERDESIGNER_HOME）")


def ramp(colors: list[str]) -> dict:
    return {"outline": colors[0], "line": colors[0], "ramp": colors, "steps": [0.55, 0.86]}


def parts_for(cd: Path, company: str, base: str, cloth: str, name: str, role: str) -> dict:
    pal = PALETTES[company]
    materials = {"hair": ramp(pal["hair"])}
    if cloth != "hair":
        materials[cloth] = ramp(pal["cloth"])
    return {"base": str(cd / "characters" / base / "parts.json"), "name": name,
            "brief": f"{role}。土台は CharacterDesigner の {base}。会社の色（髪と服）だけを変えた", "materials": materials}


def run(py: str, cd: Path, script: str, *args: str) -> None:
    subprocess.run([py, str(cd / "tools" / script), *args], check=True, cwd=cd,
                   stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cd")
    ap.add_argument("--out", default=str(ROOT / "ui" / "staff"))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    cd = find_cd(a.cd)
    out = Path(a.out)
    index = []
    for company, sid, name, role, base, cloth in STAFF:
        if a.only and sid not in a.only and company not in a.only:
            continue
        d = out / company / sid
        d.mkdir(parents=True, exist_ok=True)
        parts = d / "parts.json"
        parts.write_text(json.dumps(parts_for(cd, company, base, cloth, name, role), ensure_ascii=False, indent=1), encoding="utf-8")
        run(a.python, cd, "build.py", str(parts))
        run(a.python, cd, "anim.py", "--anim", "idle", str(d / "idle.gif"), str(d / "sprite.json"))
        run(a.python, cd, "anim.py", "--anim", "walk", str(d / "walk.gif"), str(d / "sprite.json"))
        run(a.python, cd, "sheet.py", str(parts), str(d / "sheet.png"))
        for junk in ("idle.png", "walk.png"):
            (d / junk).unlink(missing_ok=True)
        # 土台の絶対パスはこの機械のもの。配る parts.json は職業の名前だけにする
        spec = json.loads(parts.read_text(encoding="utf-8"))
        spec["base"] = f"characterdesigner:{base}"
        parts.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
        index.append({"company": company, "id": sid, "name": name, "role": role, "base": base,
                      "art": f"/staff/{company}/{sid}/idle.gif", "walk": f"/staff/{company}/{sid}/walk.gif",
                      "sheet": f"/staff/{company}/{sid}/sheet.png"})
        print(f"{company}/{sid}: {name}（{base}）")
    (out / "staff.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(index)} 人 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
