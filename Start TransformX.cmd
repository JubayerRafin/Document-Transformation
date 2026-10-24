@echo off
cd /d "%~dp0"
if not exist ".venv-paddleocr\Scripts\python.exe" (
  echo TransformX environment not found. Create .venv-paddleocr and install requirements.txt and requirements-deep-doc.txt first.
  pause
  exit /b 1
)
".venv-paddleocr\Scripts\python.exe" -m streamlit run transform_app.py --server.address 127.0.0.1 --server.port 8504 --server.fileWatcherType poll --server.runOnSave true
