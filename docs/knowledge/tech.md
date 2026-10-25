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
