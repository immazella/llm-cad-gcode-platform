@echo off
REM Backend (8000) ve frontend (5173) sunucularini ayri pencerelerde baslatir.
cd /d %~dp0
if not exist .venv (
  echo Sanal ortam olusturuluyor...
  py -3.12 -m venv .venv || python -m venv .venv
)
REM requirements.txt degistiyse (yeni paket eklendiyse) bagimliliklari guncelle
fc /b backend\requirements.txt .venv\requirements.stamp >nul 2>&1 || (
  .venv\Scripts\pip install -r backend\requirements.txt && copy /y backend\requirements.txt .venv\requirements.stamp >nul
)
if not exist frontend\node_modules (
  cd frontend && call npm install && cd ..
)
start "backend" cmd /k "cd backend && ..\.venv\Scripts\uvicorn app.main:app --reload --port 8000"
cd frontend && npm run dev
