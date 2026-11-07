# 技術

## 環境の事実（2026-10-25）

- この開発環境の `pip` は Python 3.13 を指し、`python3` は 3.11。依存は `python3 -m pip` で入れる
- `llama-cpp-python` は CPU wheel の index（abetlen.github.io/llama-cpp-python/whl/cpu）から入る。ビルド不要
- `anthropic` 1.x は認証情報が無いと生成時ではなく最初のリクエスト時に `TypeError` を投げる。生成時に捕まえても意味がない
- Hugging Face の GGUF は `resolve/main/<file>` で直リンクでき、HEAD の `x-linked-size` が実サイズ
- Playwright は Node 版だけ入っている（`require('playwright')`）。Python 版は無い

## 内蔵エンジン（llama.cpp）

- `create_chat_completion` の `stop` に `</tool_call>` を入れると閉じタグが落ちる。解釈側で閉じタグ無しを許す
- 0.5B クラスはツールの書式を説明だけでは守らない。例を1つ添えると安定した（test_local_llm の実推論テスト）
- エンジンは同時に1つだけ保持し、呼び出しはロックで直列にする。llama.cpp はスレッド安全でない

## AdaptiveAIStudio の MCP クライアント（src/mcp/client.ts）

- Streamable HTTP: POST に JSON-RPC 1件、返事は JSON でよい（SSE 不要）。通知（id なし）は 202
- `mcp-session-id` ヘッダを返すと以後付けてくる。DELETE で切る
- `initialize` → `notifications/initialized` → `tools/list` の順。ツール名は `<サーバ名>__<道具名>` に付け替えられる
- MCP クライアントは本体（Session）が立つときに起動する。モデルが無いと本体が立たないので繋がらない
- 外のツールの呼び出しは「命令」扱いで、`approval: autoEdit`（既定）では承認待ちになる（`/api/approve`）
- AAS の本体へは `/api/send` で送り、`/api/events` の `session` 出来事の `text.delta` を集め、`busy: false` で終わる


## 実モデルでの初回実走（2026-11-07、Actions の Windows ランナー）

配布版（Setup.exe）を黙って入れ、アプリを起動し、内蔵の模型 qwen2.5-0.5b（491,400,032 バイト）を取り、
「1+1=」に 0.8 秒で「2」と答え、CEO を 1 周（2 ターン、道具 1 回、入力 2,688 トークン、出力 43 トークン）回して
`ProgramData\Company\data\runs.csv` に 1 行残った。入れて開くだけでは「動く」とは言えないので、この実走を
setup.yml の Windows のジョブに入れ、main への push のたびに見る。

ユーザーの機械で動かないときは、`ProgramData\Company\logs\app.log` に起動と失敗の記録が残る（pythonw は画面に何も出さないため）。

## NetVision の MCP を Company から呼んだ記録（2026-11-07）

手元の NetVision の clone（backend）に依存を入れ、Company の `partners.McpClient`（標準入出力）で
`python -m app.mcp_server` を起こし、initialize → tools/list → tools/call を通した。
tools/list は list_devices、get_device、device_summary、list_links、list_sites の 5 つ。
台帳が PostgreSQL に無い状態では `device_summary` が psycopg の接続失敗を isError で返し、
`NETVISION_DATABASE_URL=sqlite:///...` で空の台帳を作ると `{"total": 0, ...}` と `{"sites": []}` が返った。
役職の AI からは `netvision__device_summary` の名前で同じ道具が見える。
AdaptiveAIStudio は手元に無く、登録（aas.config.json への書き込み）は偽の置き場でしか確かめていない。
