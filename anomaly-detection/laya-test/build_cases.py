# -*- coding: utf-8 -*-
"""시험 문제 만들기. 정답은 api/app/autoctl.py 의 규칙과 작물표(crops.json rose)에서 나온다.
- real : CUBE001 10분 간격 실제 기록
- synth: 경계 근처를 포함한 합성 값(실제 기록에 상한 초과가 거의 없어 보완)
- fan  : evaluate() 로 배기팬 다음 단계를 구한 것(현재 33 %)
"""
import csv, json, os, random, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = Path(os.environ.get("SMARTCUBE_API_DIR", "api"))   # 서버 api 코드 폴더(edgeserver-JS/api) 위치
HERE = Path(__file__).parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(API))
from app import autoctl, crops  # noqa: E402

random.seed(20261125)
KST = timezone(timedelta(hours=9))
RAW = json.loads((API / "app" / "crops.json").read_text(encoding="utf-8"))["crops"]["rose"]
for name in ("load", "reload", "init"):
    if hasattr(crops, name):
        getattr(crops, name)()
        break
PROF = crops.profile("rose")
assert PROF, "작물표 rose 를 읽지 못함"

SENSORS = {
    "temperature": dict(rng=RAW["temp_day"], unit="degrees Celsius", dec=1, lo=5, hi=45),
    "humidity": dict(rng=RAW["humidity_day"], unit="percent", dec=1, lo=15, hi=95),
    "moisture_pct": dict(rng=RAW["soil_moisture"], unit="percent", dec=0, lo=0, hi=100),
    "lux": dict(rng=RAW["lux_day"], unit="lux", dec=1, lo=0, hi=50000),
}


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def label(s, v):
    r = SENSORS[s]["rng"]
    return "below" if v < r["min"] else "above" if v > r["max"] else "within"


rows = list(csv.DictReader((HERE / "cube001_10min.csv").open(encoding="utf-8")))
cases = []

# 1) 실제 기록: 항목·판정별 최대 100개
for s in SENSORS:
    bucket = {"below": [], "within": [], "above": []}
    for r in rows:
        v = num(r[s])
        if v is None:
            continue
        if s == "lux":
            t = datetime.strptime(r["time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(KST)
            if not autoctl.is_day(PROF, t.hour * 60 + t.minute):
                continue
        bucket[label(s, v)].append((r["time"], v))
    for lb, items in bucket.items():
        for t, v in random.sample(items, min(100, len(items))):
            cases.append(dict(set="real", sensor=s, value=round(v, 1), min=SENSORS[s]["rng"]["min"],
                              max=SENSORS[s]["rng"]["max"], label=lb, time=t))

# 2) 합성: 항목마다 below/within/above 각 40개, 경계와의 거리는 범위 폭의 1 %~150 %
for s, c in SENSORS.items():
    lo, hi = c["rng"]["min"], c["rng"]["max"]
    w = hi - lo
    for lb in ("below", "within", "above"):
        n, guard = 0, 0
        while n < 40 and guard < 5000:
            guard += 1
            m = w * 10 ** random.uniform(-2, 0.18)
            if lb == "below":
                v = lo - m
            elif lb == "above":
                v = hi + m
            else:
                v = random.choice([lo + min(m, w * 0.5), hi - min(m, w * 0.5)])
            v = round(v, c["dec"])
            if not (c["lo"] <= v <= c["hi"]) or v in (lo, hi) or label(s, v) != lb:
                continue
            cases.append(dict(set="synth", sensor=s, value=v, min=lo, max=hi, label=lb, time=None))
            n += 1

# 3) 배기팬 결정: 실제 규칙 함수 evaluate() 를 그대로 부른다(현재 33 %, 최근 변경 없음)
NOON = datetime(2026, 11, 20, 12, 0, tzinfo=KST).timestamp()


def fan_next(t, h):
    st = autoctl.new_state()
    st["fan_idx"] = 1
    vals = dict(temperature=t, humidity=h, moisture_pct=50, lux=8000, fan1=33, led1=50, pump=False)
    res = autoctl.evaluate(PROF, vals, None, 1.0, st, NOON, False)
    return {0: "lower", 33: "keep", 66: "raise"}[res["desired"]["fan1"]]


pool = {"lower": [], "keep": [], "raise": []}
for r in rows:
    t, h = num(r["temperature"]), num(r["humidity"])
    if t is None or h is None:
        continue
    t, h = round(t, 1), round(h, 1)
    pool[fan_next(t, h)].append((t, h, "real"))
picked = []
for lb, items in pool.items():
    picked += [(lb, *x) for x in random.sample(items, min(100, len(items)))]
print("fan real", {k: len(v) for k, v in pool.items()})
need = {lb: 100 - sum(p[0] == lb for p in picked) for lb in pool}
guard = 0
while any(v > 0 for v in need.values()) and guard < 200000:
    guard += 1
    t, h = round(random.uniform(16, 36), 1), round(random.uniform(40, 85), 1)
    lb = fan_next(t, h)
    if need[lb] > 0:
        picked.append((lb, t, h, "synth"))
        need[lb] -= 1
fan = [dict(set=src, temperature=t, humidity=h, label=lb) for lb, t, h, src in picked]

out = dict(ranges={s: c["rng"] for s, c in SENSORS.items()}, units={s: c["unit"] for s, c in SENSORS.items()},
           range_cases=cases, fan_cases=fan)
(HERE / "cases.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
from collections import Counter  # noqa: E402
print(len(cases), Counter((c["set"], c["sensor"], c["label"]) for c in cases))
print(len(fan), Counter((c["set"], c["label"]) for c in fan))
