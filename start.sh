#!/bin/sh
set -eu

cd /app
TOR_PID=""
APP_PID=""

cleanup() {
    if [ -n "$APP_PID" ] && kill -0 "$APP_PID" 2>/dev/null; then
        kill -TERM "$APP_PID" 2>/dev/null || true
        wait "$APP_PID" 2>/dev/null || true
    fi
    if [ -n "$TOR_PID" ] && kill -0 "$TOR_PID" 2>/dev/null; then
        kill -TERM "$TOR_PID" 2>/dev/null || true
        wait "$TOR_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

if [ "${TSUM_TOR_START:-1}" = "1" ]; then
    echo "[start] Linux版Torを起動します..."
    mkdir -p /tmp/tor-data
    chmod 700 /tmp/tor-data
    tor \
        --RunAsDaemon 0 \
        --SocksPort 127.0.0.1:9050 \
        --DataDirectory /tmp/tor-data \
        --GeoIPFile /usr/share/tor/geoip \
        --GeoIPv6File /usr/share/tor/geoip6 \
        --Log "notice stdout" &
    TOR_PID=$!

    if ! python - <<'PY'
import socket
import sys
import time

for _ in range(60):
    try:
        with socket.create_connection(("127.0.0.1", 9050), timeout=1):
            print("[start] Tor SOCKSポート (9050) を確認しました。")
            sys.exit(0)
    except OSError:
        time.sleep(1)
print("[start] 警告: Tor SOCKSポート (9050) を確認できません。Botは直接接続へ切り替えます。", file=sys.stderr)
PY
    then
        echo "[start] Torを利用できない場合もBot本体は起動します。" >&2
    fi
else
    echo "[start] TSUM_TOR_START=0 のためTorを起動しません。"
fi

echo "[start] 既存Discord Botとツムツム機能を起動します..."
python -u /app/main.py &
APP_PID=$!
set +e
wait "$APP_PID"
APP_STATUS=$?
set -e
exit "$APP_STATUS"
