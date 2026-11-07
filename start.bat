@echo off
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
	start "" ".venv\Scripts\pythonw.exe" tray_app.py
	exit /b 0
)

echo .venv が見つかりません。
echo 先にセットアップを実行してください:
echo   python -m venv .venv
echo   .venv\Scripts\python.exe -m pip install pyautogui opencv-python numpy keyboard pillow pystray mss
pause
