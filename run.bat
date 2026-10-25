@echo off
rem Windows 用の起動スクリプト。初回は依存を入れる。
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv || goto :fail
)
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt || goto :fail
python app.py %*
goto :eof
:fail
echo.
echo セットアップに失敗しました。Python 3.11 以上が入っているか確認してください。
pause
