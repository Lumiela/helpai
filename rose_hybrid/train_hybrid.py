"""혼합형 모델 학습: 봉오리 1클래스 검출(det) + 등급 분류(cls).

먼저 tools/prepare_hybrid_data.py 로 학습 데이터(<data>/det, <data>/cls)를 만든다.

    python train_hybrid.py det --data /content/hybrid_data --device 0     # → runs/detect/rose_bud1/weights/best.pt
    python train_hybrid.py cls --data /content/hybrid_data --device 0     # → runs/classify/rose_grade/weights/best.pt
    python train_hybrid.py det --model yolov8l.pt --epochs 150 ...        # 다른 시작 가중치

시작 가중치 기본값은 configs/pipeline.yaml 의 detector.det_base (weights/yolov8m.pt) /
detector.cls_base (weights/yolo26m-cls.pt). 파일이 없으면 ultralytics 가 같은 이름으로 내려받는다.
결과 경로는 configs/pipeline.yaml 의 detector.weights / detector.cls_weights 와 같다.
FastSAM 은 학습하지 않는다 (공개 가중치 FastSAM-s.pt 를 그대로 사용).
"""

import argparse
from pathlib import Path

from rose_pipeline.config import PROJECT_ROOT, load_yaml, resolve

DEFAULT_BASE = {"det": "weights/yolov8m.pt", "cls": "weights/yolo26m-cls.pt"}


def base_model(task, given=None):
    """시작 가중치. --model > 설정의 det_base / cls_base > 기본값.
    프로젝트 안에 파일이 있으면 그 경로, 없으면 파일 이름만 넘겨 ultralytics 가 내려받게 함."""
    if not given:
        det_cfg = load_yaml(PROJECT_ROOT / "configs" / "pipeline.yaml").get("grading", {}).get("detector", {})
        given = det_cfg.get(f"{task}_base") or DEFAULT_BASE[task]
    p = resolve(given)
    return str(p) if p.exists() else Path(given).name


def train_det(args):
    from ultralytics import YOLO

    data = Path(args.data) / "det" / "data.yaml"
    model = YOLO(base_model("det", args.model))
    model.train(data=str(data), epochs=args.epochs or 100, imgsz=args.imgsz or 640, batch=args.batch,
                device=args.device, project=str(PROJECT_ROOT / "runs" / "detect"), name=args.name or "rose_bud1",
                exist_ok=True)
    split = "test" if (Path(args.data) / "det" / "test").exists() else "val"   # test 분할이 없는 데이터셋은 val
    m = model.val(data=str(data), split=split)
    print(f"[봉오리 위치] {split} mAP50: {m.box.map50:.3f}  mAP50-95: {m.box.map:.3f}  "
          f"precision: {m.box.mp:.3f}  recall: {m.box.mr:.3f}")


def train_cls(args):
    from ultralytics import YOLO

    data = Path(args.data) / "cls"
    model = YOLO(base_model("cls", args.model))
    model.train(data=str(data), epochs=args.epochs or 50, imgsz=args.imgsz or 224, batch=args.batch,
                device=args.device, project=str(PROJECT_ROOT / "runs" / "classify"), name=args.name or "rose_grade",
                exist_ok=True)
    split = "test" if (data / "test").exists() else "val"
    m = model.val(data=str(data), split=split)
    print(f"[등급 분류] {split} top1 정확도: {m.top1:.3f}  (혼동 행렬: runs/classify/{args.name or 'rose_grade'}/)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task", choices=["det", "cls"], help="det = 봉오리 위치 검출, cls = 등급 분류")
    ap.add_argument("--data", default=str(PROJECT_ROOT / "hybrid_data"), help="prepare_hybrid_data.py 의 --out")
    ap.add_argument("--model", help="시작 가중치 (기본: det=weights/yolov8m.pt, cls=weights/yolo26m-cls.pt)")
    ap.add_argument("--epochs", type=int, help="기본: det=100, cls=50")
    ap.add_argument("--imgsz", type=int, help="기본: det=640, cls=224")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default=None, help="0 (GPU) 또는 cpu")
    ap.add_argument("--name", help="결과 폴더 이름 (기본: det=rose_bud1, cls=rose_grade)")
    args = ap.parse_args()
    (train_det if args.task == "det" else train_cls)(args)


if __name__ == "__main__":
    main()
