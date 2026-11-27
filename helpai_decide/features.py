# -*- coding: utf-8 -*-
"""
숫자 → 판정 문장(state).  기준 = 서버 api 의 autoctl 규칙 (docs/05).

Laya 는 산수·비교에 약하고 문맥이 짧다(typed-decisions 판 1024 토큰). 그래서 autoctl 이 쓰는 비교
(범위 안/밖, 최고 − 1 °C·최고 − 5 %p, 하루 4회·60 분 대기, 30 s 보고 끊김)를 여기서 모두 끝내고,
모델에는 "above max 30.0 by 1.2" 같은 **짧은 영어 판정 문장**만 넘긴다.

  · 학습(synth·replay)과 추론(젯슨 제어기)이 **반드시 이 모듈 하나**로 state 를 만든다.
    문장 하나라도 바뀌면 FEATURE_VERSION 을 올린다 → 묶음 manifest 의 feature_version 과 달라져 모델이 shadow 로 내려간다.
  · 장치에서 온 문자열은 절대 넣지 않는다(입력 문구로 점수를 조종할 수 있다). 숫자 → 이 코드가 만든 문장만.
"""
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional

from .profile import is_light_period, ranges_at

FEATURE_VERSION = 2

# ── autoctl.py 상수 (backend 28aed59) ──
FAN_STEPS = (0, 33, 66, 100)
FAN_NAMES = ("off", "low", "mid", "high")
TEMP_BAND = 1.0                 # 최고 − 1 °C 이하로 내려와야 한 단계 내림
HUM_BAND = 5.0                  # 최고 − 5 %p
PUMP_WAIT_MIN = 60.0            # 재급수 대기
PUMP_MAX_PER_DAY = 4
PUMP_PULSE_S = 5
LED_STEP = 10
STALE_S = 30.0                  # 보고가 이보다 오래되면 "센서 점검 필요"
SOIL_DEFAULT_AIR = 22520        # 토양 센서 공장 기본 보정값
SOIL_DEFAULT_WATER = 11100

# ── 결정 모델 확장 판단 (autoctl 에 없음, labeler 와 공유) ──
WATER_SOAK_MIN = 5.0            # 급수 후 이 시간이 지나야 수분 변화를 본다
WATER_CHECK_MIN = 30.0          # 급수 후 이 시간까지의 수분 변화를 본다
WATER_RISE_MIN = 1.0            # 급수했는데 이보다 덜 오르면 이상
LED_HIGH_PCT = 80
LUX_LOW_UNDER_LED = 1000.0      # LED ≥ 80 % 인데 조도가 이보다 낮으면 조도계가 조명 아래에 없거나 고장
FAR_C = 3.0                     # 온도가 범위 밖으로 이만큼 넘으면 심각


@dataclass
class Snapshot:
    """큐브 한 대의 한 시점 (autoctl 이 보는 값 + 이력 맥락). 학습과 추론이 같은 형태로 만든다."""
    local_time: datetime                        # KST, tz 없음
    temperature: Optional[float] = None         # °C
    humidity: Optional[float] = None            # %
    lux: Optional[float] = None
    soil_moisture: Optional[float] = None       # % (moisture_pct)
    soil_calibrated: Optional[bool] = True      # status air·water 가 공장 기본값이 아니면 True, 모르면 None
    soil_sensor_ok: bool = True                 # ads1115_ok
    led_pct: Optional[int] = None               # led1 보고값
    fan_pct: Optional[int] = None               # fan1 보고값
    pump_on: bool = False
    irrigations_today: int = 0                  # KST 날짜 기준
    minutes_since_irrigation: Optional[float] = None    # 오늘 급수 없음 = None
    moisture_change_since_irrigation: Optional[float] = None   # %p
    temp_trend_30m: Optional[float] = None
    humidity_trend_30m: Optional[float] = None
    temp_flat_30m: bool = False                 # 30 분간 값이 전혀 안 변함(고착 의심)
    max_data_age_s: Optional[float] = None      # 마지막 보고 이후 초

    def to_dict(self):
        d = asdict(self)
        d["local_time"] = self.local_time.isoformat(timespec="minutes")
        return d

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["local_time"] = datetime.fromisoformat(d["local_time"])
        return cls(**d)


def soil_calibrated_from_status(air, water):
    """autoctl.soil_calibrated 와 같음: True 보정됨 · False 공장 기본값 · None 모름."""
    if air is None or water is None:
        return None
    return not (float(air) == SOIL_DEFAULT_AIR and float(water) == SOIL_DEFAULT_WATER)


def nearest_step(v):
    """autoctl.nearest_step — 보고된 팬 값에 가장 가까운 단계 번호. 값이 없으면 0."""
    if v is None:
        return 0
    return min(range(len(FAN_STEPS)), key=lambda i: abs(FAN_STEPS[i] - float(v)))


def watering_check_active(s):
    m = s.minutes_since_irrigation
    return (s.moisture_change_since_irrigation is not None and m is not None
            and WATER_SOAK_MIN <= m <= WATER_CHECK_MIN)


def data_stale(s):
    return s.max_data_age_s is not None and s.max_data_age_s > STALE_S


def _f(x):
    return "%.1f" % x


def _trend(x, unit, fast):
    if x is None:
        return "trend unknown"
    if abs(x) < 0.2 * fast:
        return "flat over 30 min"
    word = "rising" if x > 0 else "falling"
    speed = " fast" if abs(x) >= fast else ""
    return "%s%s (%+.1f %s in 30 min)" % (word, speed, x, unit)


def _range_sentence(v, rng, band, unit):
    """범위 안/밖 + autoctl 의 '최고 − 폭' 기준을 문장으로."""
    lo, hi = rng["min"], rng["max"]
    if v < lo:
        return "below min %s by %s" % (_f(lo), _f(lo - v))
    if v > hi:
        return "above max %s by %s" % (_f(hi), _f(v - hi))
    if v > hi - band:
        return "within %s to %s, close to max (within %s%s)" % (_f(lo), _f(hi), _f(band), unit)
    return "within %s to %s, comfortably below max" % (_f(lo), _f(hi))


def _time(s, p):
    pp = p["photoperiod"]
    hhmm = s.local_time.strftime("%H:%M")
    if is_light_period(s.local_time, p):
        return "%s, daytime (light period %s to %s)" % (hhmm, pp["on"], pp["off"])
    return "%s, night (light period %s to %s): lights must be off" % (hhmm, pp["on"], pp["off"])


def _temperature(s, p):
    if s.temperature is None:
        return "missing (no valid reading)"
    trng, _ = ranges_at(s.local_time, p)
    out = "%s C; %s; %s" % (_f(s.temperature), _range_sentence(s.temperature, trng, TEMP_BAND, " C"),
                            _trend(s.temp_trend_30m, "C", 1.5))
    if s.temp_flat_30m:
        out += "; value has not changed at all for 30 min"
    return out


def _humidity(s, p):
    if s.humidity is None:
        return "missing (no valid reading)"
    _, hrng = ranges_at(s.local_time, p)
    return "%s %%; %s; %s" % (_f(s.humidity), _range_sentence(s.humidity, hrng, HUM_BAND, " %"),
                              _trend(s.humidity_trend_30m, "%", 8.0))


def _light(s, p):
    lr = p["lux_day"]
    if s.led_pct is None:
        led = "LED unknown"
    elif s.led_pct >= 100:
        led = "LED 100 % (already at max)"
    elif s.led_pct <= 0:
        led = "LED 0 % (off)"
    else:
        led = "LED %d %%" % s.led_pct
    if not is_light_period(s.local_time, p):
        return "%s; lux target not used at night" % led
    if s.lux is None:
        return "%s; lux missing" % led
    L = s.lux
    if L < lr["min"]:
        word = "below min %d" % lr["min"]
    elif L > lr["max"]:
        word = "above max %d" % lr["max"]
    else:
        word = "within %d to %d" % (lr["min"], lr["max"])
    return "%s; %d lux, %s" % (led, round(L), word)


def _soil(s, p):
    sr = p["soil_moisture"]
    if not s.soil_sensor_ok:
        return "sensor error (no valid reading)"
    if s.soil_moisture is None:
        return "missing (no valid reading)"
    M = s.soil_moisture
    if M < sr["min"]:
        word = "below min %s by %s" % (_f(sr["min"]), _f(sr["min"] - M))
    elif M > sr["max"]:
        word = "above max %s by %s" % (_f(sr["max"]), _f(M - sr["max"]))
    else:
        word = "within %s to %s" % (_f(sr["min"]), _f(sr["max"]))
    cal = {True: "sensor calibrated", False: "sensor NOT calibrated (factory default)",
           None: "calibration unknown"}[s.soil_calibrated]
    return "%s %%; %s; %s" % (_f(M), word, cal)


def _irrigation(s, p):
    parts = ["%d of %d pulses used today" % (s.irrigations_today, PUMP_MAX_PER_DAY)]
    if s.irrigations_today >= PUMP_MAX_PER_DAY:
        parts.append("daily limit reached")
    if s.minutes_since_irrigation is None:
        parts.append("none yet today")
    else:
        m = s.minutes_since_irrigation
        if m < PUMP_WAIT_MIN:
            parts.append("last %d min ago, %d min wait NOT passed" % (round(m), PUMP_WAIT_MIN))
        else:
            parts.append("last %d min ago, wait passed" % round(m))
    if watering_check_active(s):
        d = s.moisture_change_since_irrigation
        parts.append("moisture %s after watering (%+.1f)" % ("did NOT rise" if d < WATER_RISE_MIN else "rose", d))
    if s.pump_on:
        parts.append("pump running now")
    return "; ".join(parts)


def _fan(s):
    if s.fan_pct is None:
        return "unknown (treated as off)"
    i = nearest_step(s.fan_pct)
    edge = " (already at max)" if i == len(FAN_STEPS) - 1 else (" (already off)" if i == 0 else "")
    return "%d %% (level %s)%s" % (s.fan_pct, FAN_NAMES[i], edge)


def _data(s):
    if s.max_data_age_s is None:
        return "freshness unknown"
    if data_stale(s):
        return "STALE: last report %d s ago" % round(s.max_data_age_s)
    return "fresh (last report %d s ago)" % round(s.max_data_age_s)


def build_state(s, profile):
    """Snapshot → state dict (영어 판정 문장). 키 순서도 고정이다."""
    return {
        "crop": profile.get("id", "rose"),
        "time": _time(s, profile),
        "temperature": _temperature(s, profile),
        "humidity": _humidity(s, profile),
        "light": _light(s, profile),
        "soil": _soil(s, profile),
        "irrigation": _irrigation(s, profile),
        "fan": _fan(s),
        "data": _data(s),
    }


def state_text_len(state):
    """토큰 수의 대략 상한(공백 단어 수 × 1.6). 정확한 토큰 수는 laya_tools.token_count 로 잰다."""
    words = sum(len(str(k).split()) + len(str(v).split()) for k, v in state.items())
    return int(words * 1.6) + 1
