# -*- coding: utf-8 -*-
"""CUBE001 센서 간 상관 분석 — 최근 24시간(비교용으로 직전 6일).

입력: cube001_1min_7d.csv (네이버 InfluxDB, 1분 평균, UTC)
출력: corr_result.json, 화면 요약
방법
  1) 스피어만 상관: 1분 원래 값 / 10분 평균의 변화량(이웃한 10분끼리의 차)
  2) LED 켜짐·꺼짐 구간을 나눠 원래 값 상관
  3) 편상관(순위 기반): LED 설정값과 시각(하루 주기 sin·cos)의 영향을 뺀 상관
  4) 시간 지연: LED 를 켜거나 끈 시점 전후의 평균 변화(사건 기준), 1분 변화량의 교차 상관
  5) 날짜별 안정성: 같은 계산을 24시간 창 7개에 반복
  6) 같은 종류 센서(온도 3개·습도 3개) 비교
  신뢰 구간: 1시간 블록 부트스트랩 1,000번(10분 변화량 상관)
"""
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).parent
df = pd.read_csv(HERE / "cube001_1min_7d.csv")
df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert("Asia/Seoul")
df = df.set_index("time").sort_index()
df = df.reindex(pd.date_range(df.index.min().floor("min"), df.index.max().floor("min"), freq="1min", tz="Asia/Seoul"))
END = df.index.max()
MAIN = ["temperature", "humidity", "lux", "co2_ppm", "moisture_pct", "soil_temp", "power_v", "power_a", "led1", "fan1"]
LABEL = {"temperature": "온도", "humidity": "습도", "lux": "조도", "co2_ppm": "CO2", "moisture_pct": "토양수분",
         "soil_temp": "토양온도", "power_v": "전압", "power_a": "전류", "led1": "LED", "fan1": "배기팬"}
rng = np.random.default_rng(20261127)
out = {"end": str(END), "start_7d": str(df.index.min())}


def win(d, end, hours=24):
    return d[(d.index > end - pd.Timedelta(hours=hours)) & (d.index <= end)]


def spearman(a, b):
    m = a.notna() & b.notna()
    if m.sum() < 30 or a[m].nunique() < 3 or b[m].nunique() < 3:
        return None, int(m.sum())
    return float(a[m].rank().corr(b[m].rank())), int(m.sum())


def diff10(d):
    x = d.resample("10min").mean()
    return x.diff()


def block_ci(a, b, block=6, n=1000):     # 10분 단위 6칸 = 1시간 블록
    m = (a.notna() & b.notna()).to_numpy()
    ra, rb = a.to_numpy()[m], b.to_numpy()[m]
    if len(ra) < 30:
        return None
    nb = len(ra) // block
    vals = []
    for _ in range(n):
        idx = np.concatenate([np.arange(s, s + block) for s in rng.integers(0, len(ra) - block + 1, nb)])
        x, y = pd.Series(ra[idx]), pd.Series(rb[idx])
        if x.nunique() < 3 or y.nunique() < 3:
            continue
        vals.append(x.rank().corr(y.rank()))
    return [round(float(np.percentile(vals, 2.5)), 2), round(float(np.percentile(vals, 97.5)), 2)] if vals else None


def partial_rank(d, a, b, ctrl):
    cols = [a, b] + ctrl
    x = d[cols].dropna()
    if len(x) < 50 or x[a].nunique() < 3 or x[b].nunique() < 3:
        return None, len(x)
    R = x.rank()
    Z = np.column_stack([np.ones(len(R))] + [R[c].to_numpy() for c in ctrl])
    res = {}
    for c in (a, b):
        beta, *_ = np.linalg.lstsq(Z, R[c].to_numpy(), rcond=None)
        res[c] = R[c].to_numpy() - Z @ beta
    return float(np.corrcoef(res[a], res[b])[0, 1]), len(x)


d24 = win(df, END)
out["coverage_24h"] = {k: int(d24[k].notna().sum()) for k in MAIN + ["temp_1", "temp_2", "temp_3", "hum_1", "hum_2", "hum_3"]}
out["range_24h"] = {k: [round(float(d24[k].min()), 2), round(float(d24[k].max()), 2), round(float(d24[k].mean()), 2)]
                    for k in MAIN if d24[k].notna().any()}
print("24시간 창:", d24.index.min(), "~", d24.index.max(), "분 수", len(d24))
print("기록 분 수", out["coverage_24h"])
print("범위(최소, 최대, 평균)", out["range_24h"])

# 1) 원래 값·10분 변화량 상관
pairs = [(a, b) for i, a in enumerate(MAIN) for b in MAIN[i + 1:]]
dd = diff10(d24)
raw, chg = {}, {}
for a, b in pairs:
    r, n = spearman(d24[a], d24[b]); raw[f"{a}~{b}"] = (round(r, 2) if r is not None else None, n)
    r2, n2 = spearman(dd[a], dd[b]); chg[f"{a}~{b}"] = (round(r2, 2) if r2 is not None else None, n2)
out["raw"], out["change10"] = raw, chg

# 2) LED 켜짐·꺼짐 나눠서
on, off = d24[d24["led1"] > 0], d24[d24["led1"] == 0]
out["led_minutes"] = {"on": len(on), "off": len(off)}
split = {}
for a, b in pairs:
    if "led1" in (a, b):
        continue
    r1, n1 = spearman(on[a], on[b]); r0, n0 = spearman(off[a], off[b])
    split[f"{a}~{b}"] = {"led_on": (round(r1, 2) if r1 is not None else None, n1), "led_off": (round(r0, 2) if r0 is not None else None, n0)}
out["led_split"] = split

# 3) 편상관: LED·하루 주기(sin, cos) 영향 제거
hh = (d24.index.hour + d24.index.minute / 60) / 24 * 2 * np.pi
d24c = d24.assign(sin_t=np.sin(hh), cos_t=np.cos(hh))
part = {}
for a, b in pairs:
    if "led1" in (a, b):
        continue
    r, n = partial_rank(d24c, a, b, ["led1", "sin_t", "cos_t"])
    part[f"{a}~{b}"] = (round(r, 2) if r is not None else None, n)
out["partial_led_daycycle"] = part

# 신뢰 구간(10분 변화량)
KEY = [("temperature", "humidity"), ("temperature", "co2_ppm"), ("humidity", "co2_ppm"), ("temperature", "lux"),
       ("temperature", "soil_temp"), ("humidity", "moisture_pct"), ("temperature", "led1"), ("humidity", "led1"), ("co2_ppm", "led1"),
       ("temperature", "fan1"), ("humidity", "fan1"), ("power_a", "led1"), ("power_a", "fan1")]
out["change10_ci95"] = {f"{a}~{b}": block_ci(dd[a], dd[b]) for a, b in KEY}

# 4) LED 켜고 끈 시점 전후(7일 전체, 사건 기준)
led = df["led1"]
ev = []
prev = None
for t, v in led.items():
    if pd.isna(v):
        continue
    if prev is not None and (prev == 0) != (v == 0):
        ev.append((t, "on" if v > 0 else "off"))
    prev = v
# 1분 평균이라 LED 가 잠깐 깜박인 경우(30분 안에 다시 바뀜)는 제외
ev = [(t, k) for j, (t, k) in enumerate(ev) if (j + 1 == len(ev) or ev[j + 1][0] - t > pd.Timedelta(minutes=30)) and (j == 0 or t - ev[j - 1][0] > pd.Timedelta(minutes=30))]
step = {}
for var in ("temperature", "humidity", "co2_ppm", "power_a", "soil_temp"):
    for kind in ("on", "off"):
        rows = []
        for t, k in ev:
            if k != kind:
                continue
            base = df[var][t - pd.Timedelta(minutes=5): t - pd.Timedelta(minutes=1)].mean()
            r = {}
            for m in (5, 15, 30, 60, 120):
                after = df[var][t + pd.Timedelta(minutes=m - 2): t + pd.Timedelta(minutes=m + 2)].mean()
                r[m] = after - base
            if not np.isnan(base):
                rows.append(r)
        if rows:
            step[f"{var}_{kind}"] = {"n": len(rows), **{f"{m}min": round(float(np.nanmedian([r[m] for r in rows])), 3) for m in (5, 15, 30, 60, 120)}}
out["led_events"] = {"n_on": sum(k == "on" for _, k in ev), "n_off": sum(k == "off" for _, k in ev),
                     "times": [t.strftime("%m-%d %H:%M") + " " + k for t, k in ev]}
out["led_step_median_change"] = step

# 1분 변화량 교차 상관: LED 변화 → 몇 분 뒤 다른 센서 변화
xc = {}
d1 = df.diff()
for var in ("temperature", "humidity", "co2_ppm"):
    best = []
    for lag in range(0, 61):
        a, b = d1["led1"], d1[var].shift(-lag)
        m = a.notna() & b.notna()
        best.append((lag, float(np.corrcoef(a[m], b[m])[0, 1]) if m.sum() > 100 and a[m].std() > 0 else np.nan))
    lag, r = max(best, key=lambda x: abs(x[1]) if not np.isnan(x[1]) else -1)
    xc[var] = {"best_lag_min": lag, "r": round(r, 3), "r_lag0": round(best[0][1], 3)}
out["led_crosscorr_1min_diff_7d"] = xc

# 5) 날짜별 안정성: 24시간 창 7개(끝 시각을 24시간씩 앞으로)
stab = {}
for a, b in KEY[:6]:
    rr, cc = [], []
    for k in range(7):
        w = win(df, END - pd.Timedelta(hours=24 * k))
        r, n = spearman(w[a], w[b]); rr.append(round(r, 2) if r is not None and n > 300 else None)
        w10 = diff10(w); r2, n2 = spearman(w10[a], w10[b]); cc.append(round(r2, 2) if r2 is not None and n2 > 30 else None)
    stab[f"{a}~{b}"] = {"raw_by_window_newest_first": rr, "change10_by_window": cc}
out["stability_7windows"] = stab

# 6) 같은 종류 센서끼리
red = {}
for grp in (("temp_1", "temp_2", "temp_3"), ("hum_1", "hum_2", "hum_3")):
    x = d24[list(grp)].dropna()
    med = x.median(axis=1)
    for c in grp:
        dev = x[c] - med
        red[c] = {"mean_minus_median": round(float(dev.mean()), 2), "sd": round(float(dev.std()), 2),
                  "max_abs": round(float(dev.abs().max()), 2), "minutes": len(x)}
    for i, a in enumerate(grp):
        for b in grp[i + 1:]:
            r, n = spearman(diff10(d24[[a]])[a], diff10(d24[[b]])[b]); red[f"{a}~{b}_change10"] = round(r, 3) if r is not None else None
out["redundancy_24h"] = red
# 7일 동안 하루 단위 오프셋 변화(센서가 서서히 벌어지는지)
drift = {}
for grp in (("temp_1", "temp_2", "temp_3"), ("hum_1", "hum_2", "hum_3")):
    x = df[list(grp)].dropna()
    dm = x.sub(x.median(axis=1), axis=0).groupby(x.index.date).mean().round(2)
    drift["/".join(grp)] = {str(k): v.tolist() for k, v in dm.iterrows()}
out["redundancy_daily_offset_7d"] = drift
# 전류: 7일 중 크기가 바뀐 때가 있는지(11-26 션트 R002→R010 반영 펌웨어)
pa = df["power_a"].dropna()
out["power_a_daily_median"] = {str(k): round(float(v), 3) for k, v in pa.groupby(pa.index.date).median().items()}

(HERE / "corr_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def show(title, dct):
    print("\n== " + title)
    for k, v in dct.items():
        a, b = k.split("~")
        print("  %s~%s %s" % (LABEL.get(a, a), LABEL.get(b, b), v))


show("원래 값 스피어만 (상관, 분 수)", raw)
show("10분 변화량 스피어만 (상관, 칸 수)", chg)
show("10분 변화량 95% 구간(1시간 블록)", out["change10_ci95"])
show("LED 켜짐/꺼짐 나눠서", split)
show("편상관(LED·하루 주기 제거)", part)
print("\nLED 사건", out["led_events"])
print("LED 사건 뒤 변화(중앙값)", json.dumps(step, ensure_ascii=False))
print("LED 1분 변화 교차 상관", xc)
print("\n날짜별 안정성", json.dumps(stab, ensure_ascii=False))
print("\n같은 종류 센서", json.dumps(red, ensure_ascii=False))
print("하루 단위 오프셋", json.dumps(drift, ensure_ascii=False))
print("전류 하루 중앙값", out["power_a_daily_median"])
