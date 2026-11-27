# -*- coding: utf-8 -*-
"""설비 기준 분석: 설비 값이 바뀐 시점 전후로 센서가 어떻게 변했나 (CUBE001, 2026-09-23 ~ 11-27, 1분 평균).

사건 = 설비 값(반올림)이 바뀌고, 바뀌기 전 10분·바뀐 뒤 10분 동안 값이 그대로인 시점.
격리 = 사건 앞 10분 ~ 뒤 60분 안에 다른 설비 사건·값 흔들림이 없고, 앞뒤 5분 안에 장치 재시작(uptime 감소)이 없음.
반응 = (사건 뒤 h분 전후 5분 평균) − (사건 직전 5분 평균), h = 5·15·30·60.
대조 = 앞뒤 60분 안에 설비 변화·재시작이 없는 시각 중 사건과 같은 시간대(사건 시각 ±1시간)에서 같은 계산.
격리 판정에는 같은 분의 다른 설비 변경도 넣고, 펌프·모터는 1분 평균이 0 보다 크면 움직인 것으로 본다(2차 수정).
전류(power_a)는 11-26 11:09 이후 값만 쓴다(션트 R010 판).
"""
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).parent
df = pd.read_csv(HERE / "cube001_1min_all_period.csv")
df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert("Asia/Seoul").dt.floor("min")
df = df.groupby("time").mean()
df = df.reindex(pd.date_range(df.index.min(), df.index.max(), freq="1min", tz="Asia/Seoul"))
df.loc[df.index < pd.Timestamp("2026-11-26 11:09", tz="Asia/Seoul"), ["power_a", "power_v"]] = np.nan
EQ = ["led1", "fan1", "fan2", "pump", "motor_set"]
SEN = ["temperature", "humidity", "co2_ppm", "lux", "moisture_raw", "soil_temp", "chip_temp", "power_a", "power_v"]
LAB = {"temperature": "기온", "humidity": "습도", "co2_ppm": "CO2", "lux": "조도", "moisture_raw": "토양수분 원시값",
       "soil_temp": "7종 센서 온도(공기)", "chip_temp": "칩 온도", "power_a": "전류", "power_v": "전압"}
HS = (5, 15, 30, 60)
N = len(df)
idx = {t: i for i, t in enumerate(df.index)}
up = df["uptime_ms"].to_numpy()
restart = np.zeros(N, bool)
last = np.nan
for i in range(N):
    if not np.isnan(up[i]):
        if not np.isnan(last) and up[i] < last:
            restart[i] = True
        last = up[i]
out = {"period": [str(df.index.min()), str(df.index.max())], "minutes": N, "restarts": int(restart.sum())}

# 설비 상태(반올림)와 사건
state = {e: df[e].round().to_numpy() for e in EQ}
events = []
unstable = np.zeros(N, bool)          # 어떤 설비든 값이 바뀐 분(사건이든 흔들림이든)
chg_by = {}
for e in EQ:
    s = state[e]; u = np.zeros(N, bool)
    for i in range(1, N):
        if not np.isnan(s[i]) and not np.isnan(s[i - 1]) and s[i] != s[i - 1]:
            u[i] = True
    chg_by[e] = u; unstable |= u
# 펌프·모터는 몇 초만 켜질 수 있어 1분 평균이 0 보다 크면 '움직임'으로 본다(반올림하면 0 이 됨)
for e in ("pump", "motor_set"):
    moved = (df[e].fillna(0).to_numpy() > 0)
    chg_by[e] = chg_by[e] | moved; unstable |= moved
for e in EQ:
    s = state[e]
    for i in range(11, N - 11):
        a, b = s[i - 1], s[i]
        if np.isnan(a) or np.isnan(b) or a == b:
            continue
        before, after = s[i - 10:i], s[i + 1:i + 11]
        # 1분 평균이라 바뀐 분은 중간값일 수 있음 → 바로 다음 분부터 새 값이 10분 유지되면 그 값을 새 상태로
        new = after[0]
        if np.isnan(new) or not np.all(after == new) or not np.all(before == a) or new == a:
            continue
        kind = (e, "on" if a == 0 and new > 0 else "off" if a > 0 and new == 0 else "up" if new > a else "down")
        events.append({"eq": e, "kind": kind[1], "i": i, "t": df.index[i], "from": float(a), "to": float(new)})
# 같은 변화가 1~2분에 걸쳐 두 번 잡힌 것 제거(중간값 → 새 값)
ev2 = []
for ev in sorted(events, key=lambda x: x["i"]):
    if ev2 and ev2[-1]["eq"] == ev["eq"] and ev["i"] - ev2[-1]["i"] <= 2:
        continue
    ev2.append(ev)
events = ev2
for ev in events:
    i = ev["i"]
    lo, hi = max(0, i - 10), min(N, i + 61)
    others = [j for o in EQ if o != ev["eq"] for j in range(lo, hi) if chg_by[o][j]]
    own = [j for j in range(lo, hi) if chg_by[ev["eq"]][j] and abs(j - i) > 1]
    others += own
    ev["isolated"] = (not others) and (not restart[max(0, i - 5):i + 6].any())
    ev["restart_near"] = bool(restart[max(0, i - 5):i + 6].any())

S = {k: df[k].to_numpy() for k in SEN}


def resp(i, k, h):
    x = S[k]
    base = x[max(0, i - 5):i]
    aft = x[i + h - 2:i + h + 3]
    if np.isnan(base).all() or np.isnan(aft).all() or (~np.isnan(base)).sum() < 3 or (~np.isnan(aft)).sum() < 3:
        return np.nan
    return float(np.nanmean(aft) - np.nanmean(base))


# 대조: 앞뒤 60분 안에 설비 변화·재시작이 없는 시각(30분 간격으로 뽑음)
bad = unstable | restart
quiet = np.convolve(bad.astype(int), np.ones(121, int), mode="same") == 0
ctrl_i = [i for i in range(70, N - 70, 30) if quiet[i]]
ctrl = {k: {h: np.array([v for v in (resp(i, k, h) for i in ctrl_i) if not np.isnan(v)]) for h in HS} for k in SEN}
out["control_points"] = len(ctrl_i)
out["pump_minutes_gt0"] = int((df["pump"].fillna(0) > 0).sum()); out["motor_minutes_gt0"] = int((df["motor_set"].fillna(0) > 0).sum())
out["pump_times"] = [x.strftime("%m-%d %H:%M") for x in df.index[(df["pump"].fillna(0) > 0).to_numpy()]][:30]

summary = {}
for key in sorted({(e["eq"], e["kind"]) for e in events}):
    evs = [e for e in events if (e["eq"], e["kind"]) == key]
    iso = [e for e in evs if e["isolated"]]
    row = {"all": len(evs), "isolated": len(iso), "restart_near": sum(e["restart_near"] for e in evs),
           "times_isolated": [e["t"].strftime("%m-%d %H:%M") + " %g→%g" % (e["from"], e["to"]) for e in iso][:40]}
    for k in SEN:
        r = {}
        for h in HS:
            v = np.array([x for x in (resp(e["i"], k, h) for e in iso) if not np.isnan(x)])
            if len(v) == 0:
                continue
            hours = {(e["t"].hour + d) % 24 for e in iso for d in (-1, 0, 1)}
            c = np.array([x for x in (resp(j, k, h) for j in ctrl_i if df.index[j].hour in hours) if not np.isnan(x)])
            med = float(np.median(v))
            # 대조 구간에서 |변화| 가 사건 중앙값의 |변화| 이상이었던 비율(작을수록 사건 때 변화가 드묾)
            share = float(np.mean(np.abs(c) >= abs(med))) if len(c) else None
            same_sign = float(np.mean(np.sign(v) == np.sign(med))) if med != 0 else None
            r[h] = {"n": int(len(v)), "median": round(med, 3), "q25": round(float(np.percentile(v, 25)), 3),
                    "q75": round(float(np.percentile(v, 75)), 3), "same_sign": None if same_sign is None else round(same_sign, 2),
                    "ctrl_median": round(float(np.median(c)), 3) if len(c) else None,
                    "ctrl_abs_p90": round(float(np.percentile(np.abs(c), 90)), 3) if len(c) else None,
                    "ctrl_share_ge": None if share is None else round(share, 3), "ctrl_n": int(len(c))}
        if r:
            row[k] = r
    summary["%s_%s" % key] = row
out["events_summary"] = summary
out["event_counts"] = {"%s_%s" % k: v for k, v in pd.Series([(e["eq"], e["kind"]) for e in events]).value_counts().items()}
out["events_all"] = [{"eq": e["eq"], "kind": e["kind"], "t": e["t"].strftime("%Y-%m-%d %H:%M"), "from": e["from"], "to": e["to"],
                      "isolated": e["isolated"], "restart_near": e["restart_near"]} for e in events]
(HERE / "equipment_response.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

print("기간", out["period"], "분", N, "재시작", out["restarts"], "대조 시각", len(ctrl_i), "| 펌프>0 분", out["pump_minutes_gt0"], out["pump_times"][:12], "| 모터>0 분", out["motor_minutes_gt0"])
print("사건 수(전체)", out["event_counts"])
for key, row in summary.items():
    print("\n== %s: 전체 %d, 격리 %d, 재시작 근처 %d" % (key, row["all"], row["isolated"], row["restart_near"]))
    print("   격리 사건:", row["times_isolated"][:12], "..." if len(row["times_isolated"]) > 12 else "")
    for k in SEN:
        if k in row:
            print("   %-12s" % LAB[k], " | ".join("%d분 %+.3f (n=%d, 같은방향 %s, 대조 중앙 %+.3f, 대조 |Δ|≥ 비율 %s)" % (
                h, v["median"], v["n"], v["same_sign"], v["ctrl_median"] or 0, v["ctrl_share_ge"]) for h, v in row[k].items()))
