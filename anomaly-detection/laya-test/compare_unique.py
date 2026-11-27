# -*- coding: utf-8 -*-
"""같은 입력을 한 번만 세어(중복 제거) 두 모델을 비교한다."""
import json
from collections import Counter
A = json.load(open("results.json", encoding="utf-8"))["runs"]; B = json.load(open("results_typed_decisions.json", encoding="utf-8"))["runs"]
M = {"below_range":"below","within_range":"within","above_range":"above","raise_speed":"raise","lower_speed":"lower","keep_speed":"keep"}
def top(run, it):
    a = it["answers"]
    if "noul" in run:
        hi, lo = a["high"]["noul"], a["low"]["noul"]
        if hi>=.5 and lo>=.5: return ("above" if hi>lo else "below"), max(hi,lo)
        if hi>=.5: return "above", hi
        if lo>=.5: return "below", lo
        return "within", 1-max(hi,lo)
    k = next(iter(a)); return M[a[k]["choice"]], a[k]["answer_confidence"]
def key(run, c):
    if run.startswith("C"): return (c["sensor"], c["label"])
    if run == "F_json": return (c["temperature"], c["humidity"])
    if run == "F_done":
        t,h=c["temperature"],c["humidity"]
        return (0 if t<22 else 3 if t>30 else 2 if t>29 else 1, 0 if h<60 else 3 if h>70 else 2 if h>65 else 1)
    return (c["sensor"], c["value"])
for run in A:
    for nm, R in (("영어판", A), ("튜닝판", B)):
        seen = {}; incons = 0
        for it in R[run]:
            k = key(run, it["case"]); v = (top(run, it)[0], it["case"]["label"], top(run, it)[1])
            if k in seen and seen[k][0] != v[0]: incons += 1
            seen.setdefault(k, v)
        P = list(seen.values()); n = len(P); acc = sum(p==t for p,t,_ in P)/n
        lab = Counter(t for _,t,_ in P); per = {l: "%d/%d" % (sum(p==t for p,t,_ in P if t==l), lab[l]) for l in sorted(lab)}
        s = "%-24s %s n=%4d 정확도 %.3f (최빈값 %.3f) %s 같은입력다른답 %d" % (run, nm, n, acc, max(lab.values())/n, per, incons)
        if not run.startswith("F"):
            dev=[x for x in P if x[1]!="within"]; nor=[x for x in P if x[1]=="within"]
            s += " | 놓침 %d/%d 반대 %d 정상→이탈 %d/%d" % (sum(p=="within" for p,_,_ in dev), len(dev), sum(p not in("within",t) for p,t,_ in dev), sum(p!="within" for p,_,_ in nor), len(nor))
        hc=[p==t for p,t,c in P if c>=.8]; s += " | 확률>=0.8 %d개 중 %d" % (len(hc), sum(hc))
        print(s)
for nm, R in (("영어판", A), ("튜닝판", B)):
    d = {}
    for x, y in zip(R["A_json_choice"], R["A_json_choice_reversed"]): d[key("A", x["case"])] = top("A", x)[0] != top("A", y)[0]
    print("순서 뒤집어 답 바뀜", nm, "%d/%d = %.3f" % (sum(d.values()), len(d), sum(d.values())/len(d)))
