# -*- coding: utf-8 -*-
"""
스마트큐브 Grafana 대시보드 생성기.
fr/src/data/mockFarmData.ts 의 큐브 구성·허용 범위와 fr/src/types/smartfarm.ts 의 SensorReading 필드명을 그대로 사용한다.
출력: grafana/dashboards/smartcube.json
"""
import json, os

CUBES = [
    dict(code="SC-01", crop="완숙 토마토", stage="생식성장기, 정식 후 45일",
         temp=(20, 26), humid=(65, 80), co2=(700, 1000), ec=(2.2, 2.8), ph=(5.8, 6.5)),
    dict(code="SC-02", crop="레드 파프리카", stage="착과기, 정식 후 60일",
         temp=(21, 27), humid=(60, 75), co2=(800, 1200), ec=(2.5, 3.2), ph=(5.5, 6.2)),
    dict(code="SC-03", crop="설향 딸기", stage="수확기, 1화방 수확 중",
         temp=(16, 22), humid=(55, 70), co2=(600, 900), ec=(1.0, 1.5), ph=(5.8, 6.4)),
]

# (필드명, 라벨, 단위, 소수점, 범위키 또는 None)
READINGS = [
    ("temperature",     "온도",      "°C",   1, "temp"),
    ("humidity",        "습도",      "%",    1, "humid"),
    ("co2",             "CO₂",       "ppm",  0, "co2"),
    ("ledIntensityKlx", "광량",      "klx",  1, None),
    ("ec",              "양액 EC",   "dS/m", 2, "ec"),
    ("ph",              "pH",        "",     1, "ph"),
    ("soilMoisture",    "배지 수분", "%",    1, None),
    ("soilTemp",        "배지 온도", "°C",   1, None),
    ("leafTemp",        "엽온",      "°C",   1, None),
    ("vpd",             "VPD",       "kPa",  2, None),
]
# 24시간 그래프로 그리는 항목 (fr ReadingKey: temp/humid/co2/light/ec/soil)
CHARTS = ["temperature", "humidity", "co2", "ledIntensityKlx", "ec", "soilMoisture"]

# 구동기 상태 (kind=status) — ESP32는 0/1 숫자로 보낸다
ACTUATORS = [
    ("outerFan",       "배기팬",   "onoff"),
    ("outerFanSpeed",  "배기팬 단", "speed"),
    ("innerFan",       "순환팬",   "onoff"),
    ("innerFanSpeed",  "순환팬 단", "speed"),
    ("ledGrowLight",   "LED",      "onoff"),
    ("ledIntensity",   "LED 밝기", "percent"),
    ("irrigationPump", "급수",     "pump"),
    ("nutrientValve",  "양액 밸브", "onoff"),
    ("co2Generator",   "CO₂ 공급", "onoff"),
    ("coolingDamper",  "냉각 댐퍼", "onoff"),
    ("heatingDamper",  "가온 댐퍼", "onoff"),
    ("autoMode",       "제어 모드", "mode"),
]

DS = {"type": "influxdb", "uid": "${DS_INFLUXDB}"}
_id = [0]
def nid():
    _id[0] += 1
    return _id[0]

def flux(cube, kind, field, last=False):
    q = (f'from(bucket: "sensors")\n'
         f'  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
         f'  |> filter(fn: (r) => r._measurement == "mqtt" and r.cube == "{cube}" and r.kind == "{kind}" and r._field == "{field}")\n')
    if last:
        q += '  |> last()\n'
    else:
        q += '  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n'
    q += '  |> keep(columns: ["_time", "_value"])'
    return q

def target(cube, kind, field, last=False, ref="A"):
    return {"datasource": DS, "refId": ref, "query": flux(cube, kind, field, last)}

def range_thresholds(rng, band):
    """band=True: 시계열의 허용 범위 띠(하늘색). False: 스탯 값 색(범위 이탈=빨강)."""
    if rng is None:
        return {"mode": "absolute", "steps": [{"color": "text" if not band else "transparent", "value": None}]}
    lo, hi = rng
    if band:
        return {"mode": "absolute", "steps": [
            {"color": "transparent", "value": None},
            {"color": "rgba(56, 189, 248, 0.28)", "value": lo},
            {"color": "transparent", "value": hi}]}
    return {"mode": "absolute", "steps": [
        {"color": "red", "value": None},
        {"color": "green", "value": lo},
        {"color": "red", "value": hi}]}

def stat_panel(cube, field, label, unit, dec, rng, x, y, w=3, h=4):
    desc = f"허용 범위 {rng[0]}~{rng[1]}{unit}" if rng else ""
    return {
        "id": nid(), "type": "stat", "title": label, "description": desc,
        "gridPos": {"x": x, "y": y, "w": w, "h": h}, "datasource": DS,
        "targets": [target(cube, "data", field, last=True)],
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "value", "graphMode": "none", "textMode": "value",
                    "justifyMode": "center", "orientation": "auto"},
        "fieldConfig": {"defaults": {"unit": "", "decimals": dec,
                                     "custom": {}, "thresholds": range_thresholds(rng, band=False),
                                     "color": {"mode": "thresholds"},
                                     "displayName": f"{label}" + (f" ({unit})" if unit else "")},
                        "overrides": []},
    }

def ts_panel(cube, field, label, unit, dec, rng, x, y, w=8, h=8):
    desc = f"최근 24시간, 허용 범위 {rng[0]}~{rng[1]}{unit} 를 하늘색 띠로 표시" if rng else "최근 24시간"
    return {
        "id": nid(), "type": "timeseries", "title": f"{label} ({unit})" if unit else label, "description": desc,
        "gridPos": {"x": x, "y": y, "w": w, "h": h}, "datasource": DS,
        "targets": [target(cube, "data", field)],
        "options": {"legend": {"displayMode": "hidden", "showLegend": False},
                    "tooltip": {"mode": "single", "sort": "none"}},
        "fieldConfig": {"defaults": {
            "decimals": dec, "displayName": label,
            "color": {"mode": "fixed", "fixedColor": "#1f2937"},
            "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 0, "showPoints": "never",
                       "spanNulls": True, "thresholdsStyle": {"mode": "area" if rng else "off"}},
            "thresholds": range_thresholds(rng, band=True)},
            "overrides": []},
    }

def act_panel(cube, x, y, w=24, h=4):
    """구동기 상태 한 줄: 값 매핑으로 정지/가동, 단수, 자동/수동 표시."""
    targets = []
    overrides = []
    for i, (field, label, kind) in enumerate(ACTUATORS):
        ref = chr(ord("A") + i)
        targets.append({"datasource": DS, "refId": ref, "query": flux(cube, "status", field, last=True)})
        if kind == "onoff":
            maps = [{"type": "value", "options": {"0": {"text": "정지", "color": "text"}, "1": {"text": "가동", "color": "green"}}}]
        elif kind == "pump":
            maps = [{"type": "value", "options": {"0": {"text": "대기", "color": "text"}, "1": {"text": "급수 중", "color": "blue"}}}]
        elif kind == "speed":
            maps = [{"type": "value", "options": {"0": {"text": "정지"}, "1": {"text": "1단"}, "2": {"text": "2단"}, "3": {"text": "3단"}}}]
        elif kind == "mode":
            maps = [{"type": "value", "options": {"0": {"text": "수동", "color": "orange"}, "1": {"text": "자동", "color": "green"}}}]
        else:  # percent
            maps = []
        props = [{"id": "displayName", "value": label}, {"id": "mappings", "value": maps}]
        if kind == "percent":
            props.append({"id": "unit", "value": "percent"})
        overrides.append({"matcher": {"id": "byFrameRefID", "options": ref}, "properties": props})
    return {
        "id": nid(), "type": "stat", "title": "구동기 상태 (topic .../ctrl/status)",
        "gridPos": {"x": x, "y": y, "w": w, "h": h}, "datasource": DS,
        "targets": targets,
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "value", "graphMode": "none", "textMode": "value_and_name",
                    "justifyMode": "center", "orientation": "horizontal"},
        "fieldConfig": {"defaults": {"thresholds": {"mode": "absolute", "steps": [{"color": "text", "value": None}]},
                                     "color": {"mode": "thresholds"}, "noValue": "-"},
                        "overrides": overrides},
    }

panels = []
y = 0
# 상단 개요: 큐브별 온도·습도·CO₂ 현재값 (fr GreenhouseOverview 카드의 핵심 3항목)
panels.append({"id": nid(), "type": "row", "title": "전체 개요 (큐브별 현재값)", "collapsed": False,
               "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})
y += 1
for ci, c in enumerate(CUBES):
    x = ci * 8
    panels.append(stat_panel(c["code"], "temperature", f'{c["code"]} 온도', "°C", 1, c["temp"], x, y, w=3, h=4))
    panels.append(stat_panel(c["code"], "humidity",    f'{c["code"]} 습도', "%",  1, c["humid"], x + 3, y, w=2, h=4))
    panels.append(stat_panel(c["code"], "co2",         f'{c["code"]} CO₂', "ppm", 0, c["co2"], x + 5, y, w=3, h=4))
y += 4

for c in CUBES:
    panels.append({"id": nid(), "type": "row", "title": f'스마트큐브 {c["code"]} · {c["crop"]} · {c["stage"]}',
                   "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})
    y += 1
    # 현재값 10개 (fr ZoneDetailView 타일)
    for i, (field, label, unit, dec, rk) in enumerate(READINGS):
        x = (i % 8) * 3
        yy = y + (i // 8) * 4
        rng = c[rk] if rk else None
        panels.append(stat_panel(c["code"], field, label, unit, dec, rng, x, yy, w=3, h=4))
    y += 8
    # 24시간 그래프 6개 (fr ReadingChart)
    for i, field in enumerate(CHARTS):
        f, label, unit, dec, rk = next(r for r in READINGS if r[0] == field)
        rng = c[rk] if rk else None
        panels.append(ts_panel(c["code"], f, label, unit, dec, rng, (i % 3) * 8, y + (i // 3) * 8))
    y += 16
    panels.append(act_panel(c["code"], 0, y))
    y += 4

dashboard = {
    "__inputs": [{"name": "DS_INFLUXDB", "label": "InfluxDB", "type": "datasource", "pluginId": "influxdb"}],
    "uid": "smartcube-edge", "title": "스마트큐브 관제 (엣지 서버)", "tags": ["smartcube", "edge"],
    "timezone": "browser", "editable": True, "graphTooltip": 1, "schemaVersion": 39, "version": 1,
    "refresh": "10s", "time": {"from": "now-24h", "to": "now"},
    "templating": {"list": []}, "annotations": {"list": []}, "links": [], "panels": panels,
}

out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "grafana", "dashboards")
os.makedirs(out_dir, exist_ok=True)
# 프로비저닝 파일은 ${DS_INFLUXDB} 입력을 못 받으므로 데이터소스 이름으로 치환
s = json.dumps(dashboard, ensure_ascii=False, indent=1).replace('"uid": "${DS_INFLUXDB}"', '"uid": "influxdb-main"')
with open(os.path.join(out_dir, "smartcube.json"), "w", encoding="utf-8", newline="\n") as f:
    f.write(s)
print("panels:", len(panels), "-> smartcube.json", len(s), "bytes")
