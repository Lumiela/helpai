"""꽃봉오리 등급 검출 모델(YOLOv11) 학습.

    pip install ultralytics
    python train_detector.py                       # 기본: yolo11n, 100 epoch
    python train_detector.py --model yolo11s.pt --epochs 150 --device 0
    python train_detector.py --data-root /content/dataset    # 데이터셋이 다른 폴더에 있을 때 (Colab)

학습 결과: runs/detect/rose_bud/weights/best.pt (configs/pipeline.yaml 의 detector.weights)
"""

import argparse
from pathlib import Path

from rose_pipeline.config import PROJECT_ROOT, load_yaml, save_yaml


def write_data_yaml(data_root=PROJECT_ROOT):
    """Roboflow data.yaml 의 '../train/images' 경로를 절대 경로로 바꾼 학습용 yaml 생성."""
    data_root = Path(data_root).resolve()
    src = load_yaml(data_root / "data.yaml")
    data = {
        "path": str(data_root),
        "train": "train/images",
        "val": "valid/images",
        "names": dict(enumerate(src["names"])),
    }
    if (data_root / "test").exists():  # test 분할이 없는 데이터셋도 있음
        data["test"] = "test/images"
    out = PROJECT_ROOT / "configs" / "rose_data.yaml"
    save_yaml(data, out, "# train_detector.py 가 data.yaml 에서 생성\n")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="yolo11n.pt", help="시작 가중치 (yolo11n/s/m/l/x.pt)")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default=None, help="0 (GPU) 또는 cpu")
    ap.add_argument("--name", default="rose_bud")
    ap.add_argument("--data-root", default=str(PROJECT_ROOT),
                    help="data.yaml 과 train/valid/test 폴더가 있는 위치")
    args = ap.parse_args()

    from ultralytics import YOLO

    data = write_data_yaml(args.data_root)
    model = YOLO(args.model)
    model.train(data=str(data), epochs=args.epochs, imgsz=args.imgsz, batch=args.batch,
                device=args.device, project=str(PROJECT_ROOT / "runs" / "detect"),
                name=args.name, exist_ok=True)
    split = "test" if (Path(args.data_root) / "test").exists() else "val"
    metrics = model.val(data=str(data), split=split)
    print(f"{split} mAP50: {metrics.box.map50:.3f}  mAP50-95: {metrics.box.map:.3f}")


if __name__ == "__main__":
    main()
