# -*- coding: utf-8 -*-
"""
교사(teacher) 라벨 — Snapshot + 작물표 → 질문별 정답 확률 분포(gold).

기준은 **서버 api 의 autoctl** (backend 브랜치 api/app/autoctl.py, docs/05 §5). autoctl 의 evaluate() 가
"지금 원하는 출력"을 정하는 부분을 질문 단위로 옮겼다. 옮기지 않은 것(제어기 코드가 맡는다):
  · 앞 단계가 장치에 적용됐는지 확인, 팬 단계 변경 후 120 s 유지, 15 s 재전송 방지, 주간 시작 LED 50 %
  · 연속 실패 5 회 일시 정지, 급수 펄스 끄기

경계(autoctl 의 < · > 비교) 근처는 **부드러운 정답**을 준다: 값을 조금(±δ) 흔들었을 때 답이 바뀌면
정답 0.7 / 바뀐 답들 0.3. 모델이 경계에서 과신하지 않게 하고, 낮은 확신은 PolicyGate 가 기준 제어기로 돌린다.

plant_stress · data_suspect 는 autoctl 에 없는 판단이다 — autoctl 의 범위 이탈(excursion)·"센서 점검 필요"를
바탕으로 넓혔다(docs/04 §3-4, §3-5).

출력 형식은 Laya 학습 데이터의 gold 와 같다:  {"fan_action": {"probabilities": {"off": 0, "down": 0, "hold": 1, "up": 0}}, ...}
"""
from dataclasses import replace

from .features import (FAN_STEPS, FAR_C, HUM_BAND, LED_HIGH_PCT, LUX_LOW_UNDER_LED, PUMP_MAX_PER_DAY, PUMP_WAIT_MIN,
                       TEMP_BAND, WATER_RISE_MIN, data_stale, nearest_step, watering_check_active)
from .profile import is_light_period, ranges_at
from .questions import options_of

SOFT_P = 0.7
PERTURB = {                     # 질문별로 흔들어 볼 값과 폭
    "fan_action": {"temperature": 0.25, "humidity": 1.0},
    "light_action": {"lux": 150.0},
    "irrigate_now": {"soil_moisture": 0.5},
    "plant_stress": {"temperature": 0.25},
    "data_suspect": {},
}
FAN_ACTIONS = ["off", "down", "hold", "up"]
LIGHT_ACTIONS = ["off", "decrease", "hold", "increase"]
STRESS = ["none", "mild", "moderate", "severe"]


# ───────────────────────────────────────── autoctl 판단 (결정론, 한 개 답)
def autoctl_fan(s, p):
    """autoctl 의 배기팬 판단 → (지금 단계 번호, 원하는 단계 번호, 이유)."""
    idx = nearest_step(s.fan_pct)
    t, h = s.temperature, s.humidity
    if t is None or h is None:
        return idx, idx, "missing"                       # 출력 유지 + 센서 점검
    trng, hrng = ranges_at(s.local_time, p)
    top = len(FAN_STEPS) - 1
    if t < trng["min"]:
        return idx, 0, "temp_low"
    if t > trng["max"]:
        return idx, min(idx + 1, top), "temp_high"
    if h > hrng["max"]:
        return idx, min(idx + 1, top), "humidity_high"
    if h < hrng["min"]:
        return idx, 0, "humidity_low"
    if idx > 0 and t <= trng["max"] - TEMP_BAND and h <= hrng["max"] - HUM_BAND:
        return idx, idx - 1, "back_in_range"
    return idx, idx, None


def fan_action_of(s, p):
    idx, want, why = autoctl_fan(s, p)
    if want == idx:
        return "hold"
    if why in ("temp_low", "humidity_low"):
        return "off"
    return "up" if want > idx else "down"


def light_action_of(s, p):
    if not is_light_period(s.local_time, p):
        return "off"
    led = s.led_pct if s.led_pct is not None else 50
    lr = p["lux_day"]
    if s.lux is None:
        return "hold"
    if s.lux < lr["min"] and led < 100:
        return "increase"
    if s.lux > lr["max"] and led > 0:
        return "decrease"
    return "hold"


def irrigate_of(s, p):
    """autoctl 이 5 s 펄스를 시작할 조건인가."""
    if s.soil_calibrated is not True or not s.soil_sensor_ok or s.soil_moisture is None:
        return "false"
    if s.pump_on or s.soil_moisture >= p["soil_moisture"]["min"]:
        return "false"
    if s.irrigations_today >= PUMP_MAX_PER_DAY:
        return "false"
    if s.minutes_since_irrigation is not None and s.minutes_since_irrigation < PUMP_WAIT_MIN:
        return "false"
    return "true"


def stress_of(s, p):
    trng, hrng = ranges_at(s.local_time, p)
    lvl = 0
    t = s.temperature
    if t is not None:
        if t > trng["max"] + FAR_C or t < trng["min"] - FAR_C:
            lvl = 3
        elif t > trng["max"] or t < trng["min"]:
            lvl = 2
    sr = p["soil_moisture"]
    M = s.soil_moisture
    if M is not None and s.soil_calibrated is True and M < sr["min"] and s.irrigations_today >= PUMP_MAX_PER_DAY:
        lvl = max(lvl, 2)                                # 말랐는데 오늘 더 줄 수 없음
    h = s.humidity
    if h is not None and (h > hrng["max"] or h < hrng["min"]):
        lvl = max(lvl, 1)
    if M is not None and s.soil_calibrated is True and M > sr["max"]:
        lvl = max(lvl, 1)                                # 과습
    if is_light_period(s.local_time, p) and s.lux is not None and s.led_pct is not None:
        lr = p["lux_day"]
        if (s.lux < lr["min"] and s.led_pct >= 100) or (s.lux > lr["max"] and s.led_pct <= 0):
            lvl = max(lvl, 1)                            # LED 로 더는 맞출 수 없음
    return STRESS[lvl]


def suspect_reasons(s, p):
    r = []
    if data_stale(s):
        r.append("stale")                                # autoctl: 30 s 넘게 보고 없음
    if s.temperature is None or s.humidity is None:
        r.append("missing_temp_humidity")                # autoctl: 온습도 값 없음
    if is_light_period(s.local_time, p) and s.lux is None:
        r.append("missing_lux")                          # autoctl: 조도 값 없음
    if not s.soil_sensor_ok or s.soil_moisture is None:
        r.append("soil_sensor")                          # autoctl: 토양 값 없음
    if s.temp_flat_30m:
        r.append("stuck_temperature")                    # 확장
    if watering_check_active(s) and s.moisture_change_since_irrigation < WATER_RISE_MIN:
        r.append("no_rise_after_watering")               # 확장
    if s.led_pct is not None and s.led_pct >= LED_HIGH_PCT and s.lux is not None and s.lux < LUX_LOW_UNDER_LED:
        r.append("led_high_lux_low")                     # 확장
    return r


def suspect_of(s, p):
    return "true" if suspect_reasons(s, p) else "false"


HARD = {
    "fan_action": (FAN_ACTIONS, fan_action_of),
    "light_action": (LIGHT_ACTIONS, light_action_of),
    "irrigate_now": (["false", "true"], irrigate_of),
    "plant_stress": (STRESS, stress_of),
    "data_suspect": (["false", "true"], suspect_of),
}


# ───────────────────────────────────────── 부드러운 정답
def soft_label(name, s, p):
    opts, fn = HARD[name]
    base = fn(s, p)
    others = set()
    for field, delta in PERTURB.get(name, {}).items():
        v = getattr(s, field)
        if v is None:
            continue
        for d in (-delta, delta):
            lab = fn(replace(s, **{field: v + d}), p)
            if lab != base:
                others.add(lab)
    probs = {o: 0.0 for o in opts}
    if not others:
        probs[base] = 1.0
        return probs
    probs[base] = SOFT_P
    for o in others:
        probs[o] += (1.0 - SOFT_P) / len(others)
    return probs


def label(s, profile, questions):
    """질문 정의의 모든 질문에 대한 gold. 교사 선택지와 질문 선택지가 다르면 오류(정의가 바뀐 것)."""
    gold = {}
    for name, q in questions.items():
        if name not in HARD:
            raise KeyError("질문 %r 의 교사 함수가 없다 (labeler.HARD 에 추가)" % name)
        probs = soft_label(name, s, profile)
        if sorted(probs) != sorted(options_of(q)):
            raise ValueError("%s: 교사 선택지 %s ≠ 질문 선택지 %s" % (name, sorted(probs), sorted(options_of(q))))
        gold[name] = {"probabilities": probs}
    return gold


def argmax(probs):
    return max(probs.items(), key=lambda kv: kv[1])[0]


def fan_target(s, action):
    """모델 답(fan_action) → 보낼 fan1 값 (제어기가 쓴다)."""
    idx = nearest_step(s.fan_pct)
    if action == "off":
        return 0
    if action == "up":
        return FAN_STEPS[min(idx + 1, len(FAN_STEPS) - 1)]
    if action == "down":
        return FAN_STEPS[max(idx - 1, 0)]
    return FAN_STEPS[idx]
