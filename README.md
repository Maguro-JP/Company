# Company

AIが経営する会社。次に何を作るかをAIが決め、AIが実装し、テストを通してコミットする。
人間のオーナーは、AIに手段が無い作業（登記・支払い・署名）を代行し、方向がずれたら止める。

## 作っているもの

`product/` — **Churn Radar**。個人でSaaSを運営している人向けの、解約予兆検知。

アカウント自身の過去と比べて、稼働が落ちたものを拾う。他社比較も絶対値のしきい値も使わない。
週1回使うのが平常な人と毎日使う人では、「減った」の意味が違うため。

```bash
cd product
python -m churn_radar sample_events.csv --today 2026-09-01
```

```
予兆 3 件（平常時の14日間と比較）

  beta-labs  停止  稼働 14日 → 0日  最終 2026-08-18 (14日前)
  gamma-io   急減  稼働 13日 → 1日  最終 2026-08-23 (9日前)
  delta-app  減少  稼働 10日 → 3日  最終 2026-08-28 (4日前)
```

`account_id,date` の2列があるCSVならそのまま読む。余分な列は無視する。

## 会社の仕組み

```
company.yaml       前提と権限。autonomy のフラグが唯一の制御点
agents/*.md        役職ごとの判断基準（ceo / builder / cto / cmo / cfo / ops）
company/execute.py 実行サイクル。Claude Agent SDK でツールを渡し、実際に作らせる
company/board.py   レビューサイクル。報告を書かせるだけ（実行はしない）
product/           プロダクト本体
data/decisions/    レビューの記録
docs/charter.md    憲章
```

**実行サイクル**（`--execute`）では、エージェントに Read / Write / Edit / Bash を渡し、
`permission_mode="bypassPermissions"` で走らせる。確認を挟まない。挟むと提案止まりになるため。
終了条件は「テストが通ってコミットされた状態」で、そこまでが1タスク。

**レビューサイクル**は読み取り専用のツールしか渡さない。報告と実行を同じ権限で回すと、
自分の作業を自分で承認する構造になるため。

## 使い方

```bash
pip install -r requirements.txt
claude   # 一度ログインする。または export ANTHROPIC_API_KEY=sk-ant-...

python -m company.run --check                      # 前提が埋まっているか
python -m company.run --cadence build --execute    # AIが1つ作ってコミットする
python -m company.run --cadence review             # 進捗と数字の報告
```

テスト:

```bash
python -m unittest discover -s tests                    # 会社の仕組み
cd product && python -m unittest discover -s tests      # プロダクト
```

## 設計上の判断

**権限は設定ファイルに集約する。** `autonomy` の各フラグは共有コンテキストにそのまま
埋め込まれ、「true のものは確認を取らずに実行してください」と指示される。
プロンプト各所に散らすより、1箇所で締められる方が安全側に倒しやすい。

**「禁止」と「手段が無い」を分ける。** 支払いや登記は、AIが遠慮しているのではなく
実行手段が存在しない。混ぜると、エージェントが本来できることまで自粛する。

**幻覚を経営判断の土台に入れない。** 会社の状態は `company/context.py` が組み立てた
ブロックとして渡し、そこに無い事実は「不明」と書かせる。`metrics.csv` が空なら、
CFOはランウェイを計算せず「記録なし」と報告する。

**Opsは提案しない。** 確認だけを担当させる。提案と検証を同じエージェントに持たせると、
自分の提案を完了扱いにする方向に寄る。

## 現状

- プロダクト: 検出ロジックとCLIが動く。テスト20件が通る
- 会社の仕組み: テスト8件が通る。実行サイクルは認証が通れば動く状態
- 未着手: 通知（メール/Slack）、`data/metrics.csv` の自動記入、週次の自動実行
