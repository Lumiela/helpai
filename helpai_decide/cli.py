# -*- coding: utf-8 -*-
"""
명령줄:  python -m helpai_decide <명령> [옵션]      (전체 흐름은 manual/02_학습_실행_매뉴얼.md)

  check-env                         학습 환경 점검
  download-base                     기반 체크포인트 내려받기
  export-influx                     서버 InfluxDB(읽기 창구 /influx) → 1분 평균 CSV
  dataset build | check             데이터셋 만들기 / 형식 검사
  token-count                       state 토큰 수 실측
  train                             미세조정
  predict                           모델 답 받기 (--endpoint HTTP 또는 --model-dir 파이썬)
  calibrate                         val 예측으로 질문별 온도 적합 → calibration.yaml
  evaluate                          test 예측 평가 → eval_report.json (+ 합격 여부)
  export-onnx / convert             ONNX 내보내기 / INT8·FP16 변환
  compare                           두 예측 파일 비교 (fp32 vs INT8)
  bundle make | verify              묶음 만들기 / 검사
  sample-request                    젯슨 실측용 요청 예시 JSON
"""
import argparse
import json
import os
import sys
from pathlib import Path

import yaml

from . import bundle as bundle_mod
from . import calibration as cal
from . import dataset as ds
from . import predict as pr
from .profile import load_profile
from .questions import load_questions

ROOT = Path(__file__).resolve().parent.parent


def _yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def cmd_check_env(a):
    from . import laya_tools
    return 0 if laya_tools.check_env() else 1


def cmd_download_base(a):
    from . import laya_tools
    laya_tools.download_base(a.out, repo=a.repo, subfolder=a.subfolder, revision=a.revision)


def cmd_export_influx(a):
    from . import influx_export as ie
    zone = _yaml(a.zone_config)["zone"]
    n = ie.export(zone, ie.parse_time(a.start), ie.parse_time(a.stop), a.out, url=a.url, org=a.org, bucket=a.bucket)
    print("저장 %s (%d 분)" % (a.out, n))


def cmd_dataset_build(a):
    cfg = _yaml(a.config)
    if cfg.get("replay") and cfg["replay"].get("enabled") and "zone" not in cfg["replay"]:
        cfg["replay"]["zone"] = _yaml(ROOT / cfg["replay"].get("zone_config", "configs/replay.yaml"))["zone"]
    summary = ds.build(cfg, load_profile(a.profile, a.crop), load_questions(a.questions), a.out)
    print(json.dumps(summary["stats"], ensure_ascii=False, indent=2))
    print("정답 분포(train): %s" % json.dumps(summary["label_balance"].get("train", {}), ensure_ascii=False))


def cmd_dataset_check(a):
    probs = ds.check_rows(a.data, load_questions(a.questions))
    for p in probs:
        print("  ✗ " + p)
    print("통과" if not probs else "문제 %d 건 이상" % len(probs))
    return 0 if not probs else 1


def cmd_token_count(a):
    from . import laya_tools
    laya_tools.token_count(a.tokenizer, a.data, a.limit)


def cmd_train(a):
    from . import laya_tools
    cfg = _yaml(a.config).get("train", {})
    laya_tools.train(a.data, a.base, a.out, cfg, device=a.device)


def cmd_predict(a):
    if a.endpoint:
        p = pr.HttpPredictor(a.endpoint, api_key=os.environ.get(a.key_env) if a.key_env else None)
    elif a.model_dir:
        p = pr.LayaPredictor(a.model_dir, device=a.device)
    else:
        raise SystemExit("--endpoint 또는 --model-dir 중 하나")
    n, fails = pr.run(p, a.data, a.out, limit=a.limit)
    preds = pr.load_preds(a.out)
    lat = sorted(r["latency_ms"] for r in preds)
    if lat:
        print("예측 %d 건 (실패 %d) · 지연 중앙 %.0f ms · p95 %.0f ms" % (n, fails, lat[len(lat) // 2],
                                                                lat[max(0, int(len(lat) * 0.95) - 1)]))


def cmd_calibrate(a):
    pairs = pr.pairs_by_question(pr.load_preds(a.preds))
    temps, lines = {}, []
    for q, ps in sorted(pairs.items()):
        T = cal.fit_temperature(ps)
        temps[q] = T
        before, after = cal.question_report(ps, 1.0), cal.question_report(ps, T)
        lines.append("  %-14s T=%-6s ECE %s → %s  NLL %.4f → %.4f" % (
            q, T, before["ece"], after["ece"], cal.nll(ps, 1.0), cal.nll(ps, T)))
    doc = {"version": 1, "method": "power_temperature",
           "note": "p_T(i) = p(i)^(1/T) / sum_j p(j)^(1/T) — helpai_decide.calibration.apply_temperature",
           "fitted_on": str(a.preds), "temperatures": temps}
    Path(a.out).write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print("\n".join(lines))
    print("저장 %s" % a.out)


def cmd_evaluate(a):
    pairs = pr.pairs_by_question(pr.load_preds(a.preds))
    temps = (_yaml(a.calibration).get("temperatures") or {}) if a.calibration else {}
    acc_min, ece_max = a.acc_min, a.ece_max
    rep = {"preds": str(a.preds), "thresholds": {"accuracy_min": acc_min, "ece_max": ece_max}, "questions": {}}
    ok_all = True
    print("%-14s %6s %8s %7s %7s %6s  %s" % ("질문", "n", "정확도", "ECE", "Brier", "T", "판정"))
    for q, ps in sorted(pairs.items()):
        r = cal.question_report(ps, temps.get(q, 1.0))
        r["pass"] = r["accuracy"] >= acc_min and (r["ece"] is not None and r["ece"] <= ece_max)
        ok_all &= r["pass"]
        rep["questions"][q] = r
        print("%-14s %6d %8.3f %7.3f %7.3f %6.2f  %s" % (q, r["n"], r["accuracy"], r["ece"], r["brier"],
                                                      r["temperature"], "합격" if r["pass"] else "불합격"))
    rep["all_pass"] = ok_all
    Path(a.out).write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print("저장 %s · 전체 %s" % (a.out, "합격" if ok_all else "불합격 (불합격 질문은 젯슨에서 shadow 로 남는다)"))


def cmd_export_onnx(a):
    from . import laya_tools
    laya_tools.export_onnx(a.model_dir, a.exporter, a.out, base_dir=a.base)


def cmd_convert(a):
    from . import laya_tools
    laya_tools.convert(a.src, a.out, a.precision)


def cmd_compare(a):
    res = pr.compare(a.a, a.b)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    worst = min((q["top1_agreement"] for q in res["questions"].values()), default=0)
    print("최상위 답 일치율 최저 %.3f — %s" % (worst, "통과(≥ %.2f)" % a.min_agree if worst >= a.min_agree else "미달"))
    return 0 if worst >= a.min_agree else 1


def cmd_bundle_make(a):
    m, n = bundle_mod.make(a.onnx_dir, a.calibration, a.questions, a.report, a.out, a.revision, a.precision,
                           a.base, exporter_dir=a.exporter_dir, dataset_info=a.dataset_info, notes=a.notes)
    print(json.dumps(m, ensure_ascii=False, indent=2))
    print("묶음 %s (파일 %d 개)" % (a.out, n))


def cmd_bundle_verify(a):
    probs = bundle_mod.verify(a.dir, a.questions)
    for p in probs:
        print("  ✗ " + p)
    print("통과" if not probs else "문제 %d 건" % len(probs))
    return 0 if not probs else 1


def cmd_sample_request(a):
    from datetime import datetime
    from .features import Snapshot, build_state
    s = Snapshot(local_time=datetime(2026, 11, 27, 14, 10), temperature=31.2, humidity=71.0, lux=4800,
                 soil_moisture=28.0, led_pct=100, fan_pct=33, irrigations_today=2, minutes_since_irrigation=75,
                 temp_trend_30m=1.8, humidity_trend_30m=-0.5, max_data_age_s=4)
    body = {"state": build_state(s, load_profile(a.profile, a.crop)), "questions": load_questions(a.questions)}
    Path(a.out).write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    print("저장 %s" % a.out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m helpai_decide", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default=str(ROOT / "profiles" / "crops.json"), help="서버 crops.json 복사본")
    ap.add_argument("--crop", default="rose")
    ap.add_argument("--questions", default=str(ROOT / "configs" / "questions.yaml"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check-env").set_defaults(fn=cmd_check_env)

    p = sub.add_parser("download-base")
    p.add_argument("--out", default="models/base")
    p.add_argument("--repo", default="convaiinnovations/laya")
    p.add_argument("--subfolder", default="typed-decisions", help="'' = 영어 기본판(루트)")
    p.add_argument("--revision", default=None, help="재현성: 커밋 해시 고정 권장")
    p.set_defaults(fn=cmd_download_base)

    p = sub.add_parser("export-influx")
    p.add_argument("--start", required=True, help="2026-11-01 (KST 0시) 또는 ISO 시각")
    p.add_argument("--stop", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--url", default=os.environ.get("INFLUX_URL", "https://smartcubens.duckdns.org/influx"),
                   help="서버 InfluxDB 읽기 창구 (환경변수 INFLUX_URL 로도). 터널이면 http://127.0.0.1:8086")
    p.add_argument("--org", default="smartcube")
    p.add_argument("--bucket", default="sensors")
    p.add_argument("--zone-config", default=str(ROOT / "configs" / "replay.yaml"))
    p.set_defaults(fn=cmd_export_influx)

    p = sub.add_parser("dataset")
    s2 = p.add_subparsers(dest="sub", required=True)
    b = s2.add_parser("build")
    b.add_argument("--config", default=str(ROOT / "configs" / "dataset.yaml"))
    b.add_argument("--out", default="data/dataset")
    b.set_defaults(fn=cmd_dataset_build)
    c = s2.add_parser("check")
    c.add_argument("--data", required=True)
    c.set_defaults(fn=cmd_dataset_check)

    p = sub.add_parser("token-count")
    p.add_argument("--tokenizer", default="models/base/tokenizer")
    p.add_argument("--data", default="data/dataset/train.jsonl")
    p.add_argument("--limit", type=int, default=500)
    p.set_defaults(fn=cmd_token_count)

    p = sub.add_parser("train")
    p.add_argument("--config", default=str(ROOT / "configs" / "training.yaml"))
    p.add_argument("--data", default="data/dataset")
    p.add_argument("--base", default="models/base")
    p.add_argument("--out", required=True, help="예: runs/r1/model")
    p.add_argument("--device", default="auto")
    p.set_defaults(fn=cmd_train)

    p = sub.add_parser("predict")
    p.add_argument("--endpoint", help="http://127.0.0.1:8000 (edge/server.mjs · laya-serve)")
    p.add_argument("--key-env", default="LAYA_API_KEY", help="Bearer 키를 담은 환경변수 이름")
    p.add_argument("--model-dir", help="PyTorch 체크포인트 폴더 (laya 패키지로 직접)")
    p.add_argument("--device", default="cuda")
    p.add_argument("--data", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(fn=cmd_predict)

    p = sub.add_parser("calibrate")
    p.add_argument("--preds", required=True, help="val 셋 예측")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_calibrate)

    p = sub.add_parser("evaluate")
    p.add_argument("--preds", required=True, help="test 셋 예측")
    p.add_argument("--calibration")
    p.add_argument("--out", required=True)
    p.add_argument("--acc-min", type=float, default=0.9)
    p.add_argument("--ece-max", type=float, default=0.1)
    p.set_defaults(fn=cmd_evaluate)

    p = sub.add_parser("export-onnx")
    p.add_argument("--model-dir", required=True)
    p.add_argument("--exporter", default=str(Path.home() / "src" / "laya-onnx" / "export" / "export_onnx.py"))
    p.add_argument("--out", required=True)
    p.add_argument("--base", default="models/base", help="rl_common.py 를 가져올 기반 폴더")
    p.set_defaults(fn=cmd_export_onnx)

    p = sub.add_parser("convert")
    p.add_argument("--src", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--precision", choices=["int8", "fp16"], default="int8")
    p.set_defaults(fn=cmd_convert)

    p = sub.add_parser("compare")
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.add_argument("--min-agree", type=float, default=0.97)
    p.set_defaults(fn=cmd_compare)

    p = sub.add_parser("bundle")
    s3 = p.add_subparsers(dest="sub", required=True)
    m = s3.add_parser("make")
    m.add_argument("--onnx-dir", required=True)
    m.add_argument("--calibration", required=True)
    m.add_argument("--report", required=True)
    m.add_argument("--out", required=True, help="bundles/<revision>")
    m.add_argument("--revision", required=True, help="예: 2026-12-05_r1")
    m.add_argument("--precision", choices=["int8", "fp16", "fp32"], default="int8")
    m.add_argument("--base", default="convaiinnovations/laya (typed-decisions)")
    m.add_argument("--exporter-dir", default=None)
    m.add_argument("--dataset-info", default="data/dataset/dataset_info.json")
    m.add_argument("--notes", default="")
    m.set_defaults(fn=cmd_bundle_make)
    v = s3.add_parser("verify")
    v.add_argument("dir")
    v.set_defaults(fn=cmd_bundle_verify)

    p = sub.add_parser("sample-request")
    p.add_argument("--out", default=str(ROOT / "edge" / "sample_request.json"))
    p.set_defaults(fn=cmd_sample_request)

    a = ap.parse_args(argv)
    rc = a.fn(a)
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
