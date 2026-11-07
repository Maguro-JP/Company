# オーナーのクリエイティブツールのシリーズ

オーナーは、同じ決まり（置き場、配布、起動、Ciel のスキル）で作ったツールを一つのシリーズとして育てている。
Company はその一員で、ほかのツールを「相手が得意なこと」に使う。ここは、今あるものと、これから作るものの台帳。
public のリポジトリなので、公開しているものだけを名前で書く。

## 共通の決まり（シリーズ全体）

- 置き場: プログラムは `C:\Program Files\<App>`、設定・記録・部品は `C:\ProgramData\<App>`、模型は `C:\LLM`（`gguf\`、`huggingface\`）で共有
- 配布: NSIS の Setup.exe。Linux の Actions で組み、Windows のランナーで入れて起動まで見てから Release に置く。更新はアプリの「更新を確かめる」
- 起動: アプリのウィンドウ（pywebview、Electron、PySide6）。CLI にしない。ショートカットを 1 つ置く
- 連携: ユーザーの設定作業を要らせない。相手が入っていれば置き場を自動で探し、口（HTTP、MCP、ファイル）だけでつなぐ。相手の中身には触らない
- 作業の決まり: どのリポジトリにも Ciel のスキルと CLAUDE.md が入り、同じ働き方で開発する

## 今あるもの

| 分野 | ツール | 役目 | Company からの口 |
|---|---|---|---|
| コーディング | AdaptiveAIStudio | モデル非依存の AI 開発の土台。画面で AI に作業を頼む | HTTP `:4174`。起動時に Company の `/mcp` を自動で登録し、向こうから会社を動かせる |
| コーディングの作法 | Ciel | Claude Code のスキルを作り・配る。シリーズ全体の働き方 | `.claude/skills`。役職の AI の指示の先頭に差し込む（company/workstyle.py） |
| キャラクターデザイン | CharacterDesigner | チャットで頼むと AI が絵を描く。作画監督が数字で検品 | ピクチャ\CharacterDesigner の絵。描く口（HTTP、MCP）は向こうにまだ無い |
| 台帳 | NetVision | ネットワーク機器の台帳・保守期限 | MCP（`NetVision-mcp.bat`、`python -m app.mcp_server`）。役職の AI が `netvision__*` で引く |
| 経営 | Company（このアプリ） | AI の役職が会社を回す。ほかのツールを道具として使う | `/api/*`、`/mcp` |

## これから作るもの（予約席）

オーナーの予定。Company は `company/partners.py` に同じ id の席を用意しておき、
相手ができたら置き場の自動検出と口を足すだけでつながるようにする。

| 分野 | 予約の id | 想定する口 | Company での使い方 |
|---|---|---|---|
| 映像 | `video` | MCP（生成・編集・書き出し）、出来た物はビデオの `<App>` | CMO の宣伝の素材、製品の紹介の動画 |
| 音楽 | `music` | MCP（曲の生成、書き出し）、出来た物はミュージックの `<App>` | 動画の BGM、アプリの音 |
| ボイス | `voice` | MCP（文を声にする、声の一覧）、出来た物はミュージックの `<App>\voice` | 役職のキャラクターの声、読み上げ |

席の中身（相手の名前、置き場、口）は、相手のリポジトリができてから決める。
CharacterDesigner に描く口（MCP）ができたら、キャラクターの絵もこの表の形で同じようにつなぐ。

## Company が守ること

- 相手の得意なことは相手に任せる。Company は「何を・いつ・誰に頼むか」だけを持つ
- シリーズに新しいツールが増えたら、この表と `partners.py` の既定に 1 行足し、自動検出の場所（Setup.exe の鍵 `HKLM\Software\<App>\InstallDir`、Program Files）を同じ決まりにそろえる
- 公開していないツールの名前は、この表ではなく作業場の `data/partners.json` にだけ置く
