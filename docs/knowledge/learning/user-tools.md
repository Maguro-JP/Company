# オーナーが作ったツール（公開分）

目標: オーナーのリポジトリにある道具を、どれでも「何か・どう起こすか・どこから呼ぶか・置き場の決まり」まで言えて、
Company から使う必要が出たときに、この表だけを見て手を付けられる。
判定: `python -m unittest tests.test_learning` が、表の各行に「何か・起こし方・口」の 3 つがそろっていることを見る。
現在: 公開リポジトリをここに書いた。非公開の物は、このリポジトリが public なので名前を書かない。全体の表はオーナーに渡した。
諦める条件: 読めないリポジトリが残っても、それは「未読」と書いて止まる。

## 共通の決まり（全部のツールにかかる）

| 決まり | 中身 | 出典 |
|---|---|---|
| 置き場 | プログラムは `C:\Program Files\<App>`（書き換えない）、設定・記録・部品は `C:\ProgramData\<App>`、模型は `C:\LLM`（`gguf\`、`huggingface\`、`manual\`）で共有。`LLM_HOME` で移せる | CharacterDesigner `chatdraw/paths.py`、AdaptiveAIStudio `installer/AdaptiveAIStudio.nsi`、shared-models |
| 配布 | NSIS の Setup.exe を Linux の Actions で組み、Windows のランナーで入れて動くのを見てから Release に置く。更新はアプリの「更新を確かめる」（releases/latest と VERSION を比べる） | CharacterDesigner `packaging/chatdraw/`、`chatdraw/updater.py` |
| 起動 | CLI ではなくアプリのウィンドウ（pywebview、Electron、PySide6）。`start.cmd` / `start.bat` を 1 つ置く | AdaptiveAIStudio `start.cmd` |
| 作業の決まり | どのリポジトリにも Ciel のスキルと CLAUDE.md が入っている。公開リポジトリには名前・ID・費用を書かない | Ciel |
| 公開 | Web の物は Vercel（`vercel.json`）。静的ならどこでも | NetVision ほか |

## 道具の表（公開リポジトリ）

| 名前 | 何か | 起こし方 | 口（Company から呼ぶとき） | 置き場 |
|---|---|---|---|---|
| AdaptiveAIStudio（AAS） | モデル非依存の AI 開発基盤。画面でファイルを選び、AI に頼む。依存ゼロ、Node 22.6 以上 | `start.cmd` の 1（画面）。`npm run doctor` で足りない物を見る。`npm run agent` でセッション | HTTP `http://127.0.0.1:4174`。`/api/send`（頼む）、`/api/events`・`/api/events/seq`（出来事）、`/api/state`、`/api/models`、`/api/mcp`（MCP の相手を足す。Company の `/mcp` を登録すると AAS から会社を動かせる）、`/api/update`。Company 側は `company/aas.py` の AasProvider | Program Files / ProgramData\AdaptiveAIStudio / C:\LLM |
| CharacterDesigner（chatdraw） | チャットで頼むと AI が絵を描く Windows アプリ。作画監督が数字で検品 | Setup.exe（Releases）。開発は `chatdraw/` で PySide6 | 画面のみ（HTTP の口は無い）。描いた絵は ピクチャ\CharacterDesigner。`tools/` に sheet.py（設定画）、sprite.py、anim.py、check.py など検品と生成の台本。GPU と模型が要るので、この環境からは動かせない | Program Files / ProgramData\CharacterDesigner / C:\LLM |
| Ciel | Claude Code のスキルを作り・集め・配る公開リポジトリ | `scripts/install.sh <skill> <repo>` で取り込む。`scripts/validate.sh` で検証 | スキルは `.claude/skills/<name>/SKILL.md`。要請は各リポジトリの `docs/reports/skill-requests.md` に書く | リポジトリ |
| NetVision | ネットワーク機器の台帳・保守期限・ライフサイクル管理（FastAPI + React） | `docker compose up --build`、または backend で `uvicorn app.main:app`、frontend で `npm run dev`。`python -m scripts.launcher` でウィンドウ | HTTP API（backend）。Excel/CSV の取り込みと書き出し | リポジトリ |
| DevMerit | 開発者の貢献度を可視化する（構想。README 1 行） | 無し | 無し | リポジトリ |
| Company | この会社 | `run.bat`、Setup.exe | `/api/*`、`/mcp`、`/api/gemini/call` | Program Files / ProgramData\Company / C:\LLM |

## Company が参考にした所

- 置き場・installer・更新: CharacterDesigner（`chatdraw/paths.py`、`packaging/chatdraw/`、`chatdraw/updater.py`、`chatdraw/help/install.md`）と AdaptiveAIStudio（`installer/AdaptiveAIStudio.nsi`）
- プロバイダの失敗の言い方と待ち時間: AdaptiveAIStudio（`src/providers/errors.ts`、`src/runtime/net-why.ts`、`src/cli/ask-once.ts`）
- AAS との連携: `company/aas.py`（AAS をモデルとして使う）と `company/mcp_http.py`（AAS から会社を動かす）

## サイクル1（2026-11-07）
仮説: README と入口（package.json の scripts、pyproject、start.cmd）を読めば、呼び方まで書ける。
やったこと: オーナーの全リポジトリを浅く clone し、README の先頭・ディレクトリ・scripts・HTTP の口を読んだ。公開の物をここに、全体はオーナーに渡す表に書いた。
判定: `tests/test_learning.py` で 3 列がそろっていることを見る。
効いた / 効かなかった: 中身が空のリポジトリが多く、読んでも「土台だけ」としか書けなかった。非公開の名前をここに書けないので、全体の表の置き場が決まっていない。
次: 全体の表の置き場（非公開のリポジトリ）をオーナーに決めてもらう。Company から AAS（/api/mcp）を実際に呼ぶ経路を 1 つ試し、動いた物を「口」に実行例として足す。

## 出典
- 各リポジトリの README.md、package.json、pyproject.toml、CLAUDE.md（手元の clone で読んだ）
- AdaptiveAIStudio `src/ui/server.ts`（`/api/` の一覧）、`src/providers/`、`src/runtime/`
- CharacterDesigner `chatdraw/paths.py`、`chatdraw/updater.py`、`packaging/chatdraw/build.sh`、`packaging/chatdraw/installer.nsi`
