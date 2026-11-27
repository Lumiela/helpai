# -*- coding: utf-8 -*-
"""
InfluxDB 이력 재생 — 1분 평균 CSV(influx_export 결과) → 분마다 Snapshot.

CSV 형식 (influx_export.py 가 만든다):
    time,CUBE001.air,CUBE001.fan1,...,CUBE001.water
    2026-11-20T05:01:00+00:00,25.9,47.6,...,100
  · time 은 UTC, 창 끝 시각. 값이 없는 칸은 빈 문자열(그 분에 데이터 없음).
  · 어떤 열을 어떤 논리 센서로 쓰는지는 configs/replay.yaml 의 zone 매핑. autoctl 처럼 **큐브 한 대** 기준이 기본.
  · 토양 보정 여부는 autoctl 과 같이 status 의 air·water 가 공장 기본값(22520·11100)인지로 본다
    (zone.soil_calibration: {air: CUBE.air, water: CUBE.water}). 없으면 '모름'(→ 급수 정답 false).

급수 횟수·경과 분·급수 후 수분 변화·30 분 추세·고착 여부는 이력에서 계산한다. 데이터 나이는
"마지막으로 값이 들어온 분"과의 차이(초)로 근사한다(1분 해상도).
"""
import csv
from collections import deque
from datetime import datetime, timedelta, timezone

from .features import Snapshot, WATER_CHECK_MIN, minutes_since_pulse, soil_calibrated_from_status

KST = timezone(timedelta(hours=9))


def _num(x):
    if x is None or x == "":
        return None
    try:
        return float(x)
    except ValueError:
        return None


def read_wide_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            t = datetime.fromisoformat(row.pop("time").replace("Z", "+00:00"))
            yield t, {k: _num(v) for k, v in row.items()}


def _first(mapping):
    """zone 매핑 값: 문자열 'CUBE001.temperature' / 목록 / {from: …, valid_if: …}."""
    if isinstance(mapping, list):
        return mapping[0]
    if isinstance(mapping, dict):
        return mapping["from"]
    return mapping


def replay(rows, zone, tz=KST, window_min=30):
    """
    rows: (utc datetime, {열: 값}) 를 시각 순으로. zone: {"sensors": {...}, "actuators": {...}}.
    분마다 (Snapshot, info) 를 내놓는다. 처음 window_min 분은 추세를 못 구하므로 건너뛴다.
    """
    sens, act = zone["sensors"], zone["actuators"]
    col = {
        "temperature": _first(sens.get("temperature")) if sens.get("temperature") else None,
        "humidity": _first(sens.get("humidity")) if sens.get("humidity") else None,
        "lux": _first(sens.get("lux")) if sens.get("lux") else None,
        "soil": _first(sens.get("soil_moisture")) if sens.get("soil_moisture") else None,
        "light": _first(act.get("light")) if act.get("light") else None,
        "fan": _first(act.get("fan")) if act.get("fan") else None,
        "pump": _first(act.get("pump")) if act.get("pump") else None,
    }
    sc = zone.get("soil_calibration") or {}
    air_col, water_col = sc.get("air"), sc.get("water")
    ok_col = zone.get("soil_sensor_ok")

    hist = deque()                      # (utc, temperature, humidity, soil)
    last_seen = {}                      # 열 → 마지막으로 값이 있던 utc
    last_val = {}
    day, count, last_pulse, pump_prev = None, 0, None, False
    moist_at_pulse = None
    start = None

    for t, v in rows:
        start = start or t
        for c, x in v.items():
            if x is not None:
                last_seen[c] = t
                last_val[c] = x
        local = t.astimezone(tz).replace(tzinfo=None)
        if local.date() != day:             # 현지 자정에 하루 급수 횟수만 0 으로 — 재급수 대기는 이어진다(autoctl 과 같음)
            day, count = local.date(), 0

        pump_now = (v.get(col["pump"]) or 0) > 0 if col["pump"] else False
        if pump_now and not pump_prev:
            count += 1
            last_pulse = t
            moist_at_pulse = last_val.get(col["soil"]) if col["soil"] else None
        pump_prev = pump_now

        def cur(key):
            c = col[key]
            return v.get(c) if c else None

        T, H, M = cur("temperature"), cur("humidity"), cur("soil")
        hist.append((t, T, H, M))
        while hist and (t - hist[0][0]) > timedelta(minutes=window_min):
            hist.popleft()
        if (t - start) < timedelta(minutes=window_min):
            continue

        def trend(i):
            old = next((h[i] for h in hist if h[i] is not None), None)
            now = hist[-1][i]
            return None if old is None or now is None else round(now - old, 2)

        temps = [h[1] for h in hist if h[1] is not None]
        flat = len(temps) >= 10 and (max(temps) - min(temps)) < 0.05

        used = [c for c in col.values() if c]
        ages = [(t - last_seen[c]).total_seconds() if c in last_seen else 1e9 for c in used]
        change = None
        since = minutes_since_pulse(t, last_pulse, count)
        if since is not None:
            if since <= WATER_CHECK_MIN and M is not None and moist_at_pulse is not None:
                change = round(M - moist_at_pulse, 2)

        cal = soil_calibrated_from_status(last_val.get(air_col) if air_col else None,
                                          last_val.get(water_col) if water_col else None)
        sensor_ok = True
        if ok_col and last_val.get(ok_col) is not None:
            sensor_ok = bool(last_val[ok_col])

        led = cur("light")
        fan = cur("fan")
        snap = Snapshot(
            local_time=local,
            temperature=T, humidity=H, lux=cur("lux"), soil_moisture=M, soil_calibrated=cal, soil_sensor_ok=sensor_ok,
            led_pct=None if led is None else int(round(led)),
            fan_pct=None if fan is None else int(round(fan)),
            pump_on=pump_now, irrigations_today=count, minutes_since_irrigation=since,
            moisture_change_since_irrigation=change,
            temp_trend_30m=trend(1), humidity_trend_30m=trend(2), temp_flat_30m=flat,
            max_data_age_s=max(ages) if ages else None,
        )
        yield snap, {"utc": t.isoformat(), "local_date": local.date().isoformat()}
