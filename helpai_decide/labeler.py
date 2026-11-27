# -*- coding: utf-8 -*-
"""
교사(teacher) 라벨 — Snapshot + 작물 프로필 → 질문별 정답 확률 분포(gold).

rules v2(domain/rules.py) 의 판단을 질문 단위로 옮긴 것이다. 차이:
  · 상태 없음(stateless): v2 의 히스테리시스(latch)·관수 기억 대신, Snapshot 에 담긴 값(관수 횟수·경과 분)만 본다.
    대신 경계 근처(히스테리시스 폭 안)는 정답을 **부드럽게(soft)** 준다 → 모델이 경계에서 과신하지 않게.
  · 저온이면 팬 off (v2 는 idle 20 % 상한, api autoctl 은 0). 질문 기준("cold needs the fan off")과 맞춘다.
  · plant_stress·data_suspect 는 v2 에 없는 새 판단 — 기준은 아래 함수 주석과 docs/04 에 있다.

출력 형식은 Laya 학습 데이터의 gold 와 같다:  {"fan_level": {"probabilities": {"off": 0, "low": 1, ...}}, ...}
"""
from .features import (FAN_LEVELS, LED_HIGH_PCT, LUX_LOW_UNDER_LED, STALE_AFTER_S, WATER_RISE_MIN,
                       fan_level_of, in_vent_window, warm_band_edges, watering_check_active)
from .profile import is_light_period
from .questions import options_of

SOFT_P = 0.7                 # 경계 근처 정답 확률 (나머지는 이웃 선택지)
FAN_EDGE_C = 0.25            # 팬 단계 경계 ± °C
LUX_EDGE = 150.0             # 조도 경계 ± lx
MOIST_EDGE = 1.0             # 토양 경계 ± %p
STRESS_FAR_C = 3.0           # 범위 밖으로 이만큼 넘으면 severe
_FAN_ORDER = list(FAN_LEVELS)


def _onehot(options, label):
    return {o: (1.0 if o == label else 0.0) for o in options}


def _soft(options, label, other, p=SOFT_P):
    d = {o: 0.0 for o in options}
    d[label] = p
    d[other] = d[other] + (1.0 - p)
    return d


def _lerp(x, x0, x1, y0, y1):
    if x <= x0:
        return y0
    if x >= x1:
        return y1
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


# ───────────────────────────────────────── fan_level
def teacher_fan(s, p):
    opts = _FAN_ORDER
    t, f, h = p["temperature"], p["fan"], p["humidity"]
    T, H = s.temperature, s.humidity
    if T is not None and T >= t["critical"]:
        return _onehot(opts, "high")
    if T is not None and T < t["min"]:
        if t["min"] - T < FAN_EDGE_C:
            return _soft(opts, "off", "low")
        return _onehot(opts, "off")
    floors = []
    if H is not None and H >= h["max"]:
        floors.append(h["vent_fan_floor"])
    if in_vent_window(s.local_time, p):
        floors.append(p["ventilation"]["fan_floor"])
    if T is None:
        base = s.fan_pct if s.fan_pct is not None else f["idle"]
    elif T >= t["max"]:
        base = f["max"]
    else:
        base = _lerp(T, t["comfort"], t["max"], f["idle"], f["max"])
    v = max([base] + floors)
    label = fan_level_of(v)
    # 경계 근처: 온도가 결정했고(바닥값이 이기지 않았고) 단계 경계 ± FAN_EDGE_C 안
    if T is not None and v == base:
        if T - t["min"] < FAN_EDGE_C and label == "low":
            return _soft(opts, "low", "off")
        for edge in warm_band_edges(p):
            if abs(T - edge) < FAN_EDGE_C:
                i = opts.index(label)
                other = opts[i + 1] if T < edge else opts[i - 1]
                return _soft(opts, label, other)
    return _onehot(opts, label)


# ───────────────────────────────────────── light_action
def teacher_light(s, p):
    opts = ["off", "decrease", "hold", "increase"]
    t, li = p["temperature"], p["light"]
    T, L = s.temperature, s.lux
    led = s.led_pct if s.led_pct is not None else 50
    if T is not None and T >= t["critical"]:
        return _onehot(opts, "decrease" if led > t["heat_light_cap"] else "hold")
    if T is not None and T < t["min"] and t["use_light_for_heating"]:
        return _onehot(opts, "increase" if led < 100 else "hold")
    if not is_light_period(s.local_time, p):
        return _onehot(opts, "off")
    if L is None:
        return _onehot(opts, "increase" if led < 100 else "hold")
    if L < li["lux_min"]:
        label = "increase" if led < 100 else "hold"
        if label == "increase" and li["lux_min"] - L < LUX_EDGE:
            return _soft(opts, "increase", "hold")
        return _onehot(opts, label)
    if L > li["lux_max"]:
        label = "decrease" if led > 0 else "hold"
        if label == "decrease" and L - li["lux_max"] < LUX_EDGE:
            return _soft(opts, "decrease", "hold")
        return _onehot(opts, label)
    if L - li["lux_min"] < LUX_EDGE and led < 100:
        return _soft(opts, "hold", "increase")
    if li["lux_max"] - L < LUX_EDGE and led > 0:
        return _soft(opts, "hold", "decrease")
    return _onehot(opts, "hold")


# ───────────────────────────────────────── irrigate_now
def irrigation_gates(s, p):
    """관수 조건 중 토양 값을 뺀 나머지가 모두 허용하는가."""
    ir = p["irrigation"]
    if s.pump_on:
        return False
    if ir.get("require_calibrated", True) and not s.soil_calibrated:
        return False
    if s.irrigations_today >= ir["max_per_day"]:
        return False
    if s.minutes_since_irrigation is not None and s.minutes_since_irrigation < ir["min_interval_min"]:
        return False
    return True


def teacher_irrigate(s, p):
    opts = ["false", "true"]
    M = s.soil_moisture
    if M is None or not irrigation_gates(s, p):
        return _onehot(opts, "false")
    mn = p["irrigation"]["moisture_min"]
    if M < mn:
        if mn - M < MOIST_EDGE:
            return _soft(opts, "true", "false")
        return _onehot(opts, "true")
    if M - mn < MOIST_EDGE:
        return _soft(opts, "false", "true")
    return _onehot(opts, "false")


# ───────────────────────────────────────── plant_stress
STRESS = ["none", "mild", "moderate", "severe"]


def stress_level(s, p):
    """0 none · 1 mild · 2 moderate · 3 severe. 가장 나쁜 항목이 결정한다."""
    t, h, li, ir = p["temperature"], p["humidity"], p["light"], p["irrigation"]
    lvl = 0
    T = s.temperature
    if T is not None:
        if T >= t["critical"] or T >= t["max"] + STRESS_FAR_C or T < t["min"] - STRESS_FAR_C:
            lvl = max(lvl, 3)
        elif T >= t["max"] or T < t["min"]:
            lvl = max(lvl, 2)
    H = s.humidity
    if H is not None and (H >= h["max"] or (h.get("low_alert") is not None and H < h["low_alert"])):
        lvl = max(lvl, 1)
    M = s.soil_moisture
    if M is not None and s.soil_calibrated and M < ir["moisture_min"] and s.irrigations_today >= ir["max_per_day"]:
        lvl = max(lvl, 2)
    if (is_light_period(s.local_time, p) and s.lux is not None and s.lux < li["lux_min"]
            and s.led_pct is not None and s.led_pct >= 100):
        lvl = max(lvl, 1)
    return lvl


def teacher_stress(s, p):
    return _onehot(STRESS, STRESS[stress_level(s, p)])


# ───────────────────────────────────────── data_suspect
def suspect_reasons(s, p):
    r = []
    if s.max_data_age_s is not None and s.max_data_age_s > STALE_AFTER_S:
        r.append("stale")
    if s.temp_flat_30m:
        r.append("stuck_temperature")
    if s.temperature is None or s.humidity is None:
        r.append("missing_reading")
    if watering_check_active(s) and s.moisture_change_since_irrigation < WATER_RISE_MIN:
        r.append("no_rise_after_watering")
    if s.led_pct is not None and s.led_pct >= LED_HIGH_PCT and s.lux is not None and s.lux < LUX_LOW_UNDER_LED:
        r.append("led_high_lux_low")
    return r


def teacher_suspect(s, p):
    return _onehot(["false", "true"], "true" if suspect_reasons(s, p) else "false")


TEACHERS = {
    "fan_level": teacher_fan,
    "light_action": teacher_light,
    "irrigate_now": teacher_irrigate,
    "plant_stress": teacher_stress,
    "data_suspect": teacher_suspect,
}


def label(s, profile, questions):
    """질문 정의에 있는 모든 질문의 gold. 질문의 선택지와 교사 출력의 키가 다르면 오류(정의가 바뀐 것)."""
    gold = {}
    for name, q in questions.items():
        if name not in TEACHERS:
            raise KeyError("질문 %r 의 교사 함수가 없다 (labeler.TEACHERS 에 추가)" % name)
        probs = TEACHERS[name](s, profile)
        if sorted(probs) != sorted(options_of(q)):
            raise ValueError("%s: 교사 선택지 %s ≠ 질문 선택지 %s" % (name, sorted(probs), sorted(options_of(q))))
        gold[name] = {"probabilities": probs}
    return gold


def argmax(probs):
    return max(probs.items(), key=lambda kv: kv[1])[0]
