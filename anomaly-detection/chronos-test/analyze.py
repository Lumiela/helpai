# -*- coding: utf-8 -*-
"""chronos_pred.npz 로 (1) 하루 주기 기준선 (2) 날짜 단위 부트스트랩 신뢰 구간 (3) 이탈 시각 분포를 본다."""
import json
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).parent
Z = np.load(HERE / "chronos_pred.npz")
import sys
_df = pd.read_csv(sys.argv[1])            # run_chronos.py 와 같은 방법으로 10분 격자를 다시 만든다
_t = pd.to_datetime(_df["time"], utc=True).dt.floor("10min")
times = pd.date_range(_t.min(), _t.max(), freq="10min").tz_convert("Asia/Seoul")
O = Z["origins"]
assert len(times) == len(Z["T"]), (len(times), len(Z["T"]))
_chk = _df.assign(time=_t).groupby("time").last().reindex(times.tz_convert("UTC"))["temperature"].to_numpy(dtype=float)
assert np.allclose(np.nan_to_num(_chk, nan=-999), np.nan_to_num(Z["T"], nan=-999)), "격자 불일치"
A = {"temperature": Z["T"], "humidity": Z["H"]}
N = len(times); rng = np.random.default_rng(20261126)
day = np.array([times[i].date() for i in O])
days = np.unique(day)
print("예측 시점", len(O), "날짜 수", len(days))
res = {}
for var, a in A.items():
    for h in (3, 6, 18):
        k = np.array([j for j, i in enumerate(O) if i + h < N and not np.isnan(a[i + h])])
        i = O[k]; y = a[i + h]
        e = {"persist": np.abs(a[i] - y)}
        # 하루 주기 기준선: 지금 값 + 24시간 전 같은 구간의 변화량
        d = a[i + h - 144] - a[i - 144]
        e["persist_plus_24h_change"] = np.abs(a[i] + d - y)
        for m in ("c2_uni", "c2_multi", "c2_future"):
            e[m] = np.abs(Z["%s__%s" % (m, var)][k, h - 1, 1] - y)
        okd = ~np.isnan(e["persist_plus_24h_change"])
        row = {"n": int(len(k)), "n_24h": int(okd.sum())}
        for m, v in e.items():
            row[m] = round(float(np.nanmean(v)), 3)
        row["on_24h_subset"] = {m: round(float(np.mean(v[okd])), 3) for m, v in e.items()}
        # 날짜 단위 부트스트랩: MAE 비율(모델 / 지금 값 유지)
        dk = day[k]
        for m in ("c2_uni", "c2_multi", "c2_future"):
            rat = []
            for _ in range(2000):
                pick = rng.choice(days, size=len(days), replace=True)
                sel = np.concatenate([np.where(dk == dd)[0] for dd in pick])
                rat.append(e[m][sel].mean() / e["persist"][sel].mean())
            lo, hi = np.percentile(rat, [2.5, 97.5])
            win = np.mean([e[m][dk == dd].mean() < e["persist"][dk == dd].mean() for dd in np.unique(dk)])
            row[m + "_ratio_ci95"] = [round(float(lo), 3), round(float(hi), 3)]
            row[m + "_days_better"] = "%d/%d" % (round(win * len(np.unique(dk))), len(np.unique(dk)))
        res["%s_%dmin" % (var, h * 10)] = row
        print("%-18s" % ("%s_%dmin" % (var, h * 10)), json.dumps(row, ensure_ascii=False))

# 실제 이탈(지금 범위 안 → 60분 안에 벗어남)이 언제 일어났나
RNG = {"temperature": (22.0, 30.0), "humidity": (60.0, 70.0)}
for var, a in A.items():
    lo, hi = RNG[var]; hours = []; side = {"low": 0, "high": 0}
    for i in O:
        if not (lo <= a[i] <= hi):
            continue
        f = a[i + 1:i + 7]; f = f[~np.isnan(f)]
        if len(f) >= 3 and np.any((f < lo) | (f > hi)):
            hours.append(times[i].hour); side["low"] += bool(np.any(f < lo)); side["high"] += bool(np.any(f > hi))
    hc = pd.Series(hours).value_counts().sort_index()
    print(var, "이탈", len(hours), side, "시각(KST)별", hc.to_dict())
# LED 가 꺼지는 시각
led = Z["led1"]; off = [times[j].strftime("%H:%M") for j in range(1, N) if led[j - 1] > 0 and led[j] == 0]
on = [times[j].strftime("%H:%M") for j in range(1, N) if led[j - 1] == 0 and led[j] > 0]
print("LED 꺼짐 시각", pd.Series(off).value_counts().head(6).to_dict(), "켜짐", pd.Series(on).value_counts().head(6).to_dict())
fan = Z["fan1"]; ch = [times[j].strftime("%m-%d %H:%M") + " %g→%g" % (fan[j - 1], fan[j]) for j in range(1, N)
                      if not np.isnan(fan[j]) and not np.isnan(fan[j - 1]) and fan[j] != fan[j - 1]]
print("배기팬 변경", len(ch), ch[:30])
(HERE / "analyze.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
