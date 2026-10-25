# AdaptiveAIStudio と繋ぐ

[AdaptiveAIStudio](https://github.com/Maguro-JP/AdaptiveAIStudio)（以下 AAS）と、どちらからでも動かせる。

| 向き | 何が起きるか | 仕組み |
|---|---|---|
| AAS → 会社 | AAS の画面で「会社の ops を起こして」と頼むと、AAS の担当が会社の道具を呼ぶ | 会社が MCP の口（`/mcp`）を立て、AAS がそこに繋ぐ |
| 会社 → AAS | 会社の役職が、AAS の本体（モデル・作業担当・作業場）に仕事を頼む | 会社のプロバイダ「AdaptiveAIStudio」が `/api/send` に送り、`/api/events` で返事を受ける |

## AAS → 会社

会社のアプリ（`python app.py`、既定 8765）を起動しておき、AAS の `aas.config.json` に1行足す。

```jsonc
{ "mcp": { "servers": {
  "company": { "url": "http://127.0.0.1:8765/mcp" }
} } }
```

AAS を立ち上げ直すと、`⚙ 設定 → 外の道具` に `company（6 件）` と出る。
道具は `company__start_cycle` / `company__start_role` / `company__get_run` / `company__stop_run` / `company__read_file` / `company__list_roles`。

**承認が一度入る。** AAS は外のツールの呼び出しを「命令」として扱い、既定（`approval: "autoEdit"`）では
実行前に画面で承認を求める。承認すると会社側でランが始まり、結果は検問を通って AAS の会話に戻る。
毎回訊かれたくなければ AAS 側で `{ "agent": { "approval": "auto" } }`。

**道具を持てるのは `mcp` 権限のある役職だけ。** AAS の既定では ENVOY（情報担当）が持つ。
会社専任の担当を足すなら:

```jsonc
{ "roles": [
  { "id": "owner-desk", "name": "会社担当",
    "responsibility": "AIが経営する会社を動かし、進捗を持ち帰る。",
    "disposition": "始める前に list_roles で何ができるかを見る。get_run の結果を要約して伝え、丸写ししない。",
    "access": ["read", "mcp"],
    "instructions": ["start_cycle か start_role で始め、run_id を覚える", "進捗は get_run で確かめてから報告する"] }
] }
```

## 会社 → AAS

AAS を**会社のフォルダを作業場にして**起動しておく（AAS の作業担当がこのリポジトリに書くため）。
会社の右パネルでプロバイダを「AdaptiveAIStudio（起動中の本体に任せる）」にし、URL を AAS の画面の URL（既定 `http://127.0.0.1:4174`）にする。

この向きでは会社のツール（read_file など）は渡さない。AAS は自分の道具と作業場と守り（ハーネス）を持っていて、
こちらの道具は見ないため。会社の役職定義は、最初の依頼に「決まり」として添える。
どのモデルを使うかは AAS の自動操縦が決める。

## 確かめたこと（2026-10-25）

本物の AAS（`dff7c61`）を Linux で headless に立て、モデルは AAS 自身の試験用の偽サーバーで代えた。

- AAS → 会社: AAS が `/mcp` に繋いで 6 件の道具を認識（`/api/mcp` で `running: true, tools: 6`）。
  偽モデルに `company__start_role` を呼ばせると AAS が承認を求め、承認すると会社側でランが始まり、
  結果が AAS の会話に「<<外のツール company/start_role>>」として戻った
- 会社 → AAS: 会社のプロバイダが `/api/send` に送り、`/api/events` から返事「はい」を受け取った

確かめていないこと: 本物のモデルでの会話の質、Windows 上での両方の起動。
