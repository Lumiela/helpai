# -*- coding: utf-8 -*-
import json
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
R = json.loads((HERE / __import__("sys").argv[1]).read_text(encoding="utf-8"))
print(R["meta"])
MAP = {"below_range": "below", "within_range": "within", "above_range": "above",
       "raise_speed": "raise", "lower_speed": "lower", "keep_speed": "keep"}


def top(run, it):
    a = it["answers"]
    if "noul" in run:
        hi, lo = a["high"]["noul"], a["low"]["noul"]
        if hi >= .5 and lo >= .5:
            p, cf = ("above" if hi > lo else "below"), max(hi, lo)
        elif hi >= .5:
            p, cf = "above", hi
        elif lo >= .5:
            p, cf = "below", lo
        else:
            p, cf = "within", 1 - max(hi, lo)
        return p, cf
    k = next(iter(a))
    return MAP[a[k]["choice"]], a[k]["answer_confidence"]


def report(name, items):
    n = len(items)
    ok = sum(p == t for p, t, _ in items)
    per = defaultdict(lambda: [0, 0])
    for p, t, _ in items:
        per[t][0] += p == t
        per[t][1] += 1
    bal = sum(a / b for a, b in per.values()) / len(per)
    maj = max(b for _, b in per.values()) / n
    wrong = [cf for p, t, cf in items if p != t]
    hc = [(p == t) for p, t, cf in items if cf >= .8]
    print("%-34s n=%4d 정확도 %.3f 균형정확도 %.3f (최빈값 %.3f) | 판정별 %s | 틀린 답 평균확신 %s | 확신>=0.8: %d개 중 정답 %s"
          % (name, n, ok / n, bal, maj, {k: "%d/%d" % tuple(v) for k, v in sorted(per.items())},
             ("%.2f" % (sum(wrong) / len(wrong))) if wrong else "-", len(hc), ("%.3f" % (sum(hc) / len(hc))) if hc else "-"))


for run, items in R["runs"].items():
    print("\n==", run)
    for s in ("real", "synth", None):
        sub = [(*top(run, it)[:1], it["case"]["label"], top(run, it)[1]) for it in items if s is None or it["case"]["set"] == s]
        report({"real": "실제 기록", "synth": "합성", None: "전체"}[s], sub)
    print("   혼동:", {"%s→%s" % k: v for k, v in sorted(Counter((it["case"]["label"], top(run, it)[0]) for it in items).items())})
    if "case" in items[0] and "sensor" in items[0]["case"]:
        for sn in ("temperature", "humidity", "moisture_pct", "lux"):
            sub = [(top(run, it)[0], it["case"]["label"], top(run, it)[1]) for it in items if it["case"]["sensor"] == sn]
            report("  " + sn, sub)

# 경계와의 거리(범위 폭 대비)별 정확도 — 실제+합성
print("\n== 경계와의 거리별 정확도(범위 폭 대비)")
bins = [(0, .05), (.05, .2), (.2, .5), (.5, 1.0), (1.0, 99)]
for run in ("A_json_choice", "A_json_noul", "B_text_choice", "B_text_noul"):
    row = []
    for a, b in bins:
        ss = []
        for it in R["runs"][run]:
            c = it["case"]
            w = c["max"] - c["min"]
            d = min(abs(c["value"] - c["min"]), abs(c["value"] - c["max"])) / w
            if a <= d < b:
                ss.append(top(run, it)[0] == c["label"])
        row.append("%g~%g: %.3f (n=%d)" % (a, b, sum(ss) / len(ss) if ss else float("nan"), len(ss)))
    print(run, " | ".join(row))

# 놓침·오작동(제어 관점): 이탈인데 정상 판정 / 정상인데 이탈 판정 / 방향 반대
print("\n== 제어 관점(전체)")
for run in ("A_json_choice", "A_json_noul", "B_text_choice", "B_text_noul"):
    P = [(top(run, it)[0], it["case"]["label"]) for it in R["runs"][run]]
    dev = [x for x in P if x[1] != "within"]
    nor = [x for x in P if x[1] == "within"]
    print(run, "이탈을 정상으로 놓침 %d/%d" % (sum(p == "within" for p, _ in dev), len(dev)),
          "반대 방향 %d/%d" % (sum(p not in ("within", t) for p, t in dev), len(dev)),
          "정상을 이탈로 %d/%d" % (sum(p != "within" for p, _ in nor), len(nor)))

# 같은 값인데 선택지 순서만 바꿨을 때 답이 달라진 비율
a, b = R["runs"]["A_json_choice"], R["runs"]["A_json_choice_reversed"]
print("\n선택지 순서를 뒤집었을 때 답이 바뀐 비율 %.3f" % (sum(top("A", x)[0] != top("A", y)[0] for x, y in zip(a, b)) / len(a)))
