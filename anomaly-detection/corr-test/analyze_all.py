# -*- coding: utf-8 -*-
"""CUBE001 이 보낸 모든 항목끼리의 상관(§60 과 같은 24시간 창: 2026-11-26 09:24 ~ 11-27 09:23 KST).

입력: cube001_1min_24h_all.csv (네이버 InfluxDB, 1분 평균, 참/거짓은 1/0)
1) 항목 분류: 24시간 동안 값이 하나뿐 / 99 % 이상 한 값 / 변하는 항목
2) 변하는 항목 전체의 스피어만 상관 행렬: 1분 원래 값, 10분 평균 변화량
3) |원래 값 상관| ≥ 0.5 또는 |변화량 상관| ≥ 0.3 인 짝 목록 + 변화량 95 % 구간(1시간 블록 부트스트랩 1,000번)
4) 안정 구간(11-26 18:21 ~ 11-27 09:23, 마지막 설비 조작 뒤)에서 같은 짝을 다시 계산
5) |원래 값 상관| ≥ 0.7 로 이어지는 묶음
전류(power_a)는 크기가 바뀐 11-26 11:09 이전 값을 뺐다(§60.2).
"""
import json
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).parent
df = pd.read_csv(HERE / "cube001_1min_24h_all.csv")
df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert("Asia/Seoul")
df = df.set_index("time").sort_index()
df = df.reindex(pd.date_range("2026-11-26 09:24", "2026-11-27 09:23", freq="1min", tz="Asia/Seoul"))
df.loc[df.index < pd.Timestamp("2026-11-26 11:09", tz="Asia/Seoul"), "power_a"] = np.nan
STEADY = (pd.Timestamp("2026-11-26 18:21", tz="Asia/Seoul"), pd.Timestamp("2026-11-27 09:23", tz="Asia/Seoul"))
rng = np.random.default_rng(20261127)
out = {"window": [str(df.index.min()), str(df.index.max())], "steady": [str(s) for s in STEADY]}

# 1) 분류
cls = {}
for c in df.columns:
    s = df[c].dropna()
    if len(s) == 0:
        cls[c] = ("기록 없음", 0, None); continue
    top = s.round(6).value_counts()
    share = float(top.iloc[0] / len(s))
    if len(top) == 1:
        cls[c] = ("값 하나", len(s), float(top.index[0]))
    elif share >= 0.99:
        cls[c] = ("99% 이상 한 값", len(s), [float(top.index[0]), round(share, 4), int(len(top))])
    else:
        cls[c] = ("변함", len(s), [round(float(s.min()), 3), round(float(s.max()), 3), int(len(top))])
out["classes"] = cls
var = [c for c, v in cls.items() if v[0] == "변함"]
print("변하는 항목", len(var), var)
print("값 하나", [c for c, v in cls.items() if v[0] == "값 하나"])
print("99% 이상 한 값", {c: v[2] for c, v in cls.items() if v[0] == "99% 이상 한 값"})


def rank_corr_matrix(d, min_n, min_uniq):
    cols = list(d.columns); n = len(cols)
    M = pd.DataFrame(np.nan, index=cols, columns=cols); N = pd.DataFrame(0, index=cols, columns=cols)
    for i in range(n):
        for j in range(i, n):
            a, b = d[cols[i]], d[cols[j]]
            m = a.notna() & b.notna()
            N.iloc[i, j] = N.iloc[j, i] = int(m.sum())
            if m.sum() >= min_n and a[m].nunique() >= min_uniq and b[m].nunique() >= min_uniq:
                r = a[m].rank().corr(b[m].rank())
                M.iloc[i, j] = M.iloc[j, i] = r
    return M, N


def block_ci(a, b, block=6, n=1000):
    m = (a.notna() & b.notna()).to_numpy()
    ra, rb = a.to_numpy()[m], b.to_numpy()[m]
    if len(ra) < 30:
        return None
    nb = len(ra) // block; vals = []
    for _ in range(n):
        idx = np.concatenate([np.arange(s, s + block) for s in rng.integers(0, len(ra) - block + 1, nb)])
        x, y = pd.Series(ra[idx]), pd.Series(rb[idx])
        if x.nunique() >= 3 and y.nunique() >= 3:
            vals.append(x.rank().corr(y.rank()))
    return [round(float(np.percentile(vals, 2.5)), 2), round(float(np.percentile(vals, 97.5)), 2)] if len(vals) > 100 else None


D = df[var]
raw, rawN = rank_corr_matrix(D, 60, 3)
chg_d = D.resample("10min").mean().diff()
chg, chgN = rank_corr_matrix(chg_d, 30, 3)
st = D[(D.index >= STEADY[0]) & (D.index <= STEADY[1])]
st_raw, _ = rank_corr_matrix(st, 60, 3)
st_chg, _ = rank_corr_matrix(st.resample("10min").mean().diff(), 30, 3)
raw.round(3).to_csv(HERE / "all_raw_spearman.csv", encoding="utf-8-sig")
chg.round(3).to_csv(HERE / "all_change10_spearman.csv", encoding="utf-8-sig")
st_raw.round(3).to_csv(HERE / "steady_raw_spearman.csv", encoding="utf-8-sig")
st_chg.round(3).to_csv(HERE / "steady_change10_spearman.csv", encoding="utf-8-sig")
rawN.to_csv(HERE / "all_raw_n.csv", encoding="utf-8-sig")

# 같은 측정에서 계산된 짝(서로 같은 값이라 상관이 당연한 것)
SAME = [{"temperature", "temp_1", "temp_2", "temp_3"}, {"humidity", "hum_1", "hum_2", "hum_3"}, {"lux", "lux_1", "lux_2"},
        {"moisture_pct", "moisture_raw", "moisture_v"}, {"fan1", "fan1_rpm", "fan1_rpm2", "fan1_stall"},
        {"fan2", "fan2_rpm", "fan2_rpm2", "fan2_stall"}, {"motor_set", "motor_duty", "pump"}]


def same_source(a, b):
    return any(a in g and b in g for g in SAME)


pairs = []
for i, a in enumerate(var):
    for b in var[i + 1:]:
        r, c = raw.loc[a, b], chg.loc[a, b]
        if (not np.isnan(r) and abs(r) >= 0.5) or (not np.isnan(c) and abs(c) >= 0.3):
            pairs.append({"a": a, "b": b, "raw": None if np.isnan(r) else round(float(r), 2), "n": int(rawN.loc[a, b]),
                          "change10": None if np.isnan(c) else round(float(c), 2), "n10": int(chgN.loc[a, b]),
                          "ci95": block_ci(chg_d[a], chg_d[b]) if not np.isnan(c) else None,
                          "steady_raw": None if np.isnan(st_raw.loc[a, b]) else round(float(st_raw.loc[a, b]), 2),
                          "steady_change10": None if np.isnan(st_chg.loc[a, b]) else round(float(st_chg.loc[a, b]), 2),
                          "same_source": same_source(a, b)})
pairs.sort(key=lambda p: -abs(p["raw"] or 0))
out["pairs"] = pairs

# 묶음: |원래 값 상관| >= 0.7 로 이어지는 항목
adj = {c: set() for c in var}
for a in var:
    for b in var:
        if a != b and not np.isnan(raw.loc[a, b]) and abs(raw.loc[a, b]) >= 0.7:
            adj[a].add(b)
seen, groups = set(), []
for c in var:
    if c in seen or not adj[c]:
        continue
    stack, g = [c], set()
    while stack:
        x = stack.pop()
        if x in g: continue
        g.add(x); stack.extend(adj[x] - g)
    seen |= g; groups.append(sorted(g))
out["groups_raw_0_7"] = groups

# 장치 재시작: uptime 이 줄어든 시각
up = df["uptime_ms"].dropna()
out["restarts"] = [t.strftime("%m-%d %H:%M") for t, d in up.diff().items() if d < 0]
out["coverage"] = {c: int(df[c].notna().sum()) for c in var}
(HERE / "all_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

print("\n장치 재시작(uptime 감소)", out["restarts"])
print("기록 분 수", out["coverage"])
print("\n묶음(|원래 값| >= 0.7)", groups)
print("\n짝 목록: 원래 값 | 10분 변화량 [95% 구간] | 안정 구간 원래 값 · 변화량 | 같은 측정")
for p in pairs:
    print("  %-14s %-14s %6s (n=%4d) | %6s %-14s (n=%3d) | %6s · %6s %s" % (
        p["a"], p["b"], p["raw"], p["n"], p["change10"], p["ci95"], p["n10"], p["steady_raw"], p["steady_change10"], "같은측정" if p["same_source"] else ""))
