# -*- coding: utf-8 -*-
"""작물 프로필(rose.yaml) 읽기 + 일장 판단. rules v2 의 domain/profile.py·growth.py 와 같은 의미."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE = ROOT / "profiles" / "rose.yaml"


def _hhmm(s):
    h, m = str(s).split(":")
    return int(h) * 60 + int(m)


def load_profile(path=None):
    p = Path(path) if path else DEFAULT_PROFILE
    with open(p, encoding="utf-8") as f:
        prof = yaml.safe_load(f)
    pp = prof["photoperiod"]
    pp["_on_min"] = _hhmm(pp["start"])
    pp["_off_min"] = _hhmm(pp["end"])
    return prof


def is_light_period(now, profile):
    """now(현지 datetime)가 조명 허용 시간인가. 겨울(night_lighting_months)은 하루 종일 (rules v2 growth.py 와 동일)."""
    pp = profile["photoperiod"]
    if now.month in pp["night_lighting_months"]:
        return True
    m = now.hour * 60 + now.minute
    on, off = pp["_on_min"], pp["_off_min"]
    if on <= off:
        return on <= m < off
    return m >= on or m < off
