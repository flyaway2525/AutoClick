@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if not %errorlevel%==0 (
    echo Python が見つかりません。先に Python 3.10+ をインストールしてください。
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo .venv を作成しています...
    python -m venv .venv
    if not %errorlevel%==0 (
        echo .venv の作成に失敗しました。
        pause
        exit /b 1
    )
)

echo 依存パッケージをインストールしています...
.venv\Scripts\python.exe -m pip install --upgrade pip
if not %errorlevel%==0 (
    echo pip の更新に失敗しました。
    pause
    exit /b 1
)

.venv\Scripts\python.exe -m pip install -r requirements.txt
if not %errorlevel%==0 (
    echo 依存パッケージのインストールに失敗しました。
    pause
    exit /b 1
)

echo セットアップ完了。
pause
