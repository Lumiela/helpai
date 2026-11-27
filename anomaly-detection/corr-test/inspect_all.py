# -*- coding: utf-8 -*-
"""analyze_all.py 보완: 항목 분류 목록, 안정 구간(11-26 18:21~)에서도 변하는 항목, 상태 표시 값이 바뀐 시각, 안정 구간 범위."""
import json
import pandas as pd
o = json.load(open("all_result.json", encoding="utf-8"))
for k in ("값 하나", "99% 이상 한 값", "변함"):
    print("==", k)
    for c, v in o["classes"].items():
        if v[0] == k: print("  ", c, v[1:])
df = pd.read_csv("cube001_1min_24h_all.csv"); df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert("Asia/Seoul"); df = df.set_index("time")
st = df[df.index >= pd.Timestamp("2026-11-26 18:21", tz="Asia/Seoul")]
print("\n안정 구간에서 값이 하나뿐인 변하는 항목:", [c for c in o["coverage"] if st[c].dropna().round(6).nunique() <= 1])
for c in ("ads1115_ok", "co2_ok", "ina226_ok", "bh1750_count", "soil_sensor_ok", "applied_seq", "fan2", "fan2_stall", "lux_2", "sht45_count"):
    s = df[c].dropna(); ch = s[s.round(6) != s.round(6).shift()].iloc[1:]
    print(c, "바뀐 시각:", [(t.strftime("%m-%d %H:%M"), round(v, 3)) for t, v in ch.items()][:12])
for c in ("power_v", "power_a", "chip_temp", "rssi_dbm", "lux", "moisture_raw", "moisture_v", "co2_ppm", "temperature", "humidity", "soil_temp"):
    s = st[c].dropna(); print(c, "안정 구간 최소·중앙·최대", round(s.min(), 3), round(s.median(), 3), round(s.max(), 3), "| 분 수", len(s))
ct = df["chip_temp"].dropna(); print("chip_temp 기록 분", len(ct), "| 이웃 기록 간격(분) 빈도", ct.index.to_series().diff().dt.total_seconds().div(60).value_counts().head(5).to_dict())
