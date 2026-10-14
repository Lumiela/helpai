# -*- coding: utf-8 -*-
"""
작물 환경표 — app/crops.json 을 기동 때 한 번 읽고 검사한다 (2026-10-06, 작물 기반 자동 제어).

  · 작물 ID(자체 영문 코드) → 그 작물의 주간·야간 범위. 값은 사용자가 준 것만 넣는다(계획서 §1·§12.3).
  · 온도·습도는 주간/야간 따로, 토양 수분은 하루 종일 한 범위, 조도는 주간 목표 범위(lux).
  · 주간/야간 경계 = 광주기(켜는 시각 ~ 끄는 시각 = 주간, 한국 시간). 끄는 시각이 켜는 시각보다 이르면 자정을 넘긴다.
  · 값을 고치면 api 재배포로 반영한다(AC-15). 검사에 실패한 작물은 자동 제어 대상에서 빠지고 사유가 남는다.

형식
  {"version": 1, "crops": {"<작물 ID>": {"name", "source", "temp_day":{"min","max"}, "temp_night", "humidity_day",
   "humidity_night", "soil_moisture", "photoperiod":{"on":"HH:MM","off":"HH:MM"}, "lux_day"}}}
"""
import json
import os
import re
from typing import Dict, Optional

CROPS_FILE = os.environ.get("CROPS_FILE", "").strip() or os.path.join(os.path.dirname(__file__), "crops.json")
CROP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
HHMM_RE = re.compile(r"^([01]\d|2[0-4]):([0-5]\d)$")
RANGES = ("temp_day", "temp_night", "humidity_day", "humidity_night", "soil_moisture", "lux_day")

_crops: Dict[str, dict] = {}       # 검사를 통과한 작물
_invalid: Dict[str, str] = {}      # 작물 ID -> 사유
stats = {"file": CROPS_FILE, "loaded": False, "error": None}


def hhmm_to_min(s: str) -> int:
    m = HHMM_RE.match(s or "")
    if not m:
        raise ValueError("시각은 HH:MM (00:00~24:00): %r" % s)
    v = int(m.group(1)) * 60 + int(m.group(2))
    if v > 24 * 60:
        raise ValueError("24:00 을 넘는 시각: %r" % s)
    return v


def validate(cid: str, d) -> dict:
    """검사 통과 시 정리된 사본(광주기 분 단위 포함)을 돌려준다. 실패하면 ValueError."""
    if not CROP_ID_RE.match(cid or ""):
        raise ValueError("작물 ID 는 영문·숫자·-_ 1~40자")
    if not isinstance(d, dict):
        raise ValueError("작물 항목이 객체가 아님")
    out = {"id": cid, "name": str(d.get("name") or cid), "source": d.get("source")}
    for key in RANGES:
        r = d.get(key)
        if not isinstance(r, dict):
            raise ValueError("%s 가 없음" % key)
        lo, hi = r.get("min"), r.get("max")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lo, hi)):
            raise ValueError("%s 의 min·max 는 숫자" % key)
        if not lo < hi:
            raise ValueError("%s: min(%s) < max(%s) 이어야 함" % (key, lo, hi))
        out[key] = {"min": float(lo), "max": float(hi)}
    for key in ("humidity_day", "humidity_night", "soil_moisture"):
        if out[key]["min"] < 0 or out[key]["max"] > 100:
            raise ValueError("%s 는 0~100 %%" % key)
    pp = d.get("photoperiod")
    if not isinstance(pp, dict):
        raise ValueError("photoperiod 가 없음")
    on, off = hhmm_to_min(pp.get("on")), hhmm_to_min(pp.get("off"))
    if on % 1440 == off % 1440:
        raise ValueError("광주기 켜는 시각과 끄는 시각이 같음")
    out["photoperiod"] = {"on": pp["on"], "off": pp["off"], "on_min": on % 1440, "off_min": off % 1440}
    return out


def load(path: Optional[str] = None) -> bool:
    """파일을 읽어 표를 바꾼다. 파일 자체를 못 읽으면 표를 비우고 False."""
    p = path or CROPS_FILE
    crops, invalid = {}, {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
        items = raw.get("crops") if isinstance(raw, dict) else None
        if not isinstance(items, dict):
            raise ValueError("최상위에 crops 객체가 없음")
    except Exception as e:   # noqa: BLE001
        _crops.clear()
        _invalid.clear()
        stats.update({"file": p, "loaded": False, "error": str(e)[:200]})
        return False
    for cid, d in items.items():
        try:
            crops[cid] = validate(cid, d)
        except ValueError as e:
            invalid[cid] = str(e)
    _crops.clear()
    _crops.update(crops)
    _invalid.clear()
    _invalid.update(invalid)
    stats.update({"file": p, "loaded": True, "error": None})
    return True


def set_table(items: Dict[str, dict]):
    """시험용: 파일 없이 표를 바꾼다."""
    _crops.clear()
    _invalid.clear()
    for cid, d in items.items():
        try:
            _crops[cid] = validate(cid, d)
        except ValueError as e:
            _invalid[cid] = str(e)
    stats.update({"loaded": True, "error": None})


def profile(cid: Optional[str]) -> Optional[dict]:
    return _crops.get(cid) if cid else None


def invalid_reason(cid: Optional[str]) -> Optional[str]:
    return _invalid.get(cid) if cid else None


def listing():
    return {"crops": [dict(v) for _, v in sorted(_crops.items())],
            "invalid": dict(_invalid), "file_error": stats["error"]}


def status():
    return {"loaded": stats["loaded"], "crops": len(_crops), "invalid": len(_invalid), "error": stats["error"]}
