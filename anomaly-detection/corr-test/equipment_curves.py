# -*- coding: utf-8 -*-
"""equipment_response.py 보완: (1) LED 켜기·끄기 뒤 1분 단위 평균 반응 곡선과 63 % 도달 시간 (2) 펌프 급수 뒤 토양수분 원시값 변화."""
import json
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).parent
R = json.loads((HERE / "equipment_response.json").read_text(encoding="utf-8"))
df = pd.read_csv(HERE / "cube001_1min_all_period.csv")
df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert("Asia/Seoul").dt.floor("min")
df = df.groupby("time").mean()
df = df.reindex(pd.date_range(df.index.min(), df.index.max(), freq="1min", tz="Asia/Seoul"))
pos = {t: i for i, t in enumerate(df.index)}
out = {}

for kind in ("on", "off"):
    evs = [e for e in R["events_all"] if e["eq"] == "led1" and e["kind"] == kind and e["isolated"]]
    for k in ("temperature", "humidity", "chip_temp", "moisture_raw"):
        x = df[k].to_numpy(); curves = []
        for e in evs:
            i = pos[pd.Timestamp(e["t"], tz="Asia/Seoul")]
            base = x[i - 5:i]
            seg = x[i:i + 61]
            if (~np.isnan(base)).sum() >= 3 and (~np.isnan(seg)).sum() >= 50:
                curves.append(seg - np.nanmean(base))
        if not curves:
            continue
        C = np.nanmedian(np.vstack(curves), axis=0)
        final = np.nanmean(C[55:61])
        t63 = next((m for m in range(61) if not np.isnan(C[m]) and abs(C[m]) >= 0.63 * abs(final)), None)
        out[f"led_{kind}_{k}"] = {"events": len(curves), "median_curve_min_0_10_20_30_45_60": [round(float(C[m]), 3) for m in (0, 1, 2, 3, 5, 10, 20, 30, 45, 60)],
                                  "final_55_60": round(float(final), 3), "minutes_to_63pct": t63}

# 펌프: 1분 평균 pump > 0 인 분을 급수로 보고, 앞 30분에 급수가 없던 첫 분만
p = df["pump"].fillna(0).to_numpy() > 0
mr = df["moisture_raw"].to_numpy()
pulses = []
for i in range(30, len(df) - 61):
    if p[i] and not p[i - 30:i].any():
        base = mr[i - 5:i]; a = {}
        for h in (5, 15, 30, 60):
            aft = mr[i + h - 2:i + h + 3]
            a[h] = round(float(np.nanmean(aft) - np.nanmean(base)), 2) if (~np.isnan(base)).sum() >= 3 and (~np.isnan(aft)).sum() >= 3 else None
        lvl = round(float(np.nanmean(base)), 1) if (~np.isnan(base)).sum() else None
        pulses.append({"t": df.index[i].strftime("%m-%d %H:%M"), "pump_minutes_next_10": int(p[i:i + 10].sum()), "moisture_raw_before": lvl, "delta": a})
out["pump_pulses"] = pulses
(HERE / "equipment_curves.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
for k, v in out.items():
    if k != "pump_pulses":
        print(k, v)
print("펌프 급수", len(pulses))
for x in pulses: print("  ", x)
