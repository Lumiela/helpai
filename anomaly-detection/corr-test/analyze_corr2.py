# -*- coding: utf-8 -*-
"""analyze_corr.py 보완: 날짜별 기록량, 전류 크기가 바뀐 시각, 바뀐 뒤만으로 전압·전류 상관, LED·배기팬 상태, CO2 분포."""
import json
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).parent
df = pd.read_csv(HERE / "cube001_1min_7d.csv")
df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert("Asia/Seoul")
df = df.set_index("time").sort_index()
END = df.index.max()
d24 = df[df.index > END - pd.Timedelta(hours=24)]
out = {}

out["minutes_per_day"] = {str(k): {c: int(v[c].notna().sum()) for c in ("temperature", "co2_ppm", "led1", "power_a")}
                          for k, v in df.groupby(df.index.date)}
pa = df["power_a"].dropna()
big = pa[pa > 12]; small = pa[pa <= 12]
out["power_a_last_large"] = str(big.index.max()) if len(big) else None
out["power_a_first_small_after"] = str(small[small.index > big.index.max()].index.min()) if len(big) else None
cut = pd.Timestamp(out["power_a_first_small_after"]) if len(big) else None
post = d24[d24.index >= cut] if cut is not None else d24
out["post_change_window"] = [str(post.index.min()), str(post.index.max()), int(post["power_a"].notna().sum())]


def sp(a, b):
    m = a.notna() & b.notna()
    return (round(float(a[m].rank().corr(b[m].rank())), 2), int(m.sum())) if m.sum() >= 30 and a[m].nunique() > 2 and b[m].nunique() > 2 else (None, int(m.sum()))


p10 = post.resample("10min").mean().diff()
res = {}
for a, b in (("power_v", "power_a"), ("power_a", "temperature"), ("power_a", "humidity"), ("power_v", "temperature"),
             ("power_v", "humidity"), ("power_a", "lux"), ("power_v", "lux"), ("power_a", "chip_temp")):
    res[f"{a}~{b}"] = {"raw": sp(post[a], post[b]), "change10": sp(p10[a], p10[b])}
out["post_change_corr"] = res
out["post_change_range"] = {c: [round(float(post[c].min()), 2), round(float(post[c].max()), 2), round(float(post[c].median()), 2)]
                            for c in ("power_v", "power_a") if post[c].notna().any()}
# 전압이 낮았던 때
low = post[post["power_v"] < 11.5]
out["power_v_below_11_5"] = {"minutes": int(len(low)), "times": [t.strftime("%m-%d %H:%M") for t in low.index[:12]],
                             "power_a_median_then": round(float(low["power_a"].median()), 2) if len(low) else None,
                             "power_a_median_otherwise": round(float(post[post["power_v"] >= 11.5]["power_a"].median()), 2)}
# LED·배기팬 상태(24시간)
out["led1_values_24h"] = {str(k): int(v) for k, v in d24["led1"].round().value_counts().items()}
out["fan1_values_24h"] = {str(k): int(v) for k, v in d24["fan1"].round().value_counts().items()}
out["fan1_on_times_24h"] = [t.strftime("%m-%d %H:%M") for t in d24.index[d24["fan1"] > 0][:20]]
out["led1_below100_times_24h"] = [t.strftime("%m-%d %H:%M") + " %.0f" % v for t, v in d24["led1"][d24["led1"] < 99].items()][:20]
# CO2 분포
c = d24["co2_ppm"].dropna()
out["co2_24h"] = {"minutes": len(c), "share_at_min": round(float((c <= c.min() + 1).mean()), 3), "min": float(c.min()),
                  "p50": float(c.median()), "p90": float(c.quantile(.9)), "max": float(c.max())}
# 시각별 평균(24시간): 무엇이 하루 동안 움직였나
h = d24[["temperature", "humidity", "lux", "co2_ppm", "power_v", "soil_temp"]].groupby(d24.index.hour).mean().round(2)
out["hourly_mean_24h"] = {int(k): v.tolist() for k, v in h.iterrows()}
# 기록이 빈 시간대(24시간, 10분 이상 연속)
t = d24["temperature"].dropna().index
gaps = [(a, b) for a, b in zip(t[:-1], t[1:]) if b - a > pd.Timedelta(minutes=10)]
out["temperature_gaps_24h"] = [(a.strftime("%m-%d %H:%M"), b.strftime("%m-%d %H:%M"), int((b - a).total_seconds() // 60)) for a, b in gaps]
(HERE / "corr_result2.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=1))
