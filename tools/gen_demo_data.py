# -*- coding: utf-8 -*-
"""
Grafana 대시보드 확인용 모의 데이터 생성기 (line protocol을 표준출력으로).
fr/src/data/mockFarmData.ts 의 큐브 설정과 fr/src/lib/history.ts 의 곡선 모형을 따른다.
최근 24시간, 15분 간격 97점 x 3큐브 (kind=data) + 3시간 간격 status.
"""
import sys, time, math, random

NOW = int(time.time())
STEP = 15 * 60
POINTS = 97  # 24h / 15min + 1

CUBES = [
    dict(code="SC-01", on=6, off=22, ramp=2.0,
         tDay=23.5, tNight=20.0, humMin=65, humMax=80, co2Min=700, co2Max=1000,
         ec=2.45, ph=6.2, soil=68.4, soilTemp=21.8, leaf=23.8, vpd=0.85, klx=6.4,
         status=dict(outerFan=1, outerFanSpeed=2, innerFan=1, innerFanSpeed=2, ledGrowLight=1,
                     ledIntensity=70, irrigationPump=0, nutrientValve=0, co2Generator=1,
                     coolingDamper=1, heatingDamper=0, autoMode=1)),
    dict(code="SC-02", on=6, off=22, ramp=2.0,
         tDay=24.0, tNight=20.5, humMin=60, humMax=75, co2Min=800, co2Max=1200,
         ec=2.85, ph=5.9, soil=62.1, soilTemp=22.5, leaf=25.1, vpd=1.12, klx=7.1,
         status=dict(outerFan=1, outerFanSpeed=3, innerFan=1, innerFanSpeed=3, ledGrowLight=1,
                     ledIntensity=80, irrigationPump=1, nutrientValve=1, co2Generator=1,
                     coolingDamper=1, heatingDamper=0, autoMode=1)),
    dict(code="SC-03", on=7, off=19, ramp=1.5,
         tDay=19.5, tNight=16.0, humMin=55, humMax=70, co2Min=600, co2Max=900,
         ec=1.28, ph=6.0, soil=55.0, soilTemp=18.2, leaf=19.7, vpd=0.95, klx=5.9,
         status=dict(outerFan=0, outerFanSpeed=1, innerFan=1, innerFanSpeed=1, ledGrowLight=0,
                     ledIntensity=0, irrigationPump=0, nutrientValve=0, co2Generator=0,
                     coolingDamper=0, heatingDamper=1, autoMode=1)),
]


def daylight(on, off, hour, ramp):
    """fr lightSaturation.daylightFactor 근사: 점등 구간에서 0->1->0 사다리꼴."""
    if hour < on or hour > off:
        return 0.0
    up = min(1.0, (hour - on) / ramp) if ramp > 0 else 1.0
    down = min(1.0, (off - hour) / ramp) if ramp > 0 else 1.0
    return max(0.0, min(up, down))


def emit(measurement, tags, fields, ts_ns):
    tagstr = ",".join("%s=%s" % (k, v) for k, v in tags.items())
    fieldstr = ",".join("%s=%s" % (k, v) for k, v in fields.items())
    return "%s,%s %s %d" % (measurement, tagstr, fieldstr, ts_ns)


lines = []
for c in CUBES:
    rnd = random.Random(hash(c["code"]) & 0xFFFFFFFF)
    noise = {k: 0.0 for k in ("t", "h", "co2", "ec", "soil")}

    def nz(key, amp):
        noise[key] = noise[key] * 0.7 + (rnd.random() * 2 - 1) * amp * 0.5
        return noise[key]

    topic = "smartcube/%s/ctrl/data" % c["code"]
    for i in range(POINTS):
        ts = NOW - (POINTS - 1 - i) * STEP
        lt = time.localtime(ts)
        hour = lt.tm_hour + lt.tm_min / 60.0
        day = daylight(c["on"], c["off"], hour, c["ramp"])

        temp = c["tNight"] + (c["tDay"] - c["tNight"]) * day + nz("t", 0.9)
        humMid = (c["humMin"] + c["humMax"]) / 2.0
        hum = humMid - 5 * (day - 0.5) + nz("h", 3.5)
        co2Mid = (c["co2Min"] + c["co2Max"]) / 2.0
        co2 = co2Mid + 140 * (day - 0.5) + nz("co2", 60)
        klx = c["klx"] * day + (nz("ec", 0.3) * day if day > 0 else 0)
        ec = c["ec"] + 0.12 * math.sin((ts / 3600.0 % 24) / 24 * 2 * math.pi) + nz("ec", 0.08)
        cycle = (ts / 3600.0 % 6) / 6.0
        soil = c["soil"] + 6 - 12 * cycle + nz("soil", 1.2)
        leaf = temp - 0.4 + nz("t", 0.3) * 0.3
        vpd = c["vpd"] + (day - 0.5) * 0.15 + nz("t", 0.05) * 0.1
        soilT = c["soilTemp"] + (day - 0.5) * 0.8 + nz("t", 0.2) * 0.2

        fields = {
            "temperature": round(temp, 1),
            "humidity": round(hum, 1),
            "co2": round(co2, 0),
            "ledIntensityKlx": round(max(0.0, klx), 1),
            "soilMoisture": round(soil, 1),
            "soilTemp": round(soilT, 1),
            "ec": round(ec, 2),
            "ph": round(c["ph"] + nz("ec", 0.03) * 0.3, 2),
            "leafTemp": round(leaf, 1),
            "vpd": round(max(0.0, vpd), 2),
        }
        lines.append(emit("mqtt",
                          {"cube": c["code"], "kind": "data", "topic": topic, "host": "jetson"},
                          fields, ts * 1000000000))

    stopic = "smartcube/%s/ctrl/status" % c["code"]
    for i in range(9):  # 3시간 간격 24시간
        ts = NOW - (8 - i) * 3 * 3600
        st = dict(c["status"])
        lt = time.localtime(ts)
        hour = lt.tm_hour + lt.tm_min / 60.0
        lit = 1 if daylight(c["on"], c["off"], hour, c["ramp"]) > 0 else 0
        if c["status"]["ledGrowLight"] == 1:
            st["ledGrowLight"] = lit
            st["ledIntensity"] = c["status"]["ledIntensity"] if lit else 0
        lines.append(emit("mqtt",
                          {"cube": c["code"], "kind": "status", "topic": stopic, "host": "jetson"},
                          # 기존 telegraf 기록이 실수형이므로 정수 접미사 i를 쓰지 않는다
                          {k: "%d" % v for k, v in st.items()}, ts * 1000000000))

sys.stdout.write("\n".join(lines) + "\n")
sys.stderr.write("generated %d lines (%d cubes)\n" % (len(lines), len(CUBES)))
