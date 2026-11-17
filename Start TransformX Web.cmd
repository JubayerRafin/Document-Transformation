@echo off
setlocal
cd /d "%~dp0"
title TransformX Web Server
if not exist ".venv-backend\Scripts\python.exe" (
  echo TransformX backend environment is missing.
  pause
  exit /b 1
)
echo TransformX: http://127.0.0.1:8000/
echo Studio:     http://127.0.0.1:8000/studio
echo Keep this window open while using the site. Press Ctrl+C to stop.
echo Wait for "Application startup complete" before opening the link.
".venv-backend\Scripts\python.exe" -u -c "from backend.api import create_app; import uvicorn; uvicorn.run(create_app(), host='127.0.0.1', port=8000)"
echo.
echo TransformX server stopped. Any startup error appears above.
pause
