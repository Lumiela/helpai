# -*- coding: utf-8 -*-
"""
조명을 CUBE002 → CUBE001 로 다시 옮긴 날 찾기 — InfluxDB 1분 CSV 에서 날마다 LED·조도 상관을 본다.

원리: 조명이 CUBE001 에 붙어 있으면 CUBE001.led1 이 바뀔 때 CUBE001.lux 가 같이 움직인다
(autoctl 이 23:00 에 LED 를 끄고 01:00 에 50 % 로 켜고, 주간에는 ±10 % 씩 움직이므로 하루에도 여러 번).
조명이 다른 큐브에 있으면 CUBE001.led1 을 바꿔도 CUBE001.lux 는 따라오지 않는다(상관 ≈ 0).

  날마다:  분 수 · led1 이 움직였는지(표준편차) · corr(led1, lux) · 다른 큐브 led1 데이터 분 수
  제안:    그날부터 끝까지 '판단 가능한 날'이 모두 상관 ≥ 문턱인 첫날

입력 CSV 는 export-influx --zone-config configs/light_check.yaml 로 받는다(CUBE001.led1·lux + CUBE002.led1).
표준 라이브러리만 쓴다.
"""
import math
from collections import defaultdict
from datetime import timedelta, timezone

from .replay import read_wide_csv

KST = timezone(timedelta(hours=9))
MIN_ROWS = 60           # 하루에 이만큼 분 데이터가 있어야 판단
MIN_LED_STD = 5.0       # led1 이 이만큼은 움직여야 상관을 볼 수 있다


def _corr(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def daily_report(rows, led_col="CUBE001.led1", lux_col="CUBE001.lux", other_led_col="CUBE002.led1"):
    """rows: (utc datetime, {열: 값}). 날짜(KST) 순 목록."""
    days = defaultdict(lambda: {"pairs": [], "other": 0})
    for t, v in rows:
        d = t.astimezone(KST).date().isoformat()
        led, lux = v.get(led_col), v.get(lux_col)
        if led is not None and lux is not None:
            days[d]["pairs"].append((led, lux))
        if other_led_col and v.get(other_led_col) is not None:
            days[d]["other"] += 1
    out = []
    for d in sorted(days):
        pairs = days[d]["pairs"]
        leds = [p[0] for p in pairs]
        n = len(pairs)
        std = math.sqrt(sum((x - sum(leds) / n) ** 2 for x in leds) / n) if n else 0.0
        r = _corr(leds, [p[1] for p in pairs]) if n >= MIN_ROWS and std >= MIN_LED_STD else None
        out.append({"date": d, "minutes": n, "led_std": round(std, 1),
                    "corr": None if r is None else round(r, 3), "other_led_minutes": days[d]["other"]})
    return out


def suggest(report, threshold=0.6):
    """'판단 가능한 날'(corr 있음) 중, 그날부터 끝까지 모두 corr ≥ threshold 인 첫날. 없으면 None."""
    judged = [r for r in report if r["corr"] is not None]
    for i, r in enumerate(judged):
        if all(x["corr"] >= threshold for x in judged[i:]):
            return r["date"]
    return None


def run(csv_path, threshold=0.6, **cols):
    rep = daily_report(read_wide_csv(csv_path), **cols)
    return rep, suggest(rep, threshold)
