# -*- coding: utf-8 -*-
"""
사후 온도 보정 + 평가 지표. 표준 라이브러리만 쓴다 — rules 엔진(젯슨)이 추론 때 같은 apply 를 쓴다.

Laya 는 확률을 과신하는 편이다. 모델이 돌려준 확률 p 에 질문별 온도 T 를 한 번 더 건다:
    p_T(i) = p(i)^(1/T) / Σ_j p(j)^(1/T)
softmax(z/T0) 의 출력에 이 변환을 걸면 softmax(z/(T0·T)) 와 정확히 같다 — 즉 로짓 온도 보정과 같은 효과를
모델 내부를 건드리지 않고(ONNX 묶음 그대로) 낸다. T > 1 이면 확률이 퍼지고(덜 과신), T < 1 이면 모인다.
"""
import math
from collections import Counter, defaultdict

EPS = 1e-9
T_MIN, T_MAX = 0.25, 8.0


def apply_temperature(probs, T):
    if T is None or abs(T - 1.0) < 1e-12:
        return dict(probs)
    w = {k: math.exp(math.log(max(v, EPS)) / T) for k, v in probs.items()}
    s = sum(w.values())
    return {k: v / s for k, v in w.items()}


def nll(pairs, T=1.0):
    """pairs: [(pred, gold)] — 둘 다 {선택지: 확률}. 부드러운 정답에 대한 교차 엔트로피 평균."""
    tot = 0.0
    for pred, gold in pairs:
        p = apply_temperature(pred, T)
        tot -= sum(g * math.log(max(p.get(k, 0.0), EPS)) for k, g in gold.items() if g > 0)
    return tot / max(1, len(pairs))


def fit_temperature(pairs, lo=T_MIN, hi=T_MAX, iters=60):
    """log T 위에서 황금분할 탐색으로 NLL 최소 T."""
    if not pairs:
        return 1.0
    a, b = math.log(lo), math.log(hi)
    g = (math.sqrt(5) - 1) / 2
    c, d = b - g * (b - a), a + g * (b - a)
    fc, fd = nll(pairs, math.exp(c)), nll(pairs, math.exp(d))
    for _ in range(iters):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - g * (b - a)
            fc = nll(pairs, math.exp(c))
        else:
            a, c, fc = c, d, fd
            d = a + g * (b - a)
            fd = nll(pairs, math.exp(d))
    return round(math.exp((a + b) / 2), 4)


def _argmax(p):
    return max(p.items(), key=lambda kv: kv[1])[0]


def ece(pairs, bins=10):
    """예상 보정 오차: 확신(최대 확률) 구간별 |정확도 - 평균 확신| 의 가중 평균."""
    if not pairs:
        return None
    b = defaultdict(lambda: [0, 0.0, 0])          # 개수, 확신 합, 정답 수
    for pred, gold in pairs:
        conf = max(pred.values())
        i = min(bins - 1, int(conf * bins))
        b[i][0] += 1
        b[i][1] += conf
        b[i][2] += int(_argmax(pred) == _argmax(gold))
    n = len(pairs)
    return sum(c / n * abs(hit / c - s / c) for c, s, hit in b.values())


def brier(pairs):
    if not pairs:
        return None
    tot = 0.0
    for pred, gold in pairs:
        keys = set(pred) | set(gold)
        tot += sum((pred.get(k, 0.0) - gold.get(k, 0.0)) ** 2 for k in keys)
    return tot / len(pairs)


def question_report(pairs, T=1.0):
    cal = [(apply_temperature(p, T), g) for p, g in pairs]
    conf = Counter()
    for p, g in cal:
        conf[(_argmax(g), _argmax(p))] += 1
    acc = sum(v for (a, b), v in conf.items() if a == b) / max(1, len(cal))
    return {
        "n": len(cal),
        "temperature": T,
        "accuracy": round(acc, 4),
        "ece": None if not cal else round(ece(cal), 4),
        "brier": None if not cal else round(brier(cal), 4),
        "mean_confidence": None if not cal else round(sum(max(p.values()) for p, _ in cal) / len(cal), 4),
        "confusion": {"%s->%s" % k: v for k, v in sorted(conf.items())},
    }
