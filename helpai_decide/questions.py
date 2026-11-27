# -*- coding: utf-8 -*-
"""질문 정의(configs/questions.yaml) 읽기·검사·해시. 학습과 추론이 같은 해시를 가져야 모델 답을 쓴다."""
import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUESTIONS = ROOT / "configs" / "questions.yaml"
TYPES = ("choice", "noul", "score")


def load_questions(path=None):
    """{"이름": {"type", "instructions", "criteria"}} 를 돌려준다 (Laya·Jev 요청의 questions 그대로)."""
    with open(Path(path) if path else DEFAULT_QUESTIONS, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    qs = doc["questions"]
    out = {}
    for name, q in qs.items():
        validate_question(name, q)
        item = {"type": q["type"], "instructions": " ".join(str(q["instructions"]).split())}
        if "criteria" in q:
            crit = q["criteria"]
            if isinstance(crit, dict):
                crit = {str(k): str(v) for k, v in crit.items()}
            item["criteria"] = crit
        out[name] = item
    return out


def validate_question(name, q):
    t = q.get("type")
    if t not in TYPES:
        raise ValueError("%s: type %r 은 %s 중 하나여야 한다" % (name, t, TYPES))
    if not str(q.get("instructions", "")).strip():
        raise ValueError("%s: instructions 가 비었다" % name)
    crit = q.get("criteria")
    if t == "choice":
        if not isinstance(crit, dict) or len(crit) < 2:
            raise ValueError("%s: choice 는 선택지 2개 이상(criteria 사전)" % name)
        if len(crit) > 20:
            raise ValueError("%s: 선택지가 20개를 넘으면 Laya 정확도가 크게 떨어진다" % name)
    if t == "noul" and crit is not None:
        keys = {str(k) for k in crit}
        if keys != {"true", "false"}:
            raise ValueError("%s: noul criteria 키는 'true'·'false' 여야 한다 (YAML 에서 따옴표 필수)" % name)
    if t == "score" and (not isinstance(crit, list) or not 2 <= len(crit) <= 10):
        raise ValueError("%s: score 는 단계 설명 목록 2~10개" % name)


def options_of(q):
    """질문의 선택지 키 목록(확률 분포의 키)."""
    if q["type"] == "choice":
        return list(q["criteria"].keys())
    if q["type"] == "noul":
        return ["false", "true"]
    return [str(i) for i in range(len(q["criteria"]))]


def questions_sha256(questions):
    """정규화한 JSON 의 SHA-256. 키 순서·공백 차이는 무시한다."""
    blob = json.dumps(questions, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
