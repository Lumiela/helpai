# -*- coding: utf-8 -*-
"""Laya(영어판, 구판 엔진)에 시험 문제를 물어 답을 저장한다. 채점은 score.py."""
import json, os, sys, time
from pathlib import Path

os.environ.setdefault("LAYA_CUDA_AMP", "fp32")       # GTX 16 계열: bf16 미지원, fp16 은 NaN 위험
os.environ.update(USE_TF="0", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
HERE = Path(__file__).parent
MODEL = os.environ.get("LAYA_MODEL_DIR") or str(HERE.parent / "model")
OUT = os.environ.get("LAYA_OUT", "results.json")
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 0

import torch  # noqa: E402
import laya   # noqa: E402

D = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
NAMES = {"temperature": "temperature", "humidity": "humidity", "moisture_pct": "soil moisture", "lux": "light level"}


def g(v):
    return int(v) if float(v).is_integer() else v


# ── 상태 쓰는 방법 3가지 ──
def st_json(c):
    return {"sensor": NAMES[c["sensor"]], "value": g(c["value"]), "unit": D["units"][c["sensor"]],
            "allowed_minimum": c["min"], "allowed_maximum": c["max"]}


def st_text(c):
    u, n = D["units"][c["sensor"]], NAMES[c["sensor"]]
    return "The %s is %s %s. The allowed range is from %s to %s %s." % (n, g(c["value"]), u, c["min"], c["max"], u)


def st_done(c):  # 대조: 비교는 코드가 끝내고 문장으로만 알려 줌
    n = NAMES[c["sensor"]]
    return {"below": "The %s is lower than the allowed minimum." % n,
            "within": "The %s is inside the allowed range." % n,
            "above": "The %s is higher than the allowed maximum." % n}[c["label"]]


CRIT = {"below_range": "the value is lower than the allowed minimum",
        "within_range": "the value is between the allowed minimum and the allowed maximum",
        "above_range": "the value is higher than the allowed maximum"}
Q_CHOICE = {"range": {"type": "choice", "instructions": "Where is the value compared with the allowed range?", "criteria": CRIT}}
Q_REV = {"range": {"type": "choice", "instructions": "Where is the value compared with the allowed range?",
                   "criteria": dict(reversed(list(CRIT.items())))}}
Q_NOUL = {"high": {"type": "noul", "instructions": "The value is higher than the allowed maximum.",
                   "criteria": {"false": "the value is equal to or lower than the allowed maximum",
                                "true": "the value is higher than the allowed maximum"}},
          "low": {"type": "noul", "instructions": "The value is lower than the allowed minimum.",
                  "criteria": {"false": "the value is equal to or higher than the allowed minimum",
                               "true": "the value is lower than the allowed minimum"}}}

# ── 배기팬 결정 ──
R = D["ranges"]


def fan_json(c):
    return {"temperature_celsius": g(c["temperature"]), "temperature_minimum": R["temperature"]["min"],
            "temperature_maximum": R["temperature"]["max"], "humidity_percent": g(c["humidity"]),
            "humidity_minimum": R["humidity"]["min"], "humidity_maximum": R["humidity"]["max"],
            "exhaust_fan_speed_percent": 33}


def fan_done(c):  # 대조: 온도·습도 각각의 판정은 코드가 끝냄
    t, h = c["temperature"], c["humidity"]
    ts = "below its minimum" if t < 22 else "above its maximum" if t > 30 else "near its maximum" if t > 29 else "well inside its range"
    hs = "below its minimum" if h < 60 else "above its maximum" if h > 70 else "near its maximum" if h > 65 else "well inside its range"
    return {"temperature": ts, "humidity": hs, "exhaust_fan_speed_percent": 33}


Q_FAN = {"fan": {"type": "choice", "instructions": "What should the exhaust fan do next?", "criteria": {
    "raise_speed": "the temperature is above 30, or the temperature is 22 to 30 and the humidity is above 70",
    "lower_speed": "the temperature is below 22, or the temperature is 22 to 30 and the humidity is below 60, or the temperature is at most 29 and the humidity is at most 65",
    "keep_speed": "none of the other conditions holds"}}}
Q_FAN_DONE = {"fan": {"type": "choice", "instructions": "What should the exhaust fan do next?", "criteria": {
    "raise_speed": "the temperature is above its maximum, or the temperature is not below its minimum and the humidity is above its maximum",
    "lower_speed": "the temperature is below its minimum, or the humidity is below its minimum, or both are well inside their ranges",
    "keep_speed": "none of the other conditions holds"}}}

t0 = time.time()
agent = laya.load(MODEL, device="cuda", vegaml=False)
print("load %.1fs dtype %s" % (time.time() - t0, getattr(agent, "dtype", None)), flush=True)
rc, fc = D["range_cases"], D["fan_cases"]
if LIMIT:
    rc, fc = rc[::max(1, len(rc) // LIMIT)], fc[::max(1, len(fc) // LIMIT)]
RUNS = [("A_json_choice", rc, st_json, Q_CHOICE), ("A_json_choice_reversed", rc, st_json, Q_REV), ("A_json_noul", rc, st_json, Q_NOUL),
        ("B_text_choice", rc, st_text, Q_CHOICE), ("B_text_noul", rc, st_text, Q_NOUL),
        ("C_done_choice", rc, st_done, Q_CHOICE), ("C_done_noul", rc, st_done, Q_NOUL),
        ("F_json", fc, fan_json, Q_FAN), ("F_done", fc, fan_done, Q_FAN_DONE)]
out = {}
for name, cs, mk, q in RUNS:
    t0 = time.time()
    res = []
    for i in range(0, len(cs), 8):
        res += agent.predict_batch([mk(c) for c in cs[i:i + 8]], q)
    out[name] = [{"case": c, "answers": r.get("answers", r)} for c, r in zip(cs, res)]
    print(name, len(cs), "%.1fs" % (time.time() - t0), flush=True)
    if LIMIT:
        print("   ", json.dumps(out[name][0], ensure_ascii=False, default=str)[:700], flush=True)

lat = []
for i in range(40):
    t0 = time.time()
    agent.predict(st_json(rc[i % len(rc)]), Q_CHOICE)
    lat.append((time.time() - t0) * 1000)
lat = sorted(lat[5:])
meta = dict(model=MODEL.replace("\\", "/").rsplit("/", 1)[-1], laya=laya.__version__, torch=torch.__version__, dtype=str(getattr(agent, "dtype", None)),
            gpu=torch.cuda.get_device_name(0), gpu_max_mb=round(torch.cuda.max_memory_allocated() / 2**20),
            latency_ms_median=round(lat[len(lat) // 2], 1), latency_ms_p95=round(lat[int(len(lat) * 0.95)], 1))
print(meta)
(HERE / ("results_smoke.json" if LIMIT else OUT)).write_text(
    json.dumps(dict(meta=meta, runs=out), ensure_ascii=False, default=str), encoding="utf-8")
