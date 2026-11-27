# -*- coding: utf-8 -*-
"""
작물 환경표 읽기 + 주간 판단 — 서버 api 의 crops.json·crops.py·autoctl.is_day 와 같은 의미.

  profiles/crops.json 은 backend 브랜치 api/app/crops.json 의 복사본이다(2026-11-27, 28aed59).
  서버 쪽 값이 바뀌면 이 파일도 같이 바꾸고 데이터셋부터 다시 만든다.

  profile 사전 키 (autoctl 이 쓰는 그대로):
      temp_day·temp_night·humidity_day·humidity_night·soil_moisture·lux_day = {"min", "max"}
      photoperiod = {"on", "off", "on_min", "off_min"}   (KST, off < on 이면 자정을 넘긴다)
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE = ROOT / "profiles" / "crops.json"
DEFAULT_CROP = "rose"
RANGES = ("temp_day", "temp_night", "humidity_day", "humidity_night", "soil_moisture", "lux_day")


def _hhmm(s):
    h, m = str(s).split(":")
    v = int(h) * 60 + int(m)
    if not 0 <= v <= 24 * 60:
        raise ValueError("시각은 HH:MM (00:00~24:00): %r" % s)
    return v


def load_profile(path=None, crop=DEFAULT_CROP):
    with open(Path(path) if path else DEFAULT_PROFILE, encoding="utf-8") as f:
        raw = json.load(f)
    d = raw["crops"][crop]
    out = {"id": crop, "name": d.get("name", crop), "source": d.get("source")}
    for key in RANGES:
        lo, hi = float(d[key]["min"]), float(d[key]["max"])
        if not lo < hi:
            raise ValueError("%s: min < max 이어야 함" % key)
        out[key] = {"min": lo, "max": hi}
    on, off = _hhmm(d["photoperiod"]["on"]) % 1440, _hhmm(d["photoperiod"]["off"]) % 1440
    if on == off:
        raise ValueError("광주기 켜는 시각과 끄는 시각이 같음")
    out["photoperiod"] = {"on": d["photoperiod"]["on"], "off": d["photoperiod"]["off"], "on_min": on, "off_min": off}
    return out


def is_light_period(now, profile):
    """now(현지 KST datetime) 가 주간인가 (autoctl.is_day 와 같음)."""
    minute = now.hour * 60 + now.minute
    on, off = profile["photoperiod"]["on_min"], profile["photoperiod"]["off_min"]
    if on < off:
        return on <= minute < off
    return minute >= on or minute < off


def ranges_at(now, profile):
    """지금 적용되는 (온도 범위, 습도 범위)."""
    if is_light_period(now, profile):
        return profile["temp_day"], profile["humidity_day"]
    return profile["temp_night"], profile["humidity_night"]
