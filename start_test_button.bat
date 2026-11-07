@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
	start "テスト用ボタンツール" ".venv\Scripts\python.exe" test_tools\test_button_window.py
	exit /b 0
)

echo .venv が見つかりません。
echo 先にセットアップを実行してください:
echo   python -m venv .venv
echo   .venv\Scripts\python.exe -m pip install pyautogui opencv-python numpy keyboard pillow pystray mss
pause
