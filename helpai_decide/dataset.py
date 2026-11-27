# -*- coding: utf-8 -*-
"""
데이터셋 조립 — 합성(synth) + 이력 재생(replay) + 젯슨 shadow 기록 → train / val / test JSONL.

Laya 학습 행 형식 (laya.train 이 읽는 그대로):
    {"state": {...}, "questions": {...}, "gold": {"질문": {"probabilities": {...}}}}
행마다 id·출처·시나리오 같은 부가 정보는 같은 이름의 *.meta.jsonl 에 같은 줄 순서로 따로 쓴다
(학습기가 모르는 키를 만나도 문제가 없게).

나누기 규칙
  · 합성: id 해시로 80/10/10 — 재현 가능
  · 재생: **날짜 단위** — 마지막 날짜들이 test, 그 앞이 val. 같은 날이 train·test 에 섞이면 점수가 부풀려진다
  · 같은 state 문장은 한 번만(중복 제거) — 재생 데이터는 분마다 거의 같아서 subsample_min 마다 하나씩
"""
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from . import labeler, synth
from .features import FEATURE_VERSION, build_state, state_text_len
from .questions import load_questions, questions_sha256
from .replay import read_wide_csv, replay

SPLITS = ("train", "val", "test")


def _hash_split(key, val_frac, test_frac):
    h = int(hashlib.sha1(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    if h < test_frac:
        return "test"
    if h < test_frac + val_frac:
        return "val"
    return "train"


def _date_splits(dates, val_frac, test_frac):
    ds = sorted(set(dates))
    n = len(ds)
    n_test = max(1, round(n * test_frac)) if n >= 3 else 0
    n_val = max(1, round(n * val_frac)) if n >= 3 else 0
    out = {}
    for i, d in enumerate(ds):
        out[d] = "test" if i >= n - n_test else ("val" if i >= n - n_test - n_val else "train")
    return out


def make_row(snap, profile, questions):
    return {"state": build_state(snap, profile), "questions": questions,
            "gold": labeler.label(snap, profile, questions)}


def build(cfg, profile, questions, out_dir, log=print):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    budget = int(cfg.get("state_token_budget", 300))
    val_frac, test_frac = float(cfg.get("val_frac", 0.1)), float(cfg.get("test_frac", 0.1))
    buckets = defaultdict(list)          # split → [(row, meta)]
    seen = set()
    stats = Counter()

    def add(split, row, meta):
        key = json.dumps(row["state"], sort_keys=True)
        if key in seen:
            stats["dup_skipped"] += 1
            return
        seen.add(key)
        n_tok = state_text_len(row["state"])
        if n_tok > budget:
            stats["over_budget_skipped"] += 1
            return
        meta["approx_tokens"] = n_tok
        buckets[split].append((row, meta))
        stats["rows_" + meta["source"]] += 1

    sc = cfg.get("synth")
    if sc and sc.get("enabled", True):
        log("합성 %d 건 생성 (seed %s)" % (sc.get("n", synth.DEFAULTS["n"]), sc.get("seed", synth.DEFAULTS["seed"])))
        for i, (snap, scenario) in enumerate(synth.generate(profile, sc)):
            rid = "synth-%s-%06d" % (sc.get("seed", synth.DEFAULTS["seed"]), i)
            add(_hash_split(rid, val_frac, test_frac), make_row(snap, profile, questions),
                {"id": rid, "source": "synth", "scenario": scenario, "snapshot": snap.to_dict()})

    rc = cfg.get("replay")
    if rc and rc.get("enabled", True):
        sub = int(rc.get("subsample_min", 5))
        items = []
        for path in rc.get("csv", []):
            log("재생 %s" % path)
            for k, (snap, info) in enumerate(replay(read_wide_csv(path), rc["zone"])):
                if k % sub:
                    continue
                items.append((snap, info))
        dsplit = _date_splits([i["local_date"] for _, i in items], val_frac, test_frac)
        for snap, info in items:
            rid = "replay-%s" % info["utc"]
            add(dsplit[info["local_date"]], make_row(snap, profile, questions),
                {"id": rid, "source": "replay", "scenario": "real", "snapshot": snap.to_dict(), **info})

    hc = cfg.get("shadow")
    if hc and hc.get("enabled", True):
        import glob
        from .features import Snapshot
        items = []
        for pat in hc.get("logs", []):
            for path in sorted(glob.glob(pat)):
                log("shadow %s" % path)
                for rec in read_rows(path):
                    if "snapshot" not in rec:
                        continue
                    snap = Snapshot.from_dict(rec["snapshot"])
                    items.append((snap, {"utc": rec["t"], "local_date": snap.local_time.date().isoformat(),
                                         "sn": rec.get("sn"), "model_revision": (rec.get("model") or {}).get("revision")}))
        sub = int(hc.get("subsample", 5))
        items = items[::max(1, sub)]
        dsplit = _date_splits([i["local_date"] for _, i in items], val_frac, test_frac)
        for snap, info in items:
            rid = "shadow-%s-%s" % (info.get("sn"), info["utc"])
            add(dsplit[info["local_date"]], make_row(snap, profile, questions),
                {"id": rid, "source": "shadow", "scenario": "real", "snapshot": snap.to_dict(), **info})

    for split in SPLITS:
        rows = buckets.get(split, [])
        with open(out / ("%s.jsonl" % split), "w", encoding="utf-8") as fr, \
                open(out / ("%s.meta.jsonl" % split), "w", encoding="utf-8") as fm:
            for row, meta in rows:
                fr.write(json.dumps(row, ensure_ascii=False) + "\n")
                fm.write(json.dumps(meta, ensure_ascii=False) + "\n")
        stats["split_" + split] = len(rows)

    summary = {"feature_version": FEATURE_VERSION, "questions_sha256": questions_sha256(questions),
               "stats": dict(stats), "label_balance": label_balance(buckets)}
    with open(out / "dataset_info.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def label_balance(buckets):
    """split·질문별 정답(최대 확률 선택지) 분포 — 한쪽으로 쏠렸는지 보는 용도."""
    out = {}
    for split, rows in buckets.items():
        c = defaultdict(Counter)
        for row, _ in rows:
            for q, g in row["gold"].items():
                c[q][labeler.argmax(g["probabilities"])] += 1
        out[split] = {q: dict(v) for q, v in c.items()}
    return out


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_rows(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                yield json.loads(line)


def check_rows(path, questions=None):
    """학습 행 형식 검사. 문제 목록을 돌려준다(빈 목록 = 통과)."""
    problems = []
    for i, row in enumerate(read_rows(path)):
        for k in ("state", "questions", "gold"):
            if k not in row:
                problems.append("%d행: %s 없음" % (i + 1, k))
        for q, g in row.get("gold", {}).items():
            p = g.get("probabilities", {})
            s = sum(p.values())
            if abs(s - 1.0) > 1e-6 or any(v < 0 for v in p.values()):
                problems.append("%d행 %s: 확률 합 %.4f" % (i + 1, q, s))
        if questions is not None and row.get("questions") != questions:
            problems.append("%d행: questions 가 지금 정의와 다름" % (i + 1))
        if len(problems) > 20:
            break
    return problems
