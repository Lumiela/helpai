# -*- coding: utf-8 -*-
"""
합성 Snapshot 생성기 — 실데이터가 적을 때 autoctl 판단이 바뀌는 경계와 이상 상황을 넉넉히 만든다.

  · 결정론: 같은 seed 면 같은 결과. 표준 라이브러리 random 만 쓴다.
  · 값을 고르게 뿌리지 않고 **autoctl 판단이 바뀌는 경계 근처**에 몰아 준다:
      온도 최저·최고·최고−1·±3, 습도 최저·최고·최고−5, 조도 최저·최고, 토양 최저·최고, 급수 대기 60 분
  · 팬 현재 단계(0·33·66·100)와 LED 현재값을 고르게 섞는다 — autoctl 은 "지금 단계에서 한 칸"을 정하기 때문
  · 일정 비율로 이상 시나리오를 섞는다.
"""
import random
from datetime import datetime, timedelta

from .features import FAN_STEPS, FAR_C, HUM_BAND, PUMP_MAX_PER_DAY, PUMP_WAIT_MIN, Snapshot, TEMP_BAND
from .profile import is_light_period, ranges_at

DEFAULTS = {
    "n": 20000,
    "seed": 20261127,
    "edge_frac": 0.6,           # 경계 근처에서 뽑는 비율
    "anomaly_rate": 0.15,
    "missing_rate": 0.03,
    "night_frac": 0.2,          # 밤 비율 (장미 광주기 01~23 시라 실제 밤은 2 시간뿐 — 일부러 늘린다)
    "comfort_frac": 0.15,       # 온도 최저~최고−1, 습도 최저~최고−5 (팬 '한 단계 내림'이 나오는 좁은 구간)
    "year": 2026,
}
ANOMALIES = ("stuck_temperature", "no_rise_after_watering", "led_high_lux_low", "stale", "missing_reading",
             "soil_sensor_error")


def _pick_edge(rng, edges, sd, lo, hi):
    return min(hi, max(lo, rng.gauss(rng.choice(edges), sd)))


def _mix(rng, frac, edges, sd, lo, hi):
    if rng.random() < frac:
        return _pick_edge(rng, edges, sd, lo, hi)
    return rng.uniform(lo, hi)


def _time(rng, profile, want_day, year):
    base = datetime(year, 1, 1) + timedelta(days=rng.randrange(365))
    for _ in range(200):
        t = base + timedelta(minutes=rng.randrange(24 * 60))
        if is_light_period(t, profile) == want_day:
            return t
    return t


def sample_snapshot(rng, profile, cfg):
    ef = cfg["edge_frac"]
    day = rng.random() >= cfg["night_frac"]
    now = _time(rng, profile, day, cfg["year"])
    trng, hrng = ranges_at(now, profile)
    sr, lr = profile["soil_moisture"], profile["lux_day"]

    T = round(_mix(rng, ef, [trng["min"], trng["max"], trng["max"] - TEMP_BAND,
                             trng["min"] - FAR_C, trng["max"] + FAR_C], 0.5, 12.0, 42.0), 1)
    H = round(_mix(rng, ef, [hrng["min"], hrng["max"], hrng["max"] - HUM_BAND], 1.5, 20.0, 99.0), 1)
    fan = rng.choice(FAN_STEPS + FAN_STEPS + (20, 50, 80))
    if rng.random() < cfg["comfort_frac"]:
        T = round(rng.uniform(trng["min"], trng["max"] - TEMP_BAND), 1)
        H = round(rng.uniform(hrng["min"], hrng["max"] - HUM_BAND), 1)
        fan = rng.choice(FAN_STEPS[1:])
    led = rng.choice([0, 10, 20, 30, 40, 50, 50, 60, 70, 80, 90, 100, 100])
    if not day and rng.random() < 0.6:
        led = 0
    if rng.random() < ef * 0.5:
        lux = _pick_edge(rng, [lr["min"], lr["max"]], 250, 0, 20000)
    else:
        lux = (rng.uniform(0, 3000) if day else rng.uniform(0, 50)) + led * rng.uniform(15, 130)
    lux = round(max(0.0, lux))
    M = round(_mix(rng, ef, [sr["min"], sr["max"]], 1.0, 5.0, 95.0), 1)
    count = min(PUMP_MAX_PER_DAY, int(abs(rng.gauss(0, 2))))
    if rng.random() < 0.1:
        count = PUMP_MAX_PER_DAY
    since = None
    if count > 0:
        since = round(_mix(rng, 0.5, [PUMP_WAIT_MIN, 5.0, 30.0], 3.0, 0.5, 900.0), 1)
    rise = round(rng.uniform(1.5, 10.0), 1) if since is not None and since <= 30 else None
    cal = rng.choices([True, False, None], weights=[0.88, 0.07, 0.05])[0]

    s = dict(
        local_time=now, temperature=T, humidity=H, lux=lux, soil_moisture=M, soil_calibrated=cal,
        soil_sensor_ok=True, led_pct=led, fan_pct=fan, pump_on=rng.random() < 0.03,
        irrigations_today=count, minutes_since_irrigation=since, moisture_change_since_irrigation=rise,
        temp_trend_30m=round(rng.gauss(0, 1.2), 1), humidity_trend_30m=round(rng.gauss(0, 5), 1),
        temp_flat_30m=False, max_data_age_s=float(round(rng.uniform(1, 12))),
    )

    scenario = "normal"
    if rng.random() < cfg["anomaly_rate"]:
        scenario = rng.choice(ANOMALIES)
        if scenario == "stuck_temperature":
            s["temp_flat_30m"], s["temp_trend_30m"] = True, 0.0
        elif scenario == "no_rise_after_watering":
            s["irrigations_today"] = max(1, s["irrigations_today"])
            s["minutes_since_irrigation"] = round(rng.uniform(6, 30), 1)
            s["moisture_change_since_irrigation"] = round(rng.uniform(-0.5, 0.8), 1)
        elif scenario == "led_high_lux_low":
            s["led_pct"] = rng.choice([80, 90, 100])
            s["lux"] = round(rng.uniform(100, 990))
        elif scenario == "stale":
            s["max_data_age_s"] = float(round(rng.uniform(31, 900)))
        elif scenario == "missing_reading":
            s[rng.choice(["temperature", "humidity", "lux"])] = None
        elif scenario == "soil_sensor_error":
            s["soil_sensor_ok"], s["soil_moisture"] = False, None
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
