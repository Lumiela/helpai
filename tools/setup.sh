#!/bin/bash
# 젯슨 엣지 서버 스택 설치: docker compose v2 + /data 레이아웃 + Mosquitto/InfluxDB/Telegraf/Grafana
# 실행: sudo bash /tmp/stack/setup.sh
set -euo pipefail
SRC=/tmp/stack
OWNER=helphyai

echo "== 1. packages =="
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq docker-compose-v2 curl >/dev/null
docker compose version

echo "== 2. docker data-root -> /data/docker =="
mkdir -p /data/docker
cat > /etc/docker/daemon.json <<'JSON'
{
    "data-root": "/data/docker",
    "runtimes": {
        "nvidia": {
            "path": "nvidia-container-runtime",
            "runtimeArgs": []
        }
    }
}
JSON
systemctl restart docker
docker info 2>/dev/null | grep -E "Docker Root Dir|Runtimes"
usermod -aG docker "$OWNER"

echo "== 3. layout =="
mkdir -p /data/mosquitto/config /data/mosquitto/data /data/mosquitto/log \
         /data/influxdb/data /data/influxdb/config \
         /data/telegraf \
         /data/grafana/data /data/grafana/provisioning/datasources
cp "$SRC/docker-compose.yml"     /data/docker-compose.yml
cp "$SRC/mosquitto.conf"         /data/mosquitto/config/mosquitto.conf
cp "$SRC/telegraf.conf"          /data/telegraf/telegraf.conf
cp "$SRC/grafana-datasource.yml" /data/grafana/provisioning/datasources/influxdb.yml
chown -R 1883:1883 /data/mosquitto
chown -R 472:472  /data/grafana/data
chown "$OWNER:$OWNER" /data /data/docker-compose.yml

echo "== 4. credentials (/data/.env) =="
if [ ! -f /data/.env ]; then
  gen() { openssl rand -base64 24 | tr -d '/+=' | cut -c1-20; }
  cat > /data/.env <<ENV
MQTT_USER=smartcube
MQTT_PASSWORD=$(gen)
INFLUX_PASSWORD=$(gen)
INFLUX_TOKEN=$(openssl rand -hex 32)
GRAFANA_PASSWORD=$(gen)
ENV
  chmod 600 /data/.env
  chown "$OWNER:$OWNER" /data/.env
  echo "generated"
else
  echo "exists, kept"
fi
set -a; . /data/.env; set +a

echo "== 5. pull images =="
cd /data
docker compose pull -q

echo "== 6. mosquitto password file =="
docker run --rm -v /data/mosquitto/config:/c -e P="$MQTT_PASSWORD" eclipse-mosquitto:2 \
  sh -c 'mosquitto_passwd -b -c /c/passwd smartcube "$P" && chown 1883:1883 /c/passwd && chmod 600 /c/passwd'

echo "== 7. up =="
docker compose up -d
sleep 20
docker compose ps
echo "== SETUP DONE =="
