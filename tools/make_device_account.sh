#!/bin/bash
# ESP32 장치 전용 브로커 계정 생성 + 펌웨어 Secrets.h 생성.
# 비밀번호는 젯슨에서 생성해 /data/.env 와 Secrets.h 에만 들어간다(대화·문서에 출력하지 않음).
set -euo pipefail
DEV=cube001
HOST=helphyai-jetson.tail3126a6.ts.net
PORT=8443
set -a; . /data/.env; set +a

echo "== 1. 장치 비밀번호 생성 또는 기존 값 사용 =="
if grep -q "^CUBE001_PASSWORD=" /data/.env; then
  echo "   기존 값 사용"
else
  PW=$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-20)
  echo "CUBE001_PASSWORD=$PW" >> /data/.env
  echo "   새로 생성해 /data/.env 에 기록"
fi
set -a; . /data/.env; set +a

echo "== 2. 브로커 계정 등록 =="
docker run --rm -v /data/mosquitto/config:/c -e P="$CUBE001_PASSWORD" eclipse-mosquitto:2 \
  sh -c "mosquitto_passwd -b /c/passwd $DEV \"\$P\" && chown 1883:1883 /c/passwd && chmod 600 /c/passwd"
echo "   등록된 계정: $(sudo cut -d: -f1 /data/mosquitto/config/passwd | tr '\n' ' ')"

echo "== 3. 브로커 재시작 =="
cd /data && docker compose restart mosquitto >/dev/null 2>&1
sleep 5
docker compose ps --format "   {{.Name}} {{.Status}}" | grep mosquitto

echo "== 4. Secrets.h 생성 =="
OUT=/tmp/Secrets.h
{
  echo '#pragma once'
  echo '// 젯슨을 원격 웹 서버로 상정한 시험 설정. 2026-09-21 생성.'
  echo '// Wi-Fi 두 줄은 현장 값으로 직접 채운다.'
  echo '#define WIFI_SSID "채워넣기"'
  echo '#define WIFI_PASSWORD "채워넣기"'
  echo "#define MQTT_HOST \"$HOST\""
  echo "#define MQTT_PORT $PORT"
  echo "#define MQTT_USERNAME \"$DEV\""
  echo "#define MQTT_PASSWORD \"$CUBE001_PASSWORD\""
  echo '#define MQTT_TLS true'
  echo '// Let'"'"'s Encrypt 루트 2종(ISRG Root X1, X2). 서버 인증서 계보가 바뀌어도 대응되도록 함께 넣음.'
  echo '#define MQTT_CA_PEM R"CAPEM('
  cat /usr/share/ca-certificates/mozilla/ISRG_Root_X1.crt
  cat /usr/share/ca-certificates/mozilla/ISRG_Root_X2.crt
  echo ')CAPEM"'
} > "$OUT"
chmod 600 "$OUT"
echo "   $(wc -l < "$OUT")줄 생성"
grep -vE "PASSWORD|BEGIN|END|^[A-Za-z0-9+/=]{20,}$" "$OUT" | head -12
echo "DONE"
