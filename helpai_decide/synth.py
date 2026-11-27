# -*- coding: utf-8 -*-
"""
합성 Snapshot 생성기 — 실데이터가 적을 때 경계 상황·이상 상황을 넉넉히 만든다.

  · 결정론: 같은 seed 면 같은 결과(시험·재현용). 표준 라이브러리 random 만 쓴다.
  · 값을 고르게 뿌리지 않고 **판단이 바뀌는 경계 근처**(22·26·27.5·29.2·30·35 °C, 습도 85·40 %, 조도 6000·12000, 토양 40 %)에 몰아 준다.
  · 일정 비율로 이상 시나리오(센서 고착·관수 후 무반응·LED 높은데 조도 낮음·오래된 데이터·결측)를 섞는다.
"""
import random
from datetime import datetime, timedelta

from .features import Snapshot, warm_band_edges

DEFAULTS = {
    "n": 20000,
    "seed": 20261127,
    "edge_frac": 0.6,           # 경계 근처에서 뽑는 비율
    "anomaly_rate": 0.15,
    "missing_rate": 0.03,       # 센서별 결측 비율
    "day_frac": 0.7,            # 낮 시간 비율
    "year": 2026,
}
ANOMALIES = ("stuck_temperature", "no_rise_after_watering", "led_high_lux_low", "stale", "missing_reading")


def _pick_edge(rng, edges, sd, lo, hi):
    v = rng.gauss(rng.choice(edges), sd)
    return min(hi, max(lo, v))


def _mix(rng, frac, edges, sd, lo, hi):
    if rng.random() < frac:
        return _pick_edge(rng, edges, sd, lo, hi)
    return rng.uniform(lo, hi)


def sample_snapshot(rng, profile, cfg):
    t, h, li, ir = profile["temperature"], profile["humidity"], profile["light"], profile["irrigation"]
    pp = profile["photoperiod"]
    ef = cfg["edge_frac"]

    # 시각: 낮/밤 비율을 맞추고 1년 전체에 퍼뜨린다(겨울 = 종일 조명 달)
    day = rng.random() < cfg["day_frac"]
    base = datetime(cfg["year"], 1, 1) + timedelta(days=rng.randrange(365))
    on, off = pp["_on_min"], pp["_off_min"]
    if day:
        minute = rng.randrange(on, off)
    else:
        minute = rng.choice([rng.randrange(0, on), rng.randrange(off, 24 * 60)])
    now = base + timedelta(minutes=minute)

    e1, e2 = warm_band_edges(profile)
    T = round(_mix(rng, ef, [t["min"], t["comfort"], e1, e2, t["max"], t["critical"]], 0.6, 12.0, 42.0), 1)
    H = round(_mix(rng, ef * 0.5, [h["max"], h.get("low_alert") or 40.0], 2.0, 20.0, 99.0), 1)
    led = rng.choice([0, 0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 100])
    if not day and rng.random() < 0.7:
        led = 0
    ambient = rng.uniform(0, 3000) if day else rng.uniform(0, 50)
    per_pct = rng.uniform(15, 130)                     # LED 1 % 당 조도 (센서 위치에 따라 크게 다르다)
    if rng.random() < ef * 0.5:
        lux = _pick_edge(rng, [li["lux_min"], li["lux_max"]], 250, 0, 20000)
    else:
        lux = ambient + led * per_pct
    lux = round(max(0.0, lux))
    M = round(_mix(rng, ef, [ir["moisture_min"]], 1.5, 10.0, 95.0), 1)
    count = min(ir["max_per_day"], int(abs(rng.gauss(0, 4))))
    if rng.random() < 0.08:
        count = ir["max_per_day"]
    since = None
    if count > 0:
        since = round(_mix(rng, 0.4, [ir["min_interval_min"], 5.0, 30.0], 2.0, 0.5, 600.0), 1)
    rise = None
    if since is not None and since <= 30:
        rise = round(rng.uniform(1.5, 10.0), 1)

    s = dict(
        local_time=now, temperature=T, humidity=H, lux=lux, soil_moisture=M,
        soil_calibrated=rng.random() > 0.08, led_pct=led,
        fan_pct=rng.choice([0, 20, 33, 50, 60, 66, 100]), pump_on=rng.random() < 0.03,
        irrigations_today=count, minutes_since_irrigation=since, moisture_change_since_irrigation=rise,
        temp_trend_30m=round(rng.gauss(0, 1.2), 1), humidity_trend_30m=round(rng.gauss(0, 5), 1),
        temp_flat_30m=False, max_data_age_s=round(rng.uniform(1, 15), 0),
    )

    scenario = "normal"
    if rng.random() < cfg["anomaly_rate"]:
        scenario = rng.choice(ANOMALIES)
        if scenario == "stuck_temperature":
            s["temp_flat_30m"] = True
            s["temp_trend_30m"] = 0.0
        elif scenario == "no_rise_after_watering":
            s["irrigations_today"] = max(1, s["irrigations_today"])
            s["minutes_since_irrigation"] = round(rng.uniform(6, 30), 1)
            s["moisture_change_since_irrigation"] = round(rng.uniform(-0.5, 0.8), 1)
        elif scenario == "led_high_lux_low":
            s["led_pct"] = rng.choice([80, 90, 100])
            s["lux"] = round(rng.uniform(100, 990))
        elif scenario == "stale":
            s["max_data_age_s"] = round(rng.uniform(61, 900), 0)
        elif scenario == "missing_reading":
            s[rng.choice(["temperature", "humidity"])] = None
    for k in ("temperature", "humidity", "lux", "soil_moisture"):
        if rng.random() < cfg["missing_rate"]:
            s[k] = None
    return Snapshot(**s), scenario


def generate(profile, cfg=None):
    """(Snapshot, scenario) 를 n 개 내놓는 생성기."""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    rng = random.Random(c["seed"])
    for _ in range(int(c["n"])):
        yield sample_snapshot(rng, profile, c)
