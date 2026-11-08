#!/bin/sh
# Company の Setup.exe を Linux の上で組む（NSIS の makensis。Windows の機械は要らない）。
#
#   sh packaging/company/build.sh            dist/Company-Setup.exe を作る
#   SKIP_NSIS=1 sh packaging/company/build.sh   中身を集めるところまで（makensis が無い環境で確かめる）
#
# 中身: Python 本体（python.org の埋め込み版 amd64）、画面と HTTP の部品（pywebview、fastapi、uvicorn）、
# 内蔵エンジン（llama-cpp-python の CPU 版 wheel）、会社の本体（company、ui、agents、product、app.py、company.yaml）。
# 入れないもの: モデル（アプリの右パネルから取る）、.venv、data/ の記録。
# CharacterDesigner の packaging/chatdraw/build.sh と同じ組み方にそろえる。
set -eu
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PYVER="${CO_PYVER:-3.12.10}"
PYTAG="$(echo "$PYVER" | cut -d. -f1-2 | tr -d .)"
VERSION="$(cat "$ROOT/VERSION")"
OUT="$ROOT/dist"
B="$OUT/company-build"
SRC="$B/app"
if [ -z "${SKIP_NSIS:-}" ]; then
  command -v makensis >/dev/null || { echo "makensis が無い（apt install nsis）" >&2; exit 1; }
fi
rm -rf "$B"; mkdir -p "$SRC" "$OUT/download"
cd "$ROOT"
# 中身は HEAD から取る。コミットしていない変更は入らないので、残っていれば止める
if [ -n "$(git status --porcelain -- company ui agents product app.py company.yaml VERSION packaging/company)" ]; then
  echo "コミットしていない変更がある。コミットしてから組む:" >&2; git status --short -- company ui agents product app.py company.yaml VERSION packaging/company >&2; exit 1
fi

echo "== 会社の本体（HEAD）"
git archive HEAD company ui agents product app.py company.yaml org.yaml README.md CLAUDE.md VERSION docs/charter.md docs/help docs/reports/deliverables.md .claude/skills | tar -x -C "$SRC"
rm -rf "$SRC/product/.git"
mkdir -p "$SRC/data/decisions"

echo "== Python $PYVER（埋め込み版）"
ZIP="$OUT/download/python-$PYVER-embed-amd64.zip"
[ -f "$ZIP" ] || curl -sSfL -o "$ZIP" "https://www.python.org/ftp/python/$PYVER/python-$PYVER-embed-amd64.zip"
mkdir -p "$SRC/python"
python3 -m zipfile -e "$ZIP" "$SRC/python"
# 埋め込み版は ._pth で読む場所を固定し、Lib/site-packages を読まない。外して普通の Python と同じに読ませる
rm -f "$SRC/python/python$PYTAG._pth"

echo "== 部品（Windows 用の wheel）"
W="--platform win_amd64 --python-version $PYVER --implementation cp --only-binary=:all: -q --disable-pip-version-check"
SP="$SRC/python/Lib/site-packages"
mkdir -p "$SP"
# 依存ごと取る（手で並べると fastapi の annotated_doc のような新しい依存が抜ける。Windows のランナーで落ちた）。
# pip は --platform を渡しても環境マーカーをこの機械で評価するので、Windows でだけ要る物（pythonnet）は下で足す
python3 -m pip install $W --target "$SP" fastapi uvicorn PyYAML anthropic httpx
python3 -m pip install $W --target "$SP" --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu "llama-cpp-python==0.3.19"
# pywebview は proxy_tools（wheel が無い）に依存するので、依存を切って入れ、残りを手で足す
python3 -m pip install $W --target "$SP" --no-deps pywebview
python3 -m pip install $W --target "$SP" pythonnet clr-loader bottle typing_extensions
python3 -m pip download -q --disable-pip-version-check --no-deps --no-binary :all: -d "$B/sdist" proxy_tools
tar -xzf "$B"/sdist/proxy_tools-*.tar.gz -C "$B/sdist"
cp -r "$B"/sdist/proxy_tools-*/proxy_tools "$SP/"

echo "== 依存の抜けを見る（Windows の環境マーカーで METADATA の Requires-Dist を辿る）"
python3 "$ROOT/packaging/company/check_deps.py" "$SP"

echo "== 同梱物の欠けを見る"
missing=""
for f in VERSION app.py company.yaml CLAUDE.md .claude/skills/work-style/SKILL.md .claude/skills/lessons/LESSONS.md company/server.py company/local_llm.py ui/index.html ui/icon/app.ico ui/characters/ceo.png agents/ceo.md product/README.md org.yaml \
         python/python.exe python/pythonw.exe \
         python/Lib/site-packages/fastapi python/Lib/site-packages/uvicorn python/Lib/site-packages/webview \
         python/Lib/site-packages/pythonnet python/Lib/site-packages/clr_loader python/Lib/site-packages/proxy_tools \
         python/Lib/site-packages/llama_cpp python/Lib/site-packages/yaml python/Lib/site-packages/anthropic python/Lib/site-packages/httpx; do
  [ -e "$SRC/$f" ] || missing="$missing $f"
done
[ -z "$missing" ] || { echo "欠けている:$missing" >&2; exit 1; }
du -sh "$SRC"

if [ -n "${SKIP_NSIS:-}" ]; then echo "== SKIP_NSIS: Setup.exe は組まない"; exit 0; fi
echo "== Setup.exe"
makensis -V2 -DSRC="$SRC" -DICON="$SRC/ui/icon/app.ico" -DOUT="$OUT/Company-Setup.exe" -DVERSION="$VERSION" "$ROOT/packaging/company/installer.nsi"
ls -la "$OUT/Company-Setup.exe"
