#!/usr/bin/env bash
# Backend (8000) ve frontend (5173) sunucularını birlikte başlatır. Ctrl+C ikisini de durdurur.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Sanal ortam oluşturuluyor (.venv)…"
  python3 -m venv .venv
fi
# requirements.txt değiştiyse (yeni paket eklendiyse) bağımlılıkları güncelle
if ! cmp -s backend/requirements.txt .venv/.requirements.stamp; then
  .venv/bin/pip install -r backend/requirements.txt
  cp backend/requirements.txt .venv/.requirements.stamp
fi
if [ ! -d frontend/node_modules ]; then
  (cd frontend && npm install)
fi
(cd backend && ../.venv/bin/uvicorn app.main:app --reload --port 8000) &
BACK=$!
trap "kill $BACK 2>/dev/null" EXIT
cd frontend && npm run dev
