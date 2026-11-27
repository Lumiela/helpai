# -*- coding: utf-8 -*-
"""
모델 답 받기 — 두 경로를 같은 결과 형식으로.

  HttpPredictor   POST <url>/v1/systemone  (edge/server.mjs, laya-serve, Jev 모두 같은 모양)  ← 표준 라이브러리만
  LayaPredictor   PC 에서 laya 파이썬 패키지로 PyTorch 체크포인트 직접 (학습 직후 확인용)       ← torch·laya 필요

결과 한 줄: {"id", "pred": {질문: {선택지: 확률}}, "gold": {...}, "latency_ms"}
"""
import json
import time
import urllib.request
from pathlib import Path

from .dataset import read_rows
from .questions import options_of


def normalize_answer(q, ans):
    """Laya/Jev 답 하나 → 질문 선택지 전체에 대한 확률 분포(합 1)."""
    opts = options_of(q)
    if ans is None:
        raise ValueError("답 없음")
    if q["type"] == "noul":
        if "noul" in ans and ans["noul"] is not None:
            p = float(ans["noul"])
        else:
            pr = ans.get("probabilities", {})
            p = float(pr.get("true", pr.get(True, 0.0)))
        p = min(1.0, max(0.0, p))
        return {"false": 1.0 - p, "true": p}
    pr = ans.get("probabilities")
    if not isinstance(pr, dict):
        raise ValueError("probabilities 없음: %r" % ans)
    d = {o: float(pr.get(o, 0.0)) for o in opts}
    s = sum(d.values())
    if s <= 0:
        return {o: 1.0 / len(opts) for o in opts}
    return {o: v / s for o, v in d.items()}


def normalize_response(questions, resp):
    answers = resp.get("answers", resp)
    return {name: normalize_answer(q, answers.get(name)) for name, q in questions.items()}


class HttpPredictor:
    def __init__(self, url, api_key=None, timeout=60):
        self.url = url.rstrip("/")
        if not self.url.endswith("/v1/systemone"):
            self.url += "/v1/systemone"
        self.key = api_key
        self.timeout = timeout
        self.name = self.url

    def ask(self, state, questions):
        body = json.dumps({"state": state, "questions": questions}).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        if self.key:
            req.add_header("Authorization", "Bearer " + self.key)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))


class LayaPredictor:
    """laya 패키지의 정확한 호출 이름은 판마다 다를 수 있어 몇 가지를 차례로 시도한다(check-env 가 실제 이름을 보여 준다)."""

    def __init__(self, model_dir, device="cuda"):
        import laya                                  # noqa: 지연 import
        self.name = str(model_dir)
        self.agent = None
        errors = []
        for make in (lambda: laya.Agent(str(model_dir), device=device),
                     lambda: laya.load(str(model_dir), device=device),
                     lambda: laya.load(str(model_dir))):
            try:
                self.agent = make()
                break
            except Exception as e:                   # 다음 방법 시도
                errors.append(repr(e))
        if self.agent is None:
            raise RuntimeError("laya 로 %s 를 열지 못했다: %s" % (model_dir, " | ".join(errors)))
        for m in ("predict", "system_one", "systemone", "__call__"):
            if callable(getattr(self.agent, m, None)):
                self._call = getattr(self.agent, m)
                break
        else:
            raise RuntimeError("에이전트에 predict 계열 메서드가 없다: %s" % dir(self.agent))

    def ask(self, state, questions):
        return self._call(state, questions)


def _meta_ids(rows_path):
    meta = Path(str(rows_path).replace(".jsonl", ".meta.jsonl"))
    if meta.exists():
        return [m.get("id") for m in read_rows(meta)]
    return None


def run(predictor, rows_path, out_path, limit=None, log=print):
    ids = _meta_ids(rows_path)
    n, fails = 0, 0
    with open(out_path, "w", encoding="utf-8") as out:
        for i, row in enumerate(read_rows(rows_path)):
            if limit and n >= limit:
                break
            t0 = time.time()
            try:
                resp = predictor.ask(row["state"], row["questions"])
                pred = normalize_response(row["questions"], resp)
            except Exception as e:
                fails += 1
                log("  %d행 실패: %r" % (i + 1, e))
                if fails > 20 and fails > n:
                    raise SystemExit("실패가 너무 많다 — 서버·모델 상태를 먼저 확인")
                continue
            ms = (time.time() - t0) * 1000.0
            gold = {q: g["probabilities"] for q, g in row["gold"].items()}
            rec = {"id": ids[i] if ids and i < len(ids) else i, "pred": pred, "gold": gold, "latency_ms": round(ms, 1)}
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
            if n % 200 == 0:
                log("  %d 건" % n)
    return n, fails


def load_preds(path):
    return list(read_rows(path))


def pairs_by_question(preds):
    out = {}
    for r in preds:
        for q, p in r["pred"].items():
            out.setdefault(q, []).append((p, r["gold"][q]))
    return out


def compare(a_path, b_path):
    """두 예측 파일(같은 데이터) 비교 — fp32 와 INT8 묶음이 같은 답을 내는지."""
    a = {r["id"]: r for r in load_preds(a_path)}
    b = {r["id"]: r for r in load_preds(b_path)}
    common = [k for k in a if k in b]
    res = {}
    for k in common:
        for q, pa in a[k]["pred"].items():
            pb = b[k]["pred"].get(q)
            if pb is None:
                continue
            st = res.setdefault(q, {"n": 0, "top1_same": 0, "abs_diff_sum": 0.0, "abs_diff_max": 0.0})
            st["n"] += 1
            st["top1_same"] += int(max(pa, key=pa.get) == max(pb, key=pb.get))
            d = max(abs(pa[o] - pb.get(o, 0.0)) for o in pa)
            st["abs_diff_sum"] += d
            st["abs_diff_max"] = max(st["abs_diff_max"], d)
    for q, st in res.items():
        st["top1_agreement"] = round(st["top1_same"] / max(1, st["n"]), 4)
        st["mean_max_abs_diff"] = round(st.pop("abs_diff_sum") / max(1, st["n"]), 5)
        st["abs_diff_max"] = round(st["abs_diff_max"], 5)
    return {"rows_compared": len(common), "questions": res}
