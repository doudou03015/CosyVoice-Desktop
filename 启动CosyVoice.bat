@echo off
cd /d "%~dp0"
if exist "CosyVoice-Desktop.exe" (
  start "" "%~dp0CosyVoice-Desktop.exe"
) else (
  python -B -m desktop_app
  if errorlevel 1 pause
)
