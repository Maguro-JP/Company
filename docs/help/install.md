# 手順書: 入れる・更新する・置き場

## 1. 入れる

1. [Releases](https://github.com/Maguro-JP/Company/releases/latest) から `Company-Setup.exe` を取ります
2. 開いて、案内に沿って入れます。管理者の権限を求められます
3. デスクトップかスタートメニューの「Company」を開きます

Python などをほかに入れる必要はありません。アプリは、ユーザーの Python や環境変数の設定を読まずに起動します。

## 2. 置き場

| 置き場 | 入っている物 | 更新のとき |
|---|---|---|
| `C:\Program Files\Company\` | プログラム（company、ui、同梱の Python、app.py） | 丸ごと置き換える |
| `C:\ProgramData\Company\` | 作業場。company.yaml、agents、product、docs、data（記録・設定）。AI がここを書き換え、git の履歴を持つ | 残す。新しい版で増えた物だけ足す |
| `C:\LLM\gguf\` | 言葉の模型。AdaptiveAIStudio、CharacterDesigner と共有 | 残す。アンインストールでも消さない |

環境変数 `COMPANY_HOME` で作業場を、`LLM_HOME` で模型の置き場を移せます。

## 3. 更新する

右パネルの一番下の「更新を確かめる」を押します。新しい版があれば「取って入れる」で Setup.exe を取って開くので、
案内に沿って入れます。起動のたびにも黙って確かめ、新しい版があるときだけ知らせます。
取った Setup.exe は `C:\ProgramData\Company\updates\` に置かれ、アンインストールで消えます。

## 4. 前の版から

v0.1.x はユーザーごとの置き場（`%LOCALAPPDATA%\Programs\Company`）に入っていました。新しい Setup.exe を実行すると、
作業場と記録を `C:\ProgramData\Company` へ、模型を `C:\LLM\gguf` へ移してから、前の版を消します。
移せなかった物があれば `C:\ProgramData\Company\migrate.log` に残ります。

## 5. 消す

「設定」の「アプリ」か、スタートメニューの「Company をアンインストール」から消します。
作業場（AI が作ったプロダクトと記録）を消すかは聞かれます。模型（`C:\LLM`）は消しません。
