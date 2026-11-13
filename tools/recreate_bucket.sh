#!/bin/bash
# sensors 버킷 재생성 + 모의 데이터 재삽입. 실행: bash recreate_bucket.sh
set -uo pipefail
set -a; . /data/.env; set +a
IQ() { docker exec influxdb influx "$@" --org smartcube --token "$INFLUX_TOKEN"; }
log() { echo "[$(date +%H:%M:%S)] $*"; }

log "1. 삭제 전 상태"
IQ bucket list 2>/dev/null | awk 'NR==1 || /sensors/ {print "   "$0}'

log "2. 버킷 삭제"
IQ bucket delete --name sensors 2>&1 | tail -2

log "3. 버킷 생성 (보존 기간 무제한, 이전과 동일)"
IQ bucket create --name sensors --retention 0 2>&1 | tail -2

log "4. 실제 장치 경로(MQTT)로 먼저 발행 — 자료형을 telegraf 기준으로 등록"
docker exec mosquitto mosquitto_pub -h localhost -u "$MQTT_USER" -P "$MQTT_PASSWORD" \
  -t smartcube/SC-01/ctrl/status \
  -m '{"outerFan":1,"outerFanSpeed":2,"innerFan":1,"innerFanSpeed":2,"ledGrowLight":1,"ledIntensity":70,"irrigationPump":0,"nutrientValve":0,"co2Generator":1,"coolingDamper":1,"heatingDamper":0,"autoMode":1}'
docker exec mosquitto mosquitto_pub -h localhost -u "$MQTT_USER" -P "$MQTT_PASSWORD" \
  -t smartcube/SC-01/ctrl/data \
  -m '{"temperature":23.5,"humidity":70.0,"co2":900,"ledIntensityKlx":6.4,"soilMoisture":68.0,"soilTemp":21.8,"ec":2.45,"ph":6.2,"leafTemp":23.1,"vpd":0.85}'
log "   발행함, telegraf 반영 대기(20초)"
sleep 20

log "5. telegraf 오류 확인"
if docker logs --since 40s telegraf 2>&1 | grep -qiE "conflict|E! .*influxdb"; then
  docker logs --since 40s telegraf 2>&1 | grep -iE "conflict|E! .*influxdb" | tail -3
  echo "ABORT: telegraf 쓰기 오류"; exit 1
else
  echo "   오류 없음"
fi

log "6. 자료형 확인 (전부 double 이어야 함)"
bad=0
for f in outerFan outerFanSpeed autoMode ledIntensity temperature humidity; do
  t=$(IQ query --raw "from(bucket:\"sensors\") |> range(start:-10m) |> filter(fn:(r)=> r._field==\"$f\") |> last()" 2>/dev/null | grep '^#datatype' | head -1 | awk -F, '{print $7}')
  echo "   $f: ${t:-없음}"
  [ "$t" = "double" ] || bad=1
done
[ "$bad" -eq 0 ] || { echo "ABORT: 자료형이 double이 아님"; exit 1; }

log "7. 24시간 모의 데이터 재삽입 (실수형)"
python3 /data/tools/gen_demo_data.py 2>/dev/null \
  | docker exec -i influxdb influx write --org smartcube --token "$INFLUX_TOKEN" --bucket sensors --precision ns 2>&1 | tail -3
sleep 3

log "8. 저장 결과"
IQ query --raw "from(bucket:\"sensors\") |> range(start:-24h) |> filter(fn:(r)=> r._measurement==\"mqtt\") |> group(columns:[\"cube\",\"kind\"]) |> count()" 2>/dev/null > /tmp/rc.csv
python3 - <<'PY'
import csv
hdr=None; agg={}
for r in csv.reader(open("/tmp/rc.csv")):
    if not r: continue
    if len(r)>2 and r[1]=="result":
        hdr={n:i for i,n in enumerate(r)}; continue
    if hdr and r and r[0]=="" and len(r)>max(hdr.values()):
        try: v=int(r[hdr["_value"]])
        except ValueError: continue
        k=(r[hdr["cube"]], r[hdr["kind"]])
        agg[k]=max(agg.get(k,0), v)
for k in sorted(agg):
    print("   %s %-7s 최대 %d 포인트" % (k[0], k[1], agg[k]))
PY

log "9. 필드 목록 (구식 temp/hum/lux 없어야 함)"
IQ query 'import "influxdata/influxdb/schema" schema.fieldKeys(bucket:"sensors")' 2>/dev/null \
  | grep -vE '^Result|^Table|^$|----|_value:string' | awk '{print $NF}' | sort | tr '\n' ' '
echo

log "10. 실제 장치 경로 재시험 (충돌 없어야 함)"
docker exec mosquitto mosquitto_pub -h localhost -u "$MQTT_USER" -P "$MQTT_PASSWORD" \
  -t smartcube/SC-02/ctrl/status \
  -m '{"outerFan":1,"outerFanSpeed":3,"innerFan":1,"innerFanSpeed":3,"ledGrowLight":1,"ledIntensity":80,"irrigationPump":1,"nutrientValve":1,"co2Generator":1,"coolingDamper":1,"heatingDamper":0,"autoMode":1}'
sleep 18
if docker logs --since 30s telegraf 2>&1 | grep -qi conflict; then
  echo "   실패: 아직 충돌 있음"; exit 1
else
  echo "   충돌 없음"
fi
IQ query --raw "from(bucket:\"sensors\") |> range(start:-1m) |> filter(fn:(r)=> r.cube==\"SC-02\" and r.kind==\"status\") |> last()" 2>/dev/null \
  | grep -cE '^,,[0-9]' | sed 's/^/   방금 저장된 필드 수: /'

log "RECREATE DONE"
