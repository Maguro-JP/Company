# 成果物

1つずつ完成させる。上から順に、状態が完成でない最初の行が「今の成果物」。
完成条件は、動かして確かめられる文で書く。状態は 未着手・進行中・確認待ち・完成 の4つ。

| # | 成果物 | 完成条件 | 状態 | 残り | 更新 |
|---|---|---|---|---|---|
| 1 | 内蔵エンジンのストリーミング表示 | `python -m unittest tests.test_local_llm` の StreamingTest が通り、画面で生成中の枠に文字が流れる | 完成 | マージ済み（#15） | 2026-11-07 |
| 2 | Webhook 通知 | `python -m unittest tests.test_notify` が通り、偽の受け口に run id 入りの JSON が 1 通届く | 完成 | マージ済み（#16） | 2026-11-07 |
| 3 | 停止条件の自動化（テストが落ちた状態でのコミットを止める） | `python -m unittest tests.test_tools` で、テストが落ちるときの `git commit` が拒まれ、通るときは通る | 完成 | マージ済み（#17） | 2026-11-07 |
| 4 | 実モデルでの初回実走の記録 | 実モデルで役職を 1 周回し、data/runs.csv に 1 行と docs/knowledge/tech.md に記録が残る | 完成 | Actions の Windows で内蔵の模型 qwen2.5-0.5b により CEO を 1 周（runs.csv に 1 行）。記録は tech.md | 2026-11-07 |
| 5 | 様々な AI を使える構成 | プリセット 10 件と役職ごとの割り当てが画面に出て、「本当に答えるか確かめる」で偽のサーバーから答えが返る | 完成 | マージ済み（#18） | 2026-11-07 |
| 6 | 役職のキャラクターとアニメーション | 画面の上に役職 6 人が並び、実行中の役職が thinking → working → done の順に動き、吹き出しに文が出る（Chromium で確認） | 完成 | マージ済み（#19） | 2026-11-07 |
| 7 | Windows の Setup.exe と Release | Actions の setup が通り、Windows ランナーで入れてテストとウィンドウが確認され、Releases に v0.1.0 が置かれる | 完成 | v0.1.0 公開済み | 2026-11-07 |
| 8 | キャラクターの絵とアプリのアイコン | ui/characters/ に 6 人の PNG、ui/icon/ に app.ico があり、画面と Setup.exe とショートカットに出る | 完成 | v0.1.1 公開、Windows の写しで確認 | 2026-11-07 |
| 9 | 配布版の置き場を他のアプリにそろえる | Program Files にプログラム、ProgramData\Company に作業場、C:\LLM\gguf に模型が置かれ、Actions の Windows で入れ直しても作業場が残る | 完成 | v0.2.0 公開。Actions の Windows で Program Files・ProgramData・C:\LLM を確認 | 2026-11-07 |
| 10 | アプリ本体の更新（「更新を確かめる」） | 右パネルの「更新を確かめる」が releases/latest と VERSION を比べ、新しい版があれば Setup.exe を updates\ に取って開く。偽の API で確かめる | 完成 | v0.2.0 に入っている | 2026-11-07 |
| 11 | 同じ作者のツールとの連携 | 右パネルに相手の一覧と疎通が出て、MCP の相手の道具を役職の AI が `<相手>__<道具>` で呼べ、AdaptiveAIStudio の設定に Company の /mcp を書ける。偽の MCP と Company 自身の /mcp で経路を通す | 完成 | tests/test_partners.py。本物の NetVision の MCP で list と call を確認（tech.md）。AdaptiveAIStudio は手元に無く未確認 | 2026-11-07 |
| 12 | 配布版が Windows で入れてすぐ動く | Actions の Windows で、入れる→起動→内蔵の模型を取る→1 問答える→CEO を 1 周→data/runs.csv に 1 行、まで緑 | 完成 | setup.yml の run 16 で緑（模型 491 MB を 30 秒で取り、1+1=2、CEO 1 周、runs.csv 1 行）。ユーザーの機械での不動は logs\\app.log で切り分ける | 2026-11-07 |
| 13 | 役職の AI がオーナーの働き方（Ciel のスキル）で動く | 毎回の指示の先頭に CLAUDE.md の決めたこと、lessons の「次から」、work-style ほかのスキルが模型の大きさに合わせて入り、配布版にもスキルが同梱される | 完成 | company/workstyle.py、tests/test_workstyle.py。実モデルでの効き目は未確認 | 2026-11-07 |
