@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem ============================================================
rem  配布用の exe を作る
rem  できあがり: dist\AutoClick\ （フォルダ） と dist\AutoClick.zip
rem ============================================================

if not exist ".venv\Scripts\python.exe" (
    echo .venv が見つかりません。先に setup_venv.bat を実行してください。
    pause
    exit /b 1
)
set PY=.venv\Scripts\python.exe

echo [1/4] 必要なパッケージを確認しています...
%PY% -m pip install -q -r requirements.txt pyinstaller
if errorlevel 1 goto :fail

echo [2/4] exe を作っています（1〜2分かかります）...
if exist build rmdir /s /q build
if exist dist\AutoClick rmdir /s /q dist\AutoClick
if exist dist\AutoClick.zip del /q dist\AutoClick.zip
%PY% -m PyInstaller --noconfirm --clean --log-level WARN ^
    --windowed --name AutoClick ^
    --icon "%~dp0packaging\autoclick.ico" ^
    --hidden-import pystray._win32 ^
    --exclude-module pymsgbox --exclude-module mouseinfo --exclude-module pyperclip ^
    --distpath dist --workpath build --specpath build ^
    tray_app.py
if errorlevel 1 goto :fail

rem 使わない大きなファイルを取り除く（動画用の FFmpeg、AVIF 画像）
del /q dist\AutoClick\_internal\cv2\opencv_videoio_ffmpeg*.dll 2>nul
del /q dist\AutoClick\_internal\PIL\_avif*.pyd 2>nul

echo [3/4] 画像・設定・説明書をコピーしています...
xcopy /e /i /q /y images dist\AutoClick\images >nul
if errorlevel 1 goto :fail
copy /y autoclick_config.json dist\AutoClick\ >nul
copy /y README.md dist\AutoClick\ >nul
copy /y LICENSE dist\AutoClick\ >nul
copy /y packaging\README_dist.txt "dist\AutoClick\はじめにお読みください.txt" >nul
%PY% packaging\collect_licenses.py dist\AutoClick
if errorlevel 1 goto :fail

echo [4/4] zip にまとめています...
powershell -NoProfile -Command "Compress-Archive -Path 'dist\AutoClick' -DestinationPath 'dist\AutoClick.zip' -Force"
if errorlevel 1 goto :fail

echo.
echo できました: dist\AutoClick.zip
echo （dist\AutoClick\AutoClick.exe をダブルクリックすると、そのまま試せます）
pause
exit /b 0

:fail
echo.
echo 失敗しました。上のメッセージを確認してください。
pause
exit /b 1
