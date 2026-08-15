# Company

AIが経営する会社の運営リポジトリ。

役員（CEO / CTO / CMO / CFO / Ops）をそれぞれ独立したエージェントとして定義し、
週次で会議を回して、決定を記録に残す。人間のオーナーは方向と責任を持ち、
法務・金銭・対外発信の最終判断だけを握る。

## 仕組み

```
company.yaml       会社の前提（ミッション・顧客・権限・KPI）。ここが唯一の設定
agents/*.md        役職ごとの判断基準。frontmatter + 指示文
company/           ランタイム。設定を読み、順にエージェントを走らせる
data/metrics.csv   実測値。エージェントが数値を使う唯一の情報源
data/decisions/    サイクルごとの記録。書き換えない
docs/charter.md    憲章。company.yaml より上位
```

サイクルは CEO → CTO → CMO → CFO → Ops の順で走る。
後続の役職は、先行した役職の報告を前提として受け取るので、
CEOが「今週の一点集中」を外すと全員が外れる。そこが設計上の急所。

## 使い方

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...

# 1. まず company.yaml の TODO を埋める
python -m company.run --check

# 2. APIを呼ばずに、エージェントに渡る内容を確認する
python -m company.run --cadence weekly --dry-run

# 3. 実際に1サイクル回す
python -m company.run --cadence weekly
```

出力は `data/decisions/YYYY-MM-DD-weekly.md` に書き出される。
末尾のオーナー承認欄を埋めてからコミットする。

## 設計上の判断

**エージェントに事実を推測させない。** 会社の状態は `company/context.py` が
組み立てたブロックとして渡し、そこに無いものは「不明」と書かせる。
metrics.csv が空なら、CFOはランウェイを計算せず「記録なし」と報告する。
経営判断の土台に幻覚が混ざると、記録全体が信用できなくなるため。

**権限を設定ファイルに書く。** `autonomy` の各フラグは共有コンテキストに
そのまま埋め込まれる。`can_publish_externally: false` の間、CMOは下書きまでで止まる。
プロンプトに散らすより、1箇所で変えられる方が安全側に倒しやすい。

**Opsは提案しない。** 確認だけを担当させる。提案と検証を同じエージェントに
持たせると、自分の提案を完了扱いにする方向に寄る。

## 現状と次の一手

土台のみ。`company.yaml` の mission / customer / business_model / north_star が
TODO のままなので、`--check` は失敗する。ここを埋めるのが最初の作業で、
それはAIではなくオーナーの仕事（憲章 5 節）。

未実装で、必要になったら足すもの:

- `data/metrics.csv` への自動記入（現状は手入力）
- GitHub Actions での週次実行
- 停止条件（憲章 4 節）の自動検出
