"""혼합형 모델 학습: 봉오리 1클래스 검출(det) + 등급 분류(cls).

먼저 tools/prepare_hybrid_data.py 로 학습 데이터(<data>/det, <data>/cls)를 만든다.

    python train_hybrid.py det --data /content/hybrid_data --device 0     # → runs/detect/rose_bud1/weights/best.pt
    python train_hybrid.py cls --data /content/hybrid_data --device 0     # → runs/classify/rose_grade/weights/best.pt
    python train_hybrid.py det --model yolo11s.pt --epochs 150 ...        # 더 큰 모델

결과 경로는 configs/pipeline.yaml 의 detector.weights / detector.cls_weights 와 같다.
FastSAM 은 학습하지 않는다 (공개 가중치 FastSAM-s.pt 를 그대로 사용).
"""

import argparse
from pathlib import Path

from rose_pipeline.config import PROJECT_ROOT


def train_det(args):
    from ultralytics import YOLO

    data = Path(args.data) / "det" / "data.yaml"
    model = YOLO(args.model or "yolo11n.pt")
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
    model = YOLO(args.model or "yolo11n-cls.pt")
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
    ap.add_argument("--model", help="시작 가중치 (기본: det=yolo11n.pt, cls=yolo11n-cls.pt)")
    ap.add_argument("--epochs", type=int, help="기본: det=100, cls=50")
    ap.add_argument("--imgsz", type=int, help="기본: det=640, cls=224")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default=None, help="0 (GPU) 또는 cpu")
    ap.add_argument("--name", help="결과 폴더 이름 (기본: det=rose_bud1, cls=rose_grade)")
    args = ap.parse_args()
    (train_det if args.task == "det" else train_cls)(args)


if __name__ == "__main__":
    main()
