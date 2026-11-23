"""등급 분류기 비교: 정답이 있는 크롭 폴더(<data>/cls/<split>/<클래스>/*.jpg)로 YOLO 분류 / VLM / cascade 를 평가.

    python tools/eval_classifier.py --backend yolo vlm --per-class 30
    python tools/eval_classifier.py --backend cascade --per-class 30 --data /content/hybrid_data

- 혼동 행렬, 정확도, 클래스별 재현율·정밀도, cls_conf 기준값별 결과(버림 비율 포함)를 출력합니다.
- VLM 은 크롭마다 요금이 들므로 --per-class 로 클래스마다 몇 장만 뽑아 씁니다 (같은 --seed 면 같은 크롭).
- 크롭별 예측은 output/eval_cls_<시각>/predictions.csv 에 저장됩니다.
- VLM 예시 사진(vlm.examples_dir)이 평가 크롭과 겹치지 않게, 예시는 train 크롭에서 고르세요.
"""

import argparse
import csv
import random
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rose_pipeline.config import PROJECT_ROOT, load_config, resolve  # noqa: E402
from rose_pipeline.vlm import CLASSES, IMG_EXT, build_classifier, imread  # noqa: E402

CONF_STEPS = (0.0, 0.3, 0.5, 0.7, 0.9)


def sample_crops(root, per_class, seed):
    rng = random.Random(seed)
    items = []
    for c in CLASSES:
        d = root / c
        files = sorted(p for p in d.glob("*") if p.suffix.lower() in IMG_EXT) if d.is_dir() else []
        if not files:
            print(f"  (경고) {d} 에 크롭이 없습니다")
        if per_class:
            files = rng.sample(files, min(per_class, len(files)))
        items += [(p, c) for p in files]
    return items


def report(name, truth, preds):
    labels = CLASSES
    idx = {c: i for i, c in enumerate(labels)}
    m = [[0] * len(labels) for _ in labels]
    for t, (p, _) in zip(truth, preds):
        m[idx[t]][idx.get(p, 0)] += 1
    w = max(len(c) for c in labels) + 2
    print(f"\n===== {name} =====  (행 = 정답, 열 = 예측)")
    print(" " * w + "".join(f"{c[:10]:>11}" for c in labels) + f"{'재현율':>9}")
    for i, c in enumerate(labels):
        n = sum(m[i])
        print(f"{c:<{w}}" + "".join(f"{v:>11}" for v in m[i]) + (f"{m[i][i] / n:>10.2f}" if n else f"{'-':>10}"))
    prec = [m[j][j] / s if (s := sum(m[i][j] for i in range(len(labels)))) else None for j in range(len(labels))]
    print(f"{'정밀도':<{w - 2}}" + "".join(f"{v:>11.2f}" if v is not None else f"{'-':>11}" for v in prec))
    acc = sum(m[i][i] for i in range(len(labels))) / max(len(truth), 1)
    print(f"정확도 {acc:.3f}  (n={len(truth)})")
    print("grade_one ↔ not_bud 혼동: "
          f"G1→not_bud {m[idx['grade_one']][idx['not_bud']]}, not_bud→G1 {m[idx['not_bud']][idx['grade_one']]}")

    # 파이프라인 규칙: not_bud 이거나 확신 < cls_conf 면 버림 → 봉오리만 놓고 본 결과
    print("cls_conf 별 (봉오리로 남긴 것 기준): 기준 | 남긴 수 | 그중 정답 | 놓친 봉오리 | 남은 not_bud")
    buds = sum(t != "not_bud" for t in truth)
    for th in CONF_STEPS:
        kept = [(t, p) for t, (p, c) in zip(truth, preds) if p != "not_bud" and c >= th]
        right = sum(t == p for t, p in kept)
        fp = sum(t == "not_bud" for t, _ in kept)
        missed = buds - sum(t != "not_bud" for t, _ in kept)
        print(f"  {th:.1f} | {len(kept):>5} | {right:>5} | {missed:>5} | {fp:>5}")
    return acc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(PROJECT_ROOT / "hybrid_data"), help="prepare_hybrid_data.py 의 --out")
    ap.add_argument("--split", default="val")
    ap.add_argument("--backend", nargs="+", default=["yolo", "vlm"], choices=["yolo", "vlm", "cascade"])
    ap.add_argument("--per-class", type=int, default=30, help="클래스마다 뽑을 크롭 수 (0 = 전부. VLM 은 비용 주의)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--config", default="configs/pipeline.yaml")
    ap.add_argument("--device", default=None)
    args = ap.parse_args()

    cfg = load_config(args.config)
    det_cfg = cfg["grading"]["detector"]
    root = Path(args.data) / "cls" / args.split
    items = sample_crops(root, args.per_class, args.seed)
    if not items:
        sys.exit(f"평가할 크롭이 없습니다: {root}")
    crops = [imread(p) for p, _ in items]
    truth = [c for _, c in items]
    print(f"평가 크롭 {len(items)}장 ({root})")

    out = resolve(cfg["output"]["dir"]) / f"eval_cls_{datetime.now():%Y%m%d_%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    all_preds = {}
    for backend in args.backend:
        cls_model = None
        if backend in ("yolo", "cascade"):
            from ultralytics import YOLO
            w = resolve(det_cfg["cls_weights"])
            if not w.exists():
                print(f"\n[{backend}] 건너뜀: 분류 가중치가 없습니다 ({w}). python train_hybrid.py cls 로 학습하세요.")
                continue
            cls_model = YOLO(str(w))
        clf = build_classifier(backend, cls_model, det_cfg.get("vlm"), det_cfg.get("cls_imgsz", 224),
                               args.device, det_cfg.get("cascade_below", 0.8))
        t0 = time.time()
        preds = clf.classify(crops)
        dt = time.time() - t0
        report(backend, truth, preds)
        print(f"걸린 시간 {dt:.1f}s ({dt / len(crops) * 1000:.0f} ms/크롭)")
        vlm = getattr(clf, "second", clf)
        if hasattr(vlm, "usage"):
            print(f"VLM 사용량: {vlm.usage}")
        all_preds[backend] = preds

    with open(out / "predictions.csv", "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.writer(f)
        wr.writerow(["file", "truth"] + [f"{b}_{k}" for b in all_preds for k in ("pred", "conf")])
        for i, (p, t) in enumerate(items):
            wr.writerow([str(p), t] + [v for b in all_preds for v in (all_preds[b][i][0], round(all_preds[b][i][1], 3))])
    print(f"\n크롭별 예측: {out / 'predictions.csv'}")


if __name__ == "__main__":
    main()
