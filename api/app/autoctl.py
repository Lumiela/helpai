# -*- coding: utf-8 -*-
"""
작물 기반 규칙 자동 제어 (2026-10-06, 계획서 edgeserver-JS/2026-10-06_작물기반자동제어_계획서_랩탑ClaudeCode.md §12).

  · api 안에서 돈다. 텔레메트리는 main 의 메모리 상태를 읽고, 명령은 사람 제어와 같은 송신 함수로 보낸다(순번 공유).
  · 큐브에 작물 ID 가 배정돼 있고 자동 제어가 켜진 큐브만 판단한다. 판정 주기 AUTO_INTERVAL_SECONDS(기본 10초).
  · 판정은 evaluate() 하나(입출력만 있는 함수). 루프·송신·급수 펄스는 tick()/_send() 가 맡는다.

규칙 (사용자 판정 2026-10-06)
  주간/야간      광주기 켜는 시각~끄는 시각 = 주간(한국 시간). 온도·습도는 주야 범위가 따로, 토양 수분은 하루 종일 한 범위
  배기팬(fan1)   단계 0 → 33 → 66 → 100. 온도·습도 상한 초과 → 한 단계 올림, 상한 − 폭(온도 1 °C, 습도 5 %p) 이하로
                 내려오면 한 단계 내림. 온도 또는 습도 하한 미만 → 0. 온도와 습도가 서로 반대면 온도 우선.
                 단계를 바꾼 뒤 2분은 다시 바꾸지 않는다. 장치가 앞 단계를 적용한 것을 본 뒤에만 다음 단계로.
  급수(pump)     자동 제어가 켜져 있으면 함께 동작(2026-10-06 저녁 사용자 정정: 급수 규칙 별도 스위치 없음).
                 토양 수분 < 하한 → 5초 켰다 끔, 재급수 대기 60분, 하루 최대 4회.
                 장치 status 의 air·water 가 공장 기본값(22520·11100)이면 보정 안 된 것으로 보고 급수하지 않는다.
  LED(led1)      주간: 조도가 목표 범위 밖이면 판정마다 10 %씩(하한 아래 +10, 상한 위 −10). 주간 시작 때 50 %.
                 주간 도중 자동을 켜거나 서버가 재시작되면 장치가 보고한 지금 밝기에서 이어서(모르면 50 %). 야간 0.
                 조도 값이 없으면 밝기 조절만 멈추고 켜기·끄기는 시각대로.
  센서 이상      값이 없거나 보고가 30초 넘게 안 오면 출력 유지 + "센서 점검 필요". 급수 펄스 끄기는 예외로 보낸다.
  실패           재전송 최소 30초 간격, 연속 5회 실패하면 그 큐브 자동 제어 일시 정지(사용자가 다시 켤 때까지).
  사람 조작      사람이 제어하면 자동 제어가 꺼진다(main 의 제어 라우트가 처리).
  기록           명령을 보낼 때만 audit(주체 "auto"). 범위 이탈 시작·해제는 상태 조회에만 담는다(알림 없음).
"""
import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from . import crops

KST = timezone(timedelta(hours=9))

INTERVAL_S = float(os.environ.get("AUTO_INTERVAL_SECONDS", "10") or "10")
FAN_STEPS = (0, 33, 66, 100)
TEMP_BAND = 1.0          # °C
HUM_BAND = 5.0           # %p
FAN_HOLD_S = 120.0
PUMP_PULSE_S = 5.0
PUMP_WAIT_S = 3600.0
PUMP_MAX_PER_DAY = 4
LED_STEP = 10
LED_DAY_START = 50
STALE_S = 30.0
RETRY_MIN_S = 30.0
FAIL_PAUSE_AFTER = 5
APPLY_GRACE_S = 15.0     # 보낸 값이 텔레메트리에 나타나기를 기다리는 시간(같은 값 재전송 방지)
SOIL_DEFAULT_AIR = 22520
SOIL_DEFAULT_WATER = 11100
EVENTS_KEEP = 20


class _AutoActor:
    """audit 기록 주체. 이메일 칸 "auto", 사용자 ID 없음(AC-14)."""
    email = "auto"
    sub = "auto"

    def user_uuid(self):
        return None


ACTOR = _AutoActor()


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def kst_minutes(now: float) -> int:
    t = datetime.fromtimestamp(now, KST)
    return t.hour * 60 + t.minute


def kst_day(now: float) -> str:
    return datetime.fromtimestamp(now, KST).strftime("%Y-%m-%d")


def is_day(profile: dict, minute: int) -> bool:
    on, off = profile["photoperiod"]["on_min"], profile["photoperiod"]["off_min"]
    if on < off:
        return on <= minute < off
    return minute >= on or minute < off          # 자정을 넘기는 광주기


def nearest_step(v) -> int:
    x = _num(v)
    if x is None:
        return 0
    return min(range(len(FAN_STEPS)), key=lambda i: abs(FAN_STEPS[i] - x))


def soil_calibrated(status: Optional[dict]) -> Optional[bool]:
    """True = 보정됨, False = 공장 기본값 그대로, None = 상태를 몰라 판단 불가."""
    if not status or "air" not in status or "water" not in status:
        return None
    return not (_num(status.get("air")) == SOIL_DEFAULT_AIR and _num(status.get("water")) == SOIL_DEFAULT_WATER)


def new_state() -> dict:
    return {"fan_idx": None, "fan_changed_at": 0.0, "led": None, "was_day": None,
            "pump_last_at": 0.0, "pump_day": None, "pump_count": 0, "pump_until": 0.0, "pump_started_at": 0.0,
            "fails": 0, "next_try_at": 0.0, "paused": None,
            "last_sent": None, "last_sent_at": 0.0, "last_result": None, "last_action": None,
            "last_eval": None, "rules": [], "alerts": [], "phase": None, "excursions": {}, "events": [],
            "manual_at": 0.0}


def _excursion(st: dict, item: str, value, rng: Optional[dict], now: float):
    """범위 이탈 시작·해제를 상태에만 기록(AC-17: 내부적으로만)."""
    cur = st["excursions"].get(item)
    x = _num(value)
    side = None
    if x is not None and rng is not None:
        if x < rng["min"]:
            side = "low"
        elif x > rng["max"]:
            side = "high"
    if side and (cur is None or cur["side"] != side):
        st["excursions"][item] = {"side": side, "since": now, "value": x, "min": rng["min"], "max": rng["max"]}
        st["events"].append({"t": now, "item": item, "event": "start", "side": side, "value": x})
    elif side and cur is not None:
        cur["value"] = x
    elif not side and cur is not None and (x is not None or rng is None):
        st["excursions"].pop(item, None)
        st["events"].append({"t": now, "item": item, "event": "end", "value": x})
    del st["events"][:-EVENTS_KEEP]


def evaluate(profile: dict, values: Optional[dict], status: Optional[dict], values_age: Optional[float],
             st: dict, now: float, irrigation: bool) -> dict:
    """한 번 판정. st(큐브별 기억)를 갱신하고 원하는 출력과 근거를 돌려준다.

    돌려주는 값: {"desired": {"fan1","led1","pump"} 또는 None(판단 안 함), "rules": [...], "alerts": [...],
                 "pump_start": bool, "phase": "day"|"night", "readings": {...}, "ranges": {...}}
    """
    minute = kst_minutes(now)
    day = is_day(profile, minute)
    phase = "day" if day else "night"
    trng = profile["temp_day"] if day else profile["temp_night"]
    hrng = profile["humidity_day"] if day else profile["humidity_night"]
    srng = profile["soil_moisture"]
    lrng = profile["lux_day"]
    out = {"desired": None, "rules": [], "alerts": [], "pump_start": False, "phase": phase,
           "readings": {}, "ranges": {"temp": trng, "humidity": hrng, "soil_moisture": srng,
                                      "lux": lrng if day else None}}
    if values is None or values_age is None or values_age > STALE_S:
        out["alerts"].append("센서 점검 필요: 보고가 %d초 넘게 없음" % int(STALE_S))
        return out
    if values.get("diag_mode") is True:
        out["alerts"].append("진단 모드라 판단하지 않음")
        return out

    t, h = _num(values.get("temperature")), _num(values.get("humidity"))
    soil, lux = _num(values.get("moisture_pct")), _num(values.get("lux"))
    rep_fan, rep_led = _num(values.get("fan1")), _num(values.get("led1"))
    rep_pump = values.get("pump") is True
    out["readings"] = {"temperature": t, "humidity": h, "moisture_pct": soil, "lux": lux,
                       "fan1": rep_fan, "led1": rep_led, "pump": rep_pump}
    _excursion(st, "temp", t, trng, now)
    _excursion(st, "humidity", h, hrng, now)
    _excursion(st, "soil_moisture", soil, srng, now)
    _excursion(st, "lux", lux if day else None, lrng if day else None, now)

    # ── 배기팬 ──
    if st["fan_idx"] is None:
        st["fan_idx"] = nearest_step(rep_fan)
    idx = st["fan_idx"]
    fan_out = FAN_STEPS[idx]
    if t is None or h is None:
        out["alerts"].append("센서 점검 필요: 온습도 값 없음")
        if rep_fan is not None:
            fan_out = int(rep_fan)                     # 출력 유지
    else:
        applied = rep_fan is not None and int(rep_fan) == FAN_STEPS[idx]
        held = st["fan_changed_at"] and now - st["fan_changed_at"] < FAN_HOLD_S
        want = idx
        if t < trng["min"]:
            want, why = 0, "온도 낮음"
        elif t > trng["max"]:
            want, why = min(idx + 1, len(FAN_STEPS) - 1), "온도 높음"
        elif h > hrng["max"]:
            want, why = min(idx + 1, len(FAN_STEPS) - 1), "습도 높음"
        elif h < hrng["min"]:
            want, why = 0, "습도 낮음"
        elif idx > 0 and t <= trng["max"] - TEMP_BAND and h <= hrng["max"] - HUM_BAND:
            want, why = idx - 1, "범위 안으로 돌아옴"
        else:
            why = None
        if why:
            out["rules"].append("배기팬: " + why)
        if want != idx and applied and not held:
            st["fan_idx"] = want
            st["fan_changed_at"] = now
        fan_out = FAN_STEPS[st["fan_idx"]]

    # ── LED ──
    if not day:
        st["led"] = 0
    else:
        if st["was_day"] is False:                     # 야간 → 주간
            st["led"] = LED_DAY_START
        elif st["led"] is None:                        # 주간 도중 시작·재시작
            st["led"] = int(rep_led) if rep_led is not None else LED_DAY_START
        elif lux is None:
            out["alerts"].append("센서 점검 필요: 조도 값 없음")
        elif rep_led is not None and int(rep_led) == st["led"]:   # 앞 단계가 적용된 뒤에만
            if lux < lrng["min"] and st["led"] < 100:
                st["led"] = min(100, st["led"] + LED_STEP)
                out["rules"].append("LED: 조도 낮음")
            elif lux > lrng["max"] and st["led"] > 0:
                st["led"] = max(0, st["led"] - LED_STEP)
                out["rules"].append("LED: 조도 높음")
    st["was_day"] = day

    # ── 급수 ──
    pump = now < st["pump_until"]                      # 펄스 진행 중이면 켜진 상태 유지
    if irrigation:
        today = kst_day(now)
        if st["pump_day"] != today:
            st["pump_day"], st["pump_count"] = today, 0
        cal = soil_calibrated(status)
        if cal is None:
            out["alerts"].append("급수 안 함: 장치 상태(보정값)를 아직 모름")
        elif cal is False:
            out["alerts"].append("급수 안 함: 토양 보정값이 공장 기본값")
        elif values.get("ads1115_ok") is False or soil is None:
            out["alerts"].append("센서 점검 필요: 토양 값 없음")
        elif soil < srng["min"] and not pump:
            if st["pump_count"] >= PUMP_MAX_PER_DAY:
                out["rules"].append("급수: 오늘 %d회 다 씀" % PUMP_MAX_PER_DAY)
            elif st["pump_last_at"] and now - st["pump_last_at"] < PUMP_WAIT_S:
                out["rules"].append("급수: 재급수 대기 중")
            else:
                out["rules"].append("급수: 토양 건조")
                out["pump_start"] = True
                pump = True

    out["desired"] = {"fan1": fan_out, "led1": int(st["led"]), "pump": pump}
    return out


def diff(desired: dict, readings: dict) -> dict:
    """장치 보고와 다른 출력만."""
    d = {}
    for k in ("fan1", "led1"):
        if readings.get(k) is None or int(readings[k]) != desired[k]:
            d[k] = desired[k]
    if bool(readings.get("pump")) != desired["pump"]:
        d["pump"] = desired["pump"]
    return d


# ══════════════════════════════════════════════ 루프 (main 이 bind 로 연결)

_states: Dict[str, dict] = {}
_locks: Dict[str, asyncio.Lock] = {}
_deps = {}
_log: Callable = print
_task: Optional[asyncio.Task] = None
stats = {"running": False, "ticks": 0, "commands": 0, "failures": 0, "last_tick": None, "last_error": None}


def set_logger(fn):
    global _log
    _log = fn


def bind(*, enabled: Callable, snapshot: Callable, send: Callable):
    """enabled() -> [(iid, sn, settings)], snapshot(sn) -> (values, status, values_at), send(iid, sn, body, extra) -> result."""
    _deps.update(enabled=enabled, snapshot=snapshot, send=send)


def state(sn: str) -> dict:
    return _states.setdefault(sn, new_state())


def reset(sn: str):
    _states.pop(sn, None)


def note_manual(sn: str, now: Optional[float] = None):
    """사람 조작: 자동 상태를 버린다(진행 중인 급수 펄스의 끄기도 사람에게 넘긴다)."""
    st = _states.get(sn)
    if st is not None:
        st["manual_at"] = now or time.time()
        st["pump_until"] = 0.0
    _states.pop(sn, None)


def view(sn: str) -> Optional[dict]:
    st = _states.get(sn)
    if st is None:
        return None
    return {k: st[k] for k in ("phase", "rules", "alerts", "excursions", "events", "paused", "fails",
                               "last_eval", "last_action", "pump_count", "pump_last_at")}


def _lock(sn: str) -> asyncio.Lock:
    lk = _locks.get(sn)
    if lk is None:
        lk = _locks[sn] = asyncio.Lock()
    return lk


async def _send(iid: str, sn: str, st: dict, body: dict, res: dict, pump_start: bool, now: float):
    async with _lock(sn):
        extra = {"crop": res.get("crop"), "phase": res["phase"], "rules": res["rules"],
                 "readings": res["readings"], "ranges": res["ranges"]}
        try:
            result = await _deps["send"](iid, sn, body, extra)
        except Exception as e:   # noqa: BLE001  (HTTPException 503/409/504 포함)
            result = "error: %s" % (getattr(e, "detail", None) or str(e))[:100]
        stats["commands"] += 1
        st["last_sent"], st["last_sent_at"], st["last_result"] = dict(body), now, result
        st["last_action"] = {"t": now, "sent": dict(body), "result": result, "rules": list(res["rules"])}
        if result == "ok":
            st["fails"] = 0
            st["next_try_at"] = 0.0
            if pump_start:
                st["pump_last_at"] = now
                st["pump_count"] += 1
                st["pump_until"] = now + PUMP_PULSE_S
                st["pump_started_at"] = now
                asyncio.get_running_loop().create_task(_pulse_off(iid, sn, now))
        else:
            stats["failures"] += 1
            st["fails"] += 1
            st["next_try_at"] = now + RETRY_MIN_S
            if st["fails"] >= FAIL_PAUSE_AFTER:
                st["paused"] = "연속 %d회 실패(마지막: %s) — 자동 제어를 다시 켜면 재개" % (st["fails"], result)
                _log("[auto] %s 일시 정지: %s" % (sn, st["paused"]))


async def _pulse_off(iid: str, sn: str, started_at: float):
    """급수 펄스 끄기. 센서 보고가 끊겨도 보낸다(사용자 판정). 사람이 그 사이 조작했으면 사람에게 맡긴다."""
    await asyncio.sleep(PUMP_PULSE_S)
    for attempt in range(2):
        st = _states.get(sn)
        if st is None or st["pump_started_at"] != started_at or st["manual_at"] > started_at:
            return
        body = {"pump": False, "fan1": FAN_STEPS[st["fan_idx"] or 0], "led1": int(st["led"] or 0)}
        res = {"phase": st.get("phase"), "rules": ["급수: 펄스 끝"], "readings": {}, "ranges": {}}
        async with _lock(sn):
            try:
                result = await _deps["send"](iid, sn, body, {"rules": res["rules"]})
            except Exception as e:   # noqa: BLE001
                result = "error: %s" % (getattr(e, "detail", None) or str(e))[:100]
        st["last_action"] = {"t": time.time(), "sent": body, "result": result, "rules": res["rules"]}
        if result == "ok":
            st["pump_until"] = 0.0
            return
        _log("[auto] %s 급수 끄기 실패(%s) — %s" % (sn, result, "재시도" if attempt == 0 else "펌웨어 30초 자동 정지에 맡김"))
        await asyncio.sleep(PUMP_PULSE_S)
    st = _states.get(sn)
    if st is not None:
        st["pump_until"] = 0.0


def tick(now: Optional[float] = None) -> List[asyncio.Task]:
    """한 번 판정하고 필요한 명령을 작업으로 띄운다. 돌려주는 값 = 띄운 송신 작업(시험용)."""
    now = now or time.time()
    stats["ticks"] += 1
    stats["last_tick"] = now
    tasks = []
    enabled = {sn: (iid, s) for iid, sn, s in _deps["enabled"]()}
    for sn in list(_states):
        if sn not in enabled:
            _states.pop(sn, None)                     # 꺼진 큐브는 기억을 버린다(다시 켜면 처음부터)
    for sn, (iid, s) in enabled.items():
        st = state(sn)
        prof = crops.profile(s.get("crop_id"))
        if prof is None:
            st["alerts"] = ["작물이 없거나 작물표에 없음: %s" % s.get("crop_id")]
            continue
        if st["paused"]:
            continue
        values, status, values_at = _deps["snapshot"](sn)
        age = (now - values_at) if values_at else None
        res = evaluate(prof, values, status, age, st, now, True)   # 급수는 자동 제어에 포함(별도 스위치 없음)
        res["crop"] = prof["id"]
        st.update(last_eval=now, rules=res["rules"], alerts=res["alerts"], phase=res["phase"])
        if res["desired"] is None or _lock(sn).locked():
            continue
        body = diff(res["desired"], res["readings"])
        if not body:
            continue
        if now < st["next_try_at"]:
            continue
        if (st["last_result"] == "ok" and st["last_sent"] is not None and now - st["last_sent_at"] < APPLY_GRACE_S
                and all(st["last_sent"].get(k) == v for k, v in body.items())):
            continue                                   # 방금 보낸 값이 아직 텔레메트리에 안 나타남
        full = dict(res["desired"])                    # 펌프를 포함한 세 출력을 모두 직접 채운다(§6.3)
        tasks.append(asyncio.get_running_loop().create_task(_send(iid, sn, st, full, res, res["pump_start"], now)))
    return tasks


async def run():
    stats["running"] = True
    _log("[auto] 자동 제어 루프 시작: %.0f초마다, 작물 %d종" % (INTERVAL_S, crops.status()["crops"]))
    try:
        while True:
            await asyncio.sleep(INTERVAL_S)
            try:
                tick()
            except Exception as e:   # noqa: BLE001
                stats["last_error"] = str(e)[:200]
                _log("[auto] 판정 오류: %s" % str(e)[:160])
    except asyncio.CancelledError:
        stats["running"] = False
        return


def status():
    return {"interval_s": INTERVAL_S, "cubes": len(_states), **stats, "crops": crops.status()}
