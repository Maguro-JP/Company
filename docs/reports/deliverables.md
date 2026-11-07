# 成果物

1つずつ完成させる。上から順に、状態が完成でない最初の行が「今の成果物」。
完成条件は、動かして確かめられる文で書く。状態は 未着手・進行中・確認待ち・完成 の4つ。

| # | 成果物 | 完成条件 | 状態 | 残り | 更新 |
|---|---|---|---|---|---|
| 1 | 内蔵エンジンのストリーミング表示 | `python -m unittest tests.test_local_llm` の StreamingTest が通り、画面で生成中の枠に文字が流れる | 確認待ち | PR 待ち（pending 1） | 2026-11-07 |
| 2 | Webhook 通知 | `python -m unittest tests.test_notify` が通り、偽の受け口に run id 入りの JSON が 1 通届く | 確認待ち | PR 待ち（pending 2） | 2026-11-07 |
| 3 | 停止条件の自動化（テストが落ちた状態でのコミットを止める） | `python -m unittest tests.test_tools` で、テストが落ちるときの `git commit` が拒まれ、通るときは通る | 進行中 | 実装とテスト | 2026-11-07 |
| 4 | 実モデルでの初回実走の記録 | build を 1 周回し、data/runs.csv に 1 行と docs/knowledge/ai.md に記録が残る（仮） | 未着手 | オーナーのキーかモデルが要る | 2026-11-07 |
