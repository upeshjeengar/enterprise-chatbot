@echo off
REM CompliFlow Lite launcher (Windows)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
echo Ingesting policies (first run embeds all policy docs via NVIDIA NIM)...
.venv\Scripts\python.exe -c "from app import db, rag; db.init_db(); print('chunks:', rag.ingest())"
echo Starting CompliFlow Lite on http://127.0.0.1:8000
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
