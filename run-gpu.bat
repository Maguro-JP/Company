@echo off
rem NVIDIA GPU で内蔵エンジンを動かす場合。CUDA 12.x のドライバが必要。
cd /d "%~dp0"
if not exist .venv ( python -m venv .venv || goto :fail )
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt || goto :fail
python -m pip install -q --force-reinstall --no-deps llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124 || goto :fail
python app.py %*
goto :eof
:fail
echo セットアップに失敗しました。
pause
