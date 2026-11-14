#!/usr/bin/env bash
# MCP sunucusunu HTTP olarak başlatır ve Cloudflare Quick Tunnel ile geçici bir https linki açar.
# Link yalnızca bu betik açıkken çalışır; Ctrl+C ile kapatınca link ölür, her açılışta yeni link gelir.
#
# Gerekenler: cloudflared (macOS: brew install cloudflared) ve kurulu .venv (./run_mac_linux.sh bir kez çalışmış olmalı).
# İsteğe bağlı: MCP_HTTP_PORT (varsayılan 8765), MCP_TOKEN (verilmezse her açılışta rastgele üretilir).
#
# Web uygulaması (./run_mac_linux.sh) ayrıca açıksa uzaktaki modelin yaptığı değişiklikler tarayıcıda da görünür
# ve 3B görüntüleyicide seçilen yüzey okunabilir. Kapalıysa çekirdek bu süreç içinde çalışır.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared bulunamadı. macOS: brew install cloudflared  |  diğerleri: https://developers.cloudflare.com/tunnel/downloads/" >&2
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  echo ".venv bulunamadı. Önce ./run_mac_linux.sh ile kurulumu yapın." >&2
  exit 1
fi
# requirements.txt değiştiyse (ör. mcp paketi yeni eklendiyse) bağımlılıkları güncelle
if ! cmp -s backend/requirements.txt .venv/.requirements.stamp; then
  .venv/bin/pip install -r backend/requirements.txt
  cp backend/requirements.txt .venv/.requirements.stamp
fi

PORT="${MCP_HTTP_PORT:-8765}"
TOKEN="${MCP_TOKEN:-$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(24))')}"
LOG="$(mktemp -t cloudflared.XXXXXX)"
MCP_PID=""
CF_PID=""

cleanup() {
  [ -n "$CF_PID" ] && kill "$CF_PID" 2>/dev/null || true
  [ -n "$MCP_PID" ] && kill "$MCP_PID" 2>/dev/null || true
  rm -f "$LOG"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

if command -v lsof >/dev/null 2>&1 && lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT kullanımda. Başka bir port için: MCP_HTTP_PORT=8766 $0" >&2
  exit 1
fi

echo "MCP sunucusu başlatılıyor (127.0.0.1:$PORT)…"
(cd backend && MCP_TOKEN="$TOKEN" MCP_HTTP_PORT="$PORT" exec ../.venv/bin/python -m app.mcp_server --http) &
MCP_PID=$!

# Sunucu ayağa kalkana kadar bekle (CadQuery'nin ilk yüklenmesi birkaç saniye sürebilir)
for _ in $(seq 1 60); do
  if ! kill -0 "$MCP_PID" 2>/dev/null; then
    echo "MCP sunucusu başlamadı; yukarıdaki hata mesajına bakın." >&2
    exit 1
  fi
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; then
    break
  fi
  sleep 1
done

echo "Cloudflare tüneli açılıyor…"
cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" >"$LOG" 2>&1 &
CF_PID=$!

URL=""
for _ in $(seq 1 60); do
  # api.trycloudflare.com cloudflared'in kendi uç noktasıdır (hata mesajlarında geçer); tünel adresi değildir
  URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | grep -v '://api\.' | head -1 || true)"
  [ -n "$URL" ] && break
  if ! kill -0 "$CF_PID" 2>/dev/null; then
    break
  fi
  sleep 1
done
if [ -z "$URL" ]; then
  echo "Tünel açılamadı. cloudflared çıktısı:" >&2
  cat "$LOG" >&2
  exit 1
fi

cat <<EOF

────────────────────────────────────────────────────────────────────
 MCP bağlantı linki (uzak MCP sunucusu olarak ekleyin):

   $URL/mcp/$TOKEN

 • Link bu pencere açıkken çalışır; Ctrl+C ile kapatınca geçersiz olur.
 • Linki bilen herkes CAD/G-code araçlarını kullanabilir; paylaşmayın.
   Bilgisayarınızdaki dosyalara erişen araçlar bu modda kapalıdır.
 • Tünelin DNS kaydı yayılana kadar ilk birkaç saniye bağlantı hata verebilir.
────────────────────────────────────────────────────────────────────

EOF

# Süreçlerden biri durursa betik de kapanır
while kill -0 "$MCP_PID" 2>/dev/null && kill -0 "$CF_PID" 2>/dev/null; do
  sleep 2
done
echo "Sunucu veya tünel kapandı." >&2
