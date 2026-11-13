# -*- coding: utf-8 -*-
"""ESP32 장치가 실제로 보내는 필드를 보여주는 Grafana 대시보드 생성기.

2026-10-02: 큐브 이름을 고정 목록(SC-01~03)에서 드롭다운 변수 `cube` 로 바꿈.
목록은 InfluxDB 의 cube 태그 값(최근 30일)에서 자동으로 채우고 기본 선택은 DEFAULT_CUBE.
펌웨어에 추가된 값(온도·습도·조도·토양 수분 %·칩 온도·모터·LED 모드) 패널 추가.
2026-10-13: 맨 아래에 "자동 추가 필드" 행. 변수 `field` 가 InfluxDB 필드 이름 중 위 패널들이 아직 쓰지 않는
것만 채우고(SHOWN_FIELDS 제외), 현재값·추이 패널 두 개가 그 값마다 반복된다(Grafana repeat).
펌웨어에 새 값이 생기면 대시보드를 다시 열 때 패널이 생긴다. 이 파일에 패널을 만들면 자동 행에서는 빠진다.
"""
import json
import os

DEFAULT_CUBE = "CUBE001"
DS = {"type": "influxdb", "uid": "influxdb-main"}
_id = [0]
SHOWN_FIELDS = set()   # flux() 를 거친 data 필드 이름 — 자동 추가 행에서 뺀다

CUBE_LIST_FLUX = ('import "influxdata/influxdb/schema"\n'
                  'schema.tagValues(bucket: "sensors", tag: "cube", '
                  'predicate: (r) => r._measurement == "mqtt" and r.kind == "data", start: -30d)')


def nid():
    _id[0] += 1
    return _id[0]


def flux(field, last=False):
    if not field.startswith("$"):
        SHOWN_FIELDS.add(field)
    q = ('from(bucket: "sensors")\n'
         '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
         '  |> filter(fn: (r) => r._measurement == "mqtt" and r.cube == "${cube}" '
         f'and r.kind == "data" and r._field == "{field}")\n')
    q += '  |> last()\n' if last else '  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n'
    return q + '  |> keep(columns: ["_time", "_value"])'


def stat(field, title, x, y, w=3, h=4, unit="", dec=0, maps=None, color="text"):
    return {
        "id": nid(), "type": "stat", "title": title,
        "gridPos": {"x": x, "y": y, "w": w, "h": h}, "datasource": DS,
        "targets": [{"datasource": DS, "refId": "A", "query": flux(field, True)}],
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "colorMode": "value", "graphMode": "none", "textMode": "value",
                    "justifyMode": "center"},
        "fieldConfig": {"defaults": {
            "unit": unit, "decimals": dec, "displayName": title, "noValue": "-",
            "mappings": maps or [],
            "color": {"mode": "fixed", "fixedColor": color},
            "thresholds": {"mode": "absolute", "steps": [{"color": color, "value": None}]}},
            "overrides": []},
    }


def ts(fields, title, x, y, w=8, h=7, unit="", dec=0, names=None):
    targets = [{"datasource": DS, "refId": chr(ord("A") + i), "query": flux(f)}
               for i, f in enumerate(fields)]
    names = names or fields
    overrides = [{"matcher": {"id": "byFrameRefID", "options": chr(ord("A") + i)},
                  "properties": [{"id": "displayName", "value": names[i]}]}
                 for i in range(len(fields))]
    return {
        "id": nid(), "type": "timeseries", "title": title,
        "gridPos": {"x": x, "y": y, "w": w, "h": h}, "datasource": DS,
        "targets": targets,
        "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": len(fields) > 1},
                    "tooltip": {"mode": "multi", "sort": "none"}},
        "fieldConfig": {"defaults": {
            "unit": unit, "decimals": dec,
            "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 0,
                       "showPoints": "auto", "spanNulls": True}},
            "overrides": overrides},
    }


# 2026-10-02: 카메라(SN = <큐브>-C<n>) 값. api 가 status 의 칩 온도·신호 세기를 cube=<카메라 SN>, kind=status 로 기록한다.
# 고른 큐브의 카메라 전부를 한 패널에 카메라별(C1, C2 …) 값·선으로 보여 준다.
CAM_NAME = "${__field.labels.cam}"


def cam_flux(field, last=False):
    q = ('import "strings"\n'
         'from(bucket: "sensors")\n'
         '  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n'
         '  |> filter(fn: (r) => r._measurement == "mqtt" and r.kind == "status" '
         'and r.cube =~ /^${cube}-C[0-9]+$/ '
         f'and r._field == "{field}")\n')
    q += '  |> last()\n' if last else '  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)\n'
    return q + ('  |> map(fn: (r) => ({_time: r._time, _value: r._value, '
                'cam: strings.trimPrefix(v: r.cube, prefix: "${cube}-")}))\n'
                '  |> group(columns: ["cam"])')


def cam_stat(field, title, x, y, w=12, h=4, unit="", dec=0):
    p = stat(field, title, x, y, w, h, unit, dec)
    p["targets"][0]["query"] = cam_flux(field, True)
    p["options"]["textMode"] = "value_and_name"
    p["fieldConfig"]["defaults"]["displayName"] = CAM_NAME
    return p


def cam_ts(field, title, x, y, w=12, h=7, unit="", dec=0):
    p = ts([field], title, x, y, w, h, unit, dec)
    p["targets"][0]["query"] = cam_flux(field)
    p["fieldConfig"]["defaults"]["displayName"] = CAM_NAME
    p["fieldConfig"]["overrides"] = []
    p["options"]["legend"]["showLegend"] = True
    return p


ONOFF =[{"type": "value", "options": {"0": {"text": "아니오", "color": "text"},
                                       "1": {"text": "예", "color": "green"}}}]
PUMP = [{"type": "value", "options": {"0": {"text": "대기", "color": "text"},
                                      "1": {"text": "급수 중", "color": "blue"}}}]
OKNG = [{"type": "value", "options": {"0": {"text": "실패", "color": "red"},
                                      "1": {"text": "정상", "color": "green"}}}]

panels = []
y = 0
panels.append({"id": nid(), "type": "row", "title": "장치 ${cube} (ESP32가 실제로 보내는 값)",
               "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})
y += 1
# 1행: 연결·상태
panels.append(stat("rssi_dbm", "무선 세기", 0, y, 3, 4, "dBm", 0))
panels.append(stat("uptime_ms", "가동 시간", 3, y, 3, 4, "ms", 0))
panels.append(stat("ads1115_ok", "수분 센서", 6, y, 3, 4, "", 0, OKNG))
panels.append(stat("lease_active", "명령 유효", 9, y, 3, 4, "", 0, ONOFF))
panels.append(stat("pump_locked", "펌프 잠금", 12, y, 3, 4, "", 0, ONOFF))
panels.append(stat("applied_seq", "적용 명령 번호", 15, y, 3, 4, "", 0))
panels.append(stat("moisture_raw", "수분 원시값", 18, y, 3, 4, "", 0))
panels.append(stat("moisture_v", "수분 전압", 21, y, 3, 4, "volt", 3))
y += 4
# 2행: 출력 상태 (모터·LED 모드 추가로 폭 4 → 3)
panels.append(stat("led1", "LED1 밝기", 0, y, 3, 4, "percent", 0, None, "yellow"))
panels.append(stat("led2", "LED2 밝기", 3, y, 3, 4, "percent", 0, None, "yellow"))
panels.append(stat("led_mode", "LED 모드", 6, y, 3, 4, "", 0, None, "yellow"))
panels.append(stat("pump", "펌프", 9, y, 3, 4, "", 0, PUMP))
panels.append(stat("fan1", "팬1 출력", 12, y, 3, 4, "percent", 0, None, "blue"))
panels.append(stat("fan2", "팬2 출력", 15, y, 3, 4, "percent", 0, None, "blue"))
panels.append(stat("motor_duty", "모터 출력", 18, y, 3, 4, "percent", 0, None, "purple"))
panels.append(stat("motor_set", "모터 목표", 21, y, 3, 4, "percent", 0, None, "purple"))
y += 4
# 3행: 조도·토양 수분
panels.append(stat("lux", "조도", 0, y, 8, 4, "lux", 0, None, "yellow"))
panels.append(stat("moisture_pct", "토양 수분", 8, y, 8, 4, "percent", 0, None, "green"))
panels.append(stat("moisture_calibrated", "수분 보정", 16, y, 8, 4, "", 0, ONOFF))
y += 4
# 4~5행: 추이
panels.append(ts(["lux"], "조도 추이 (lx)", 0, y, unit="lux"))
panels.append(ts(["moisture_pct"], "토양 수분 추이 (%)", 8, y, unit="percent"))
panels.append(ts(["moisture_raw"], "수분 원시값 추이", 16, y))
y += 7
panels.append(ts(["motor_duty", "motor_set"], "모터 출력·목표 (%)", 0, y, unit="percent"))
panels.append(ts(["fan1_rpm", "fan2_rpm"], "팬 회전수 (rpm)", 8, y))
panels.append(ts(["rssi_dbm"], "무선 세기 추이 (dBm)", 16, y))
y += 7

# 2026-10-02: 온습도 센서 값과 ESP32 칩 내부 온도를 구역으로 나눔 (사용자 지시)
panels.append({"id": nid(), "type": "row", "title": "SHT45 온습도 센서",
               "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})
y += 1
panels.append(stat("temperature", "온도 (SHT45)", 0, y, 12, 4, "celsius", 1, None, "orange"))
panels.append(stat("humidity", "습도 (SHT45)", 12, y, 12, 4, "humidity", 1, None, "blue"))
y += 4
panels.append(ts(["temperature"], "온도 추이 (SHT45)", 0, y, w=12, unit="celsius", dec=1,
                 names=["온도 (SHT45)"]))
panels.append(ts(["humidity"], "습도 추이 (SHT45)", 12, y, w=12, unit="humidity", dec=1,
                 names=["습도 (SHT45)"]))
y += 7

panels.append({"id": nid(), "type": "row", "title": "ESP32 칩",
               "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})
y += 1
panels.append(stat("chip_temp", "칩 내부 온도 (ESP32)", 0, y, 6, 4, "celsius", 1))
y += 4
panels.append(ts(["chip_temp"], "칩 내부 온도 추이 (ESP32)", 0, y, w=24, unit="celsius", dec=1,
                 names=["칩 내부 온도 (ESP32)"]))
y += 7

# 2026-10-02: 카메라 구역 (사용자 지시 — 값별로 묶고 카메라마다 값·선을 나눔)
panels.append({"id": nid(), "type": "row", "title": "ESP32-CAM 카메라",
               "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})
y += 1
panels.append(cam_stat("chip_temp", "칩 내부 온도 (ESP32-CAM)", 0, y, unit="celsius", dec=1))
panels.append(cam_stat("rssi_dbm", "신호 세기 (ESP32-CAM)", 12, y, unit="dBm"))
y += 4
panels.append(cam_ts("chip_temp", "칩 내부 온도 추이 (ESP32-CAM)", 0, y, unit="celsius", dec=1))
panels.append(cam_ts("rssi_dbm", "신호 세기 추이 (ESP32-CAM)", 12, y, unit="dBm"))
y += 7

# 2026-10-13: 자동 추가 행 (사용자 지시 — 반복 패널 방식, 아직 패널이 없는 필드만, 현재값 + 추이)
# 변수 `field` 값마다 아래 두 패널이 가로로 반복된다. 단위·소수점은 공통(없음), 제목은 필드 이름 그대로.
panels.append({"id": nid(), "type": "row", "title": "자동 추가 필드 (아직 패널이 없는 값 — 대시보드를 다시 열면 갱신)",
               "collapsed": False, "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []})
y += 1
p = stat("${field}", "${field}", 0, y, 4, 4)
p.update({"repeat": "field", "repeatDirection": "h", "maxPerRow": 6})
panels.append(p)
y += 4
p = ts(["${field}"], "${field} 추이", 0, y, w=8)
p.update({"repeat": "field", "repeatDirection": "h", "maxPerRow": 3})
panels.append(p)
y += 7

# 고른 큐브의 최근 30일 필드 이름에서, 이 파일이 이미 패널을 만든 이름(SHOWN_FIELDS)을 뺀다.
FIELD_LIST_FLUX = ('import "influxdata/influxdb/schema"\n'
                   'schema.fieldKeys(bucket: "sensors", predicate: (r) => r._measurement == "mqtt" '
                   'and r.cube == "${cube}" and r.kind == "data", start: -30d)\n'
                   '  |> filter(fn: (r) => not contains(value: r._value, set: [%s]))'
                   % ", ".join('"%s"' % f for f in sorted(SHOWN_FIELDS)))

cube_var = {
    "name": "cube", "label": "큐브", "type": "query", "datasource": DS,
    "query": CUBE_LIST_FLUX, "definition": CUBE_LIST_FLUX,
    "refresh": 1, "sort": 1, "multi": False, "includeAll": False, "hide": 0,
    "current": {"selected": True, "text": DEFAULT_CUBE, "value": DEFAULT_CUBE},
    "options": [],
}

field_var = {
    "name": "field", "label": "자동 추가 필드", "type": "query", "datasource": DS,
    "query": FIELD_LIST_FLUX, "definition": FIELD_LIST_FLUX,
    "refresh": 1, "sort": 1, "multi": True, "includeAll": True, "hide": 0,
    "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
    "options": [],
}

dashboard = {
    "uid": "smartcube-device", "title": "스마트큐브 장치 상태 (ESP32)",
    "tags": ["smartcube", "device"], "timezone": "browser", "editable": True,
    "graphTooltip": 1, "schemaVersion": 39, "version": 2,
    "refresh": "10s", "time": {"from": "now-6h", "to": "now"},
    "templating": {"list": [cube_var, field_var]}, "annotations": {"list": []}, "links": [], "panels": panels,
}

out = os.environ.get("OUT", "/data/grafana/provisioning/dashboards/device.json")
with open(out, "w", encoding="utf-8", newline="\n") as f:
    f.write(json.dumps(dashboard, ensure_ascii=False, indent=1))
print("패널 %d개 -> %s" % (len(panels), out))
