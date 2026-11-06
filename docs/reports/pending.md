# 判断待ち

自律開発（long-dev / auto-dev）が脇に置いた、人の判断が要る件。答えは「答え:」に日付と一緒に書く。

## 1. PR 待ち: claude/DEV-20261031（内蔵エンジンのストリーミング表示 + 月報 2026-11 + 整理整頓 + 日報 11/07 + 成果物の表）

- 中身: 内蔵エンジンの生成中の文字を流す（delta 出来事、画面は生成中の枠）。llama.cpp のストリームに usage が無いので字句器で数える。月報 2026-11（週報 W45 を兼ねる）。この pending.md
- 検査: precheck 24/25。落ちた 1 件は「commit messages に URL」で、過去のコミットに付けていた Claude-Session の URL（システム側の指定）。このリポジトリの決まりが禁じるので、以後は付けない。既存コミットの書き換えは force push になるため、squash マージで本文を題だけにする形で吸収したい
- 推す案: squash でマージ（コミットの本文は PR の題だけ）
- 答え:

## 2. PR 待ち: claude/DEV-20261102（11/2 の周）

- 中身: ランの終わりを Webhook（Slack / Discord の incoming webhook）に1通知らせる。右パネルで URL と「毎回 / 失敗・停止のときだけ」を選ぶ。送れなくてもランの成否は変えない（HEAD 2d83719）
- 検査: precheck は commit messages 以外すべて通過（落ちた 1 件は 1 と同じ、過去コミットの URL）。会社側テスト 60 件、プロダクト側 20 件通過
- 推す案: 1 のあとに squash でマージ
- 答え:

## 3. PR 待ち: claude/DEV-20261107（11/7 の周）

- 中身: `git commit` の前に run_command がプロダクトのテストを走らせ、落ちていればコミットしない（憲章 4 節を道具の側で守る）。HEAD 0f81cdd
- 検査: precheck の結果は会話の ❔ に書く。会社側テスト 58 件、プロダクト側 20 件通過
- 推す案: 1・2 のあとに squash でマージ
- 答え:

## 4. PR 待ち: claude/FIX-multi-ai-20261107（様々な AI を使えるようにする）

- 中身: OpenAI 互換のプリセット 10 件（OpenAI / OpenRouter / Groq / DeepSeek / xAI / Mistral / Together / Perplexity / Ollama / LM Studio）と、役職ごとの AI の割り当て。HEAD 2408102
- 検査: precheck の結果は会話の ❔ に書く。会社側テスト 62 件、プロダクト側 20 件通過。Chromium で一覧の表示・鍵の欄・役職の割り当ての保存を確認
- 推す案: 1〜3 のあとに squash でマージ
- 答え:
