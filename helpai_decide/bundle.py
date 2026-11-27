# -*- coding: utf-8 -*-
"""
묶음(bundle) — 학습(집 PC)과 추론(젯슨)을 잇는 유일한 계약.

    <revision>/
      laya.onnx [laya.onnx.data]  laya_config.json  tokenizer/     ← ONNX 내보내기 결과 (INT8 또는 FP16)
      calibration.yaml                                             ← 질문별 사후 온도
      questions.yaml                                               ← 학습에 쓴 질문 정의 (configs/questions.yaml 복사)
      eval_report.json                                             ← test 셋 결과
      manifest.json
      SHA256SUMS                                                   ← `sha256sum -c SHA256SUMS` 로도 검사 가능

젯슨 laya-edge 는 SHA256SUMS 가 맞지 않으면 적재를 거부한다. rules 엔진은 manifest 의
questions_sha256·feature_version 이 자기 것과 다르면 그 모델 답을 기록만 한다(shadow).
"""
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .calibration import T_MAX, T_MIN
from .features import FEATURE_VERSION
from .questions import load_questions, questions_sha256

MODEL_FILES = ("laya.onnx", "laya_config.json")
OPTIONAL_MODEL_FILES = ("laya.onnx.data",)
REQUIRED = MODEL_FILES + ("tokenizer", "calibration.yaml", "questions.yaml", "manifest.json")


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _files_under(root):
    root = Path(root)
    return sorted(p for p in root.rglob("*") if p.is_file() and p.name != "SHA256SUMS")


def write_sums(root):
    root = Path(root)
    lines = ["%s  %s" % (sha256_file(p), p.relative_to(root).as_posix()) for p in _files_under(root)]
    # LF 고정 — Windows 에서 만들어도 젯슨(Linux)의 sha256sum·laya-edge 가 그대로 읽게
    (root / "SHA256SUMS").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return len(lines)


def _git_rev(path):
    try:
        return subprocess.check_output(["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
                                       stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return None


def make(onnx_dir, calibration, questions_path, report, out_dir, revision, precision, base, exporter_dir=None,
         dataset_info=None, notes=""):
    onnx_dir, out = Path(onnx_dir), Path(out_dir)
    if out.exists():
        raise SystemExit("%s 가 이미 있다 — revision 을 바꾸거나 지우고 다시" % out)
    for f in MODEL_FILES + ("tokenizer",):
        if not (onnx_dir / f).exists():
            raise SystemExit("ONNX 폴더에 %s 가 없다: %s" % (f, onnx_dir))
    out.mkdir(parents=True)
    for f in MODEL_FILES + OPTIONAL_MODEL_FILES:
        if (onnx_dir / f).exists():
            shutil.copy2(onnx_dir / f, out / f)
    shutil.copytree(onnx_dir / "tokenizer", out / "tokenizer")
    shutil.copy2(calibration, out / "calibration.yaml")
    shutil.copy2(questions_path, out / "questions.yaml")
    rep = json.loads(Path(report).read_text(encoding="utf-8")) if report else {}
    if report:
        shutil.copy2(report, out / "eval_report.json")
    questions = load_questions(questions_path)
    ds = json.loads(Path(dataset_info).read_text(encoding="utf-8")) if dataset_info else {}
    manifest = {
        "revision": revision,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base": base,
        "precision": precision,
        "questions_sha256": questions_sha256(questions),
        "feature_version": FEATURE_VERSION,
        "kit_commit": _git_rev(Path(__file__).resolve().parent),
        "exporter_commit": _git_rev(exporter_dir) if exporter_dir else None,
        "dataset": ds.get("stats"),
        "eval": {q: {k: v for k, v in r.items() if k in ("n", "accuracy", "ece", "brier", "temperature")}
                 for q, r in rep.get("questions", {}).items()},
        "notes": notes,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    n = write_sums(out)
    return manifest, n


def verify(bundle_dir, questions_path=None):
    """문제 목록(빈 목록 = 통과)."""
    root = Path(bundle_dir)
    problems = []
    for f in REQUIRED:
        if not (root / f).exists():
            problems.append("없음: %s" % f)
    sums = root / "SHA256SUMS"
    if not sums.exists():
        return problems + ["없음: SHA256SUMS"]
    listed = set()
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split("  ", 1)
        listed.add(rel)
        p = root / rel
        if not p.exists():
            problems.append("SHA256SUMS 에 있으나 파일 없음: %s" % rel)
        elif sha256_file(p) != digest:
            problems.append("해시 불일치: %s" % rel)
    for p in _files_under(root):
        rel = p.relative_to(root).as_posix()
        if rel not in listed:
            problems.append("SHA256SUMS 에 없는 파일: %s" % rel)
    if (root / "manifest.json").exists() and (root / "questions.yaml").exists():
        m = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        qs = load_questions(root / "questions.yaml")
        if m.get("questions_sha256") != questions_sha256(qs):
            problems.append("manifest 의 questions_sha256 이 묶음 안 questions.yaml 과 다름")
        if questions_path is not None and questions_sha256(load_questions(questions_path)) != m.get("questions_sha256"):
            problems.append("묶음 질문 ≠ 지금 configs/questions.yaml → rules 가 shadow 로 내린다 (재학습 필요)")
        if m.get("feature_version") != FEATURE_VERSION:
            problems.append("feature_version %s ≠ 지금 %s → shadow" % (m.get("feature_version"), FEATURE_VERSION))
    if (root / "calibration.yaml").exists():
        cal = yaml.safe_load((root / "calibration.yaml").read_text(encoding="utf-8")) or {}
        for q, T in (cal.get("temperatures") or {}).items():
            if not (T_MIN <= float(T) <= T_MAX):
                problems.append("calibration %s 온도 %s 가 범위(%s~%s) 밖" % (q, T, T_MIN, T_MAX))
    return problems
