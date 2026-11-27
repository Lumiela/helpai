# -*- coding: utf-8 -*-
"""Chronos-2 로 CUBE001 온도·습도를 30분·60분·3시간 뒤까지 예측하고 기준선과 비교한다.

데이터: edgeserver-JS/laya-test/cube001_10min.csv (10분 간격, 2026-09-26 ~ 11-25, 빈 칸 = 기록 없음)
예측 시점(origin): 30분마다, 지금 온도·습도 값이 있고 직전 12시간 중 6시간 이상 기록이 있는 시점
비교 대상
  persist      지금 값이 그대로 간다고 봄(기준선)
  season24     24시간 전 같은 시각의 값(있을 때만)
  c2_uni       Chronos-2, 온도·습도 각각 따로
  c2_multi     Chronos-2, 온도·습도 함께 + 과거 배기팬·LED·조도
  c2_future    c2_multi + 앞으로의 배기팬·LED 값을 알려 줌(실제 기록을 넣음 — 제어기가 정한 계획을 안다고 가정)
"""
import json, os, sys, time
from pathlib import Path

os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
import numpy as np
import pandas as pd
import psutil
import torch
from chronos import BaseChronosPipeline

HERE = Path(__file__).parent
MODEL = str(HERE.parent / "model")
CSV = sys.argv[1]
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 0
CTX, HMAX, HS, STEP = 512, 18, (3, 6, 18), 3          # 10분 단위: 512 = 85시간, 18 = 3시간
RNG = {"temperature": (22.0, 30.0), "humidity": (60.0, 70.0)}

df = pd.read_csv(CSV)
df["time"] = pd.to_datetime(df["time"], utc=True).dt.floor("10min")
df = df.groupby("time").last()
df = df.reindex(pd.date_range(df.index.min(), df.index.max(), freq="10min"))
S = {k: pd.to_numeric(df[k], errors="coerce").to_numpy(dtype=float) for k in ("temperature", "humidity", "lux", "fan1", "led1")}
T, H = S["temperature"], S["humidity"]
N = len(df)
ok = ~np.isnan(T) & ~np.isnan(H)
origins = [i for i in range(72, N - 1, STEP)
           if ok[i] and ok[i - 71:i + 1].sum() >= 36 and any(i + h < N and ok[i + h] for h in HS)]
if LIMIT:
    origins = origins[:: max(1, len(origins) // LIMIT)][:LIMIT]
print("격자", N, "예측 시점", len(origins), flush=True)


def ctx(a, i):
    return a[max(0, i - CTX + 1): i + 1].astype(np.float32)


def fut(a, i):
    x = np.full(HMAX, np.nan, dtype=np.float32)
    seg = a[i + 1: i + 1 + HMAX]
    x[: len(seg)] = seg
    return x


dev = "cuda" if torch.cuda.is_available() else "cpu"
t0 = time.time()
pipe = BaseChronosPipeline.from_pretrained(MODEL, device_map=dev)
load_s = time.time() - t0
print("모델 올림 %.1fs (%s)" % (load_s, dev), flush=True)
Q = [0.1, 0.5, 0.9]


def run(inputs, label):
    t0 = time.time()
    qs, means = pipe.predict_quantiles(inputs, prediction_length=HMAX, quantile_levels=Q, batch_size=256)
    dt = time.time() - t0
    print("  %-10s %d건 %.1fs" % (label, len(inputs), dt), flush=True)
    return [q.float().cpu().numpy() for q in qs], dt     # 각 원소 (변수 수, HMAX, 3)


pred = {}
timing = {}
# 1) 따로
for name, a in (("temperature", T), ("humidity", H)):
    q, dt = run([ctx(a, i) for i in origins], "uni_" + name[:4])
    pred[("c2_uni", name)] = np.stack([x[0] for x in q])           # (origin, HMAX, 3)
    timing["c2_uni_" + name] = dt
# 2) 함께 + 과거 설비·조도
past = lambda i: {"fan1": ctx(S["fan1"], i), "led1": ctx(S["led1"], i), "lux": ctx(S["lux"], i)}
q, dt = run([{"target": np.stack([ctx(T, i), ctx(H, i)]), "past_covariates": past(i)} for i in origins], "multi")
pred[("c2_multi", "temperature")] = np.stack([x[0] for x in q]); pred[("c2_multi", "humidity")] = np.stack([x[1] for x in q])
timing["c2_multi"] = dt
# 3) + 앞으로의 배기팬·LED
q, dt = run([{"target": np.stack([ctx(T, i), ctx(H, i)]), "past_covariates": past(i),
              "future_covariates": {"fan1": fut(S["fan1"], i), "led1": fut(S["led1"], i)}} for i in origins], "future")
pred[("c2_future", "temperature")] = np.stack([x[0] for x in q]); pred[("c2_future", "humidity")] = np.stack([x[1] for x in q])
timing["c2_future"] = dt
gpu_mb = round(torch.cuda.max_memory_allocated() / 2**20) if dev == "cuda" else None
if not LIMIT:   # 예측 시점별 값 저장(날짜 단위 신뢰 구간·추가 기준선 계산용)
    np.savez(HERE / "chronos_pred.npz", origins=np.array(origins), times=df.index.astype("int64").to_numpy(),
             T=T, H=H, fan1=S["fan1"], led1=S["led1"],
             **{"%s__%s" % k: v for k, v in pred.items()})

# CPU 시간(64건, 따로 예측) — 서버(CPU)에서 돌릴 때의 감
cpu = {}
if not LIMIT and not os.environ.get("SKIP_CPU"):
    del pipe; torch.cuda.empty_cache()
    pc = BaseChronosPipeline.from_pretrained(MODEL, device_map="cpu")
    sub = [ctx(T, i) for i in origins[:64]]
    pc.predict_quantiles(sub[:4], prediction_length=HMAX, quantile_levels=Q)   # 예열
    t0 = time.time(); pc.predict_quantiles(sub, prediction_length=HMAX, quantile_levels=Q, batch_size=64); dt = time.time() - t0
    t1 = time.time(); pc.predict_quantiles(sub[:1], prediction_length=HMAX, quantile_levels=Q); d1 = time.time() - t1
    cpu = {"batch64_s": round(dt, 2), "per_series_ms": round(dt / 64 * 1000, 1), "single_ms": round(d1 * 1000, 1),
           "rss_mb": round(psutil.Process().memory_info().rss / 2**20), "threads": torch.get_num_threads()}
    print("CPU", cpu, flush=True)

# ── 채점 ──
out = {"meta": {"origins": len(origins), "grid": N, "context_steps": CTX, "device": dev, "load_s": round(load_s, 1),
                "gpu_max_mb": gpu_mb, "timing_s": {k: round(v, 2) for k, v in timing.items()}, "cpu": cpu,
                "torch": torch.__version__, "first": str(df.index[origins[0]]), "last": str(df.index[origins[-1]])},
       "mae": {}, "coverage80": {}, "alarm60": {}}
for var, a in (("temperature", T), ("humidity", H)):
    for h in HS:
        idx = [k for k, i in enumerate(origins) if i + h < N and not np.isnan(a[i + h])]
        y = np.array([a[origins[k] + h] for k in idx])
        rows = {"persist": np.array([a[origins[k]] for k in idx])}
        for m in ("c2_uni", "c2_multi", "c2_future"):
            rows[m] = pred[(m, var)][idx, h - 1, 1]
        r = {m: round(float(np.mean(np.abs(v - y))), 3) for m, v in rows.items()}
        sidx = [j for j, k in enumerate(idx) if origins[k] + h - 144 >= 0 and not np.isnan(a[origins[k] + h - 144])]
        if sidx:
            ys = y[sidx]; sv = np.array([a[origins[idx[j]] + h - 144] for j in sidx])
            r["season24"] = round(float(np.mean(np.abs(sv - ys))), 3)
            r["persist_on_season_subset"] = round(float(np.mean(np.abs(rows["persist"][sidx] - ys))), 3)
            r["c2_multi_on_season_subset"] = round(float(np.mean(np.abs(rows["c2_multi"][sidx] - ys))), 3)
            r["n_season"] = len(sidx)
        r["n"] = len(idx)
        out["mae"]["%s_%dmin" % (var, h * 10)] = r
        out["coverage80"]["%s_%dmin" % (var, h * 10)] = {
            m: round(float(np.mean((pred[(m, var)][idx, h - 1, 0] <= y) & (y <= pred[(m, var)][idx, h - 1, 2]))), 3)
            for m in ("c2_uni", "c2_multi", "c2_future")}
    # 지금 범위 안 → 60분 안에 범위를 벗어나는가
    lo, hi = RNG[var]
    res = {}
    for m in ("c2_uni", "c2_multi", "c2_future"):
        for rule in ("median", "q10_q90"):
            tp = fp = fn = tn = 0
            for k, i in enumerate(origins):
                if not (lo <= a[i] <= hi):
                    continue
                fut6 = a[i + 1: i + 7]
                fut6 = fut6[~np.isnan(fut6)]
                if len(fut6) < 3:
                    continue
                actual = bool(np.any((fut6 < lo) | (fut6 > hi)))
                p = pred[(m, var)][k, :6]
                said = bool(np.any((p[:, 1] < lo) | (p[:, 1] > hi))) if rule == "median" else bool(np.any((p[:, 0] < lo) | (p[:, 2] > hi)))
                tp += actual and said; fp += (not actual) and said; fn += actual and not said; tn += (not actual) and not said
            res["%s_%s" % (m, rule)] = {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
                                         "precision": round(tp / (tp + fp), 3) if tp + fp else None,
                                         "recall": round(tp / (tp + fn), 3) if tp + fn else None}
    out["alarm60"][var] = res

name = "chronos_smoke.json" if LIMIT else "chronos_results.json"
(HERE / name).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=1))
