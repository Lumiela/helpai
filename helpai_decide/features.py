# -*- coding: utf-8 -*-
"""
숫자 → 판정 문장(state).

Laya 는 산수·비교에 약하고 문맥이 짧다(typed-decisions 판 1024 토큰, 영어판 512).
그래서 비교·추세 계산은 여기서 모두 끝내고, 모델에는 "above max 30.0 by 1.2" 같은 **짧은 영어 판정 문장**만 넘긴다.

  · 학습(synth·replay)과 추론(rules 엔진)이 **반드시 이 모듈 하나**로 state 를 만든다.
    문장 하나라도 바뀌면 FEATURE_VERSION 을 올린다 → 묶음 manifest 의 feature_version 과 달라져 rules 가 모델을 shadow 로 내린다.
  · 장치에서 온 문자열은 절대 넣지 않는다(입력 문구로 점수를 조종할 수 있다). 숫자 → 이 코드가 만든 문장만.
"""
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional

from .profile import is_light_period

FEATURE_VERSION = 1

# 팬 단계 (api autoctl 의 FAN_STEPS 와 같은 값)
FAN_LEVELS = {"off": 0, "low": 33, "mid": 66, "high": 100}
_FAN_EDGES = ((16.5, "off"), (49.5, "low"), (83.0, "mid"))     # 이 값 미만이면 그 단계

# 센서 이상 판단 기준 (labeler 와 공유)
STALE_AFTER_S = 60.0
WATER_SOAK_MIN = 5.0            # 관수 후 이 시간이 지나야 수분 변화를 본다(스며드는 시간)
WATER_CHECK_MIN = 30.0          # 관수 후 이 시간까지의 수분 변화를 본다
WATER_RISE_MIN = 1.0            # 관수했는데 이보다 덜 오르면 이상
LED_HIGH_PCT = 80
LUX_LOW_UNDER_LED = 1000.0      # LED ≥ 80 % 인데 조도가 이보다 낮으면 센서가 조명 아래에 없거나 고장


@dataclass
class Snapshot:
    """한 구역의 한 시점. 학습(synth·replay)과 추론(rules)이 같은 형태로 만든다."""
    local_time: datetime                        # 현지(KST) 시각, tz 없음
    temperature: Optional[float] = None         # °C
    humidity: Optional[float] = None            # %
    lux: Optional[float] = None
    soil_moisture: Optional[float] = None       # %
    soil_calibrated: bool = True
    led_pct: Optional[int] = None               # 조명 현재값 0~100
    fan_pct: Optional[int] = None               # 팬 현재값 0~100
    pump_on: bool = False
    irrigations_today: int = 0
    minutes_since_irrigation: Optional[float] = None    # 오늘 관수 없음 = None
    moisture_change_since_irrigation: Optional[float] = None   # %p, 마지막 관수 후 WATER_CHECK_MIN 안일 때만
    temp_trend_30m: Optional[float] = None      # 30 분 전 대비 °C
    humidity_trend_30m: Optional[float] = None  # 30 분 전 대비 %p
    temp_flat_30m: bool = False                 # 30 분간 값이 전혀 안 변함(고착 의심)
    max_data_age_s: Optional[float] = None      # 구역 장치 중 가장 오래된 데이터 나이

    def to_dict(self):
        d = asdict(self)
        d["local_time"] = self.local_time.isoformat(timespec="minutes")
        return d

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["local_time"] = datetime.fromisoformat(d["local_time"])
        return cls(**d)


def _f(x):
    return "%.1f" % x


def watering_check_active(s):
    """관수 후 수분 상승을 확인할 때인가 (WATER_SOAK_MIN ≤ 경과 ≤ WATER_CHECK_MIN, 변화값 있음)."""
    m = s.minutes_since_irrigation
    return (s.moisture_change_since_irrigation is not None and m is not None
            and WATER_SOAK_MIN <= m <= WATER_CHECK_MIN)


def fan_level_of(value):
    """연속 팬 값(0~100) → 가장 가까운 단계 이름."""
    for edge, name in _FAN_EDGES:
        if value < edge:
            return name
    return "high"


def warm_band_edges(profile):
    """비례 환기 구간(comfort~max)에서 팬 단계가 바뀌는 온도. (low→mid, mid→high)"""
    t, f = profile["temperature"], profile["fan"]
    def temp_at(v):
        return t["comfort"] + (v - f["idle"]) / float(f["max"] - f["idle"]) * (t["max"] - t["comfort"])
    return temp_at(_FAN_EDGES[1][0]), temp_at(_FAN_EDGES[2][0])


def temp_band(T, profile):
    """온도 구간 이름. labeler 와 문장이 같은 기준을 쓰게 한다."""
    t = profile["temperature"]
    e1, e2 = warm_band_edges(profile)
    if T >= t["critical"]:
        return "critical"
    if T >= t["max"]:
        return "hot"
    if T < t["min"]:
        return "cold"
    if T < t["comfort"]:
        return "comfortable"
    if T < e1:
        return "slightly_warm"
    if T < e2:
        return "warm"
    return "very_warm"


def in_vent_window(now, profile):
    v = profile["ventilation"]
    return (now.minute % int(v["every_min"])) < v["duration_min"]


def _trend(x, unit, fast):
    if x is None:
        return "trend unknown"
    if abs(x) < 0.2 * fast:
        return "flat over 30 min"
    word = "rising" if x > 0 else "falling"
    speed = " fast" if abs(x) >= fast else ""
    return "%s%s (%+.1f %s in 30 min)" % (word, speed, x, unit)


def _temperature(s, p):
    if s.temperature is None:
        return "missing (no valid reading)"
    t = p["temperature"]
    T = s.temperature
    band = temp_band(T, p)
    e1, e2 = warm_band_edges(p)
    head = "%s C; " % _f(T)
    if band == "critical":
        head += "critical: at or above %s by %s" % (_f(t["critical"]), _f(T - t["critical"]))
    elif band == "hot":
        head += "hot: above max %s by %s" % (_f(t["max"]), _f(T - t["max"]))
    elif band == "cold":
        head += "cold: below min %s by %s" % (_f(t["min"]), _f(t["min"] - T))
    elif band == "comfortable":
        head += "comfortable (%s to %s)" % (_f(t["min"]), _f(t["comfort"]))
    elif band == "slightly_warm":
        head += "slightly warm (%s to %s)" % (_f(t["comfort"]), _f(e1))
    elif band == "warm":
        head += "warm (%s to %s)" % (_f(e1), _f(e2))
    else:
        head += "very warm (%s to max %s)" % (_f(e2), _f(t["max"]))
    head += "; " + _trend(s.temp_trend_30m, "C", 1.5)
    if s.temp_flat_30m:
        head += "; value has not changed at all for 30 min"
    return head


def _humidity(s, p):
    if s.humidity is None:
        return "missing (no valid reading)"
    h = p["humidity"]
    H = s.humidity
    if H >= h["max"]:
        word = "high: at or above max %s" % _f(h["max"])
    elif h.get("low_alert") is not None and H < h["low_alert"]:
        word = "low: below %s (no humidifier)" % _f(h["low_alert"])
    else:
        word = "within range (max %s)" % _f(h["max"])
    return "%s %%; %s; %s" % (_f(H), word, _trend(s.humidity_trend_30m, "%", 8.0))


def _light(s, p):
    li = p["light"]
    led = "LED unknown" if s.led_pct is None else "LED %d %%" % s.led_pct
    if s.led_pct is not None and s.led_pct >= 100:
        led += " (already at max)"
    elif s.led_pct == 0:
        led += " (off)"
    if s.lux is None:
        return "%s; lux missing" % led
    L = s.lux
    if L < li["lux_min"]:
        word = "below min %d" % li["lux_min"]
    elif L > li["lux_max"]:
        word = "above max %d" % li["lux_max"]
    else:
        word = "within %d to %d" % (li["lux_min"], li["lux_max"])
    return "%s; %d lux, %s" % (led, round(L), word)


def _soil(s, p):
    ir = p["irrigation"]
    if s.soil_moisture is None:
        return "missing (no valid reading)"
    M = s.soil_moisture
    if M < ir["moisture_min"]:
        word = "below min %s by %s" % (_f(ir["moisture_min"]), _f(ir["moisture_min"] - M))
    else:
        word = "at or above min %s" % _f(ir["moisture_min"])
    cal = "sensor calibrated" if s.soil_calibrated else "sensor NOT calibrated"
    return "%s %%; %s; %s" % (_f(M), word, cal)


def _irrigation(s, p):
    ir = p["irrigation"]
    parts = ["%d of %d used today" % (s.irrigations_today, ir["max_per_day"])]
    if s.irrigations_today >= ir["max_per_day"]:
        parts.append("daily limit reached")
    if s.minutes_since_irrigation is None:
        parts.append("none yet today")
    else:
        m = s.minutes_since_irrigation
        if m < ir["min_interval_min"]:
            parts.append("last %d min ago, interval %d min NOT passed" % (round(m), ir["min_interval_min"]))
        else:
            parts.append("last %d min ago, interval passed" % round(m))
    if watering_check_active(s):
        d = s.moisture_change_since_irrigation
        if d < WATER_RISE_MIN:
            parts.append("moisture did NOT rise after watering (%+.1f)" % d)
        else:
            parts.append("moisture rose after watering (%+.1f)" % d)
    if s.pump_on:
        parts.append("pump running now")
    return "; ".join(parts)


def _time(s, p):
    pp = p["photoperiod"]
    hhmm = s.local_time.strftime("%H:%M")
    if is_light_period(s.local_time, p):
        if s.local_time.month in pp["night_lighting_months"]:
            out = "%s, winter month: lights allowed all day" % hhmm
        else:
            out = "%s, daytime (light period %s to %s)" % (hhmm, pp["start"], pp["end"])
    else:
        out = "%s, night: lights must be off" % hhmm
    if in_vent_window(s.local_time, p):
        out += "; scheduled ventilation window now"
    return out


def _data(s):
    if s.max_data_age_s is None:
        return "freshness unknown"
    if s.max_data_age_s > STALE_AFTER_S:
        return "STALE: oldest reading %d s old" % round(s.max_data_age_s)
    return "fresh (oldest reading %d s old)" % round(s.max_data_age_s)


def build_state(s, profile):
    """Snapshot → state dict (영어 판정 문장). 키 순서도 고정이다."""
    fan = "unknown" if s.fan_pct is None else "%d %% (level %s)" % (s.fan_pct, fan_level_of(s.fan_pct))
    return {
        "crop": "rose",
        "time": _time(s, profile),
        "temperature": _temperature(s, profile),
        "humidity": _humidity(s, profile),
        "light": _light(s, profile),
        "soil": _soil(s, profile),
        "irrigation": _irrigation(s, profile),
        "fan": fan,
        "data": _data(s),
    }


def state_text_len(state):
    """토큰 수의 대략 상한(공백 단어 수 × 1.6). 정확한 토큰 수는 laya_tools.token_count 로 잰다."""
    words = sum(len(str(k).split()) + len(str(v).split()) for k, v in state.items())
    return int(words * 1.6) + 1
