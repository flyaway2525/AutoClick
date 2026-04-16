@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "テスト用ボタンツール" .venv\Scripts\python.exe test_tools\test_button_window.py
