"""실제 트레이 사진에서 봉오리 후보 크롭을 모은다 (분류 모델 보강용).

1클래스 검출기를 낮은 신뢰도로 돌려 나온 박스를 분류 학습과 같은 모양(square_box)으로 잘라 저장한다.
분류 모델이 있으면 예측 클래스 폴더에 미리 나눠 담는다.

    python tools/mine_crops.py input/*.jpg --out mined
    → mined/<예측 클래스>/*.jpg 를 눈으로 보고 틀린 것은 맞는 폴더로 옮김 (반사판·케이블·잎 → not_bud)
    → 폴더째 extra_cls/ 로 옮기고 prepare_hybrid_data.py --extra-cls extra_cls 로 데이터 재생성 → 분류 모델 재학습
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rose_pipeline.config import load_config, resolve  # noqa: E402
from rose_pipeline.detection import square_box  # noqa: E402
from rose_pipeline.visualize import imwrite  # noqa: E402

from prepare_hybrid_data import imread  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+")
    ap.add_argument("--out", default="mined")
    ap.add_argument("--conf", type=float, default=0.05, help="후보를 넓게 모으도록 낮게")
    ap.add_argument("--device", default=None)
    args = ap.parse_args()

    from ultralytics import YOLO

    det_cfg = load_config()["grading"]["detector"]
    det = YOLO(str(resolve(det_cfg["weights"])))
    cls_path = resolve(det_cfg.get("cls_weights", "runs/classify/rose_grade/weights/best.pt"))
    clf = YOLO(str(cls_path)) if cls_path.exists() else None
    pad = det_cfg.get("crop_pad", 0.15)

    n = 0
    for path in args.images:
        image = imread(path)
        if image is None:
            print("읽기 실패:", path)
            continue
        h, w = image.shape[:2]
        r = det.predict(image, conf=args.conf, imgsz=det_cfg.get("imgsz", 640), device=args.device, verbose=False)[0]
        for k, (box, c) in enumerate(zip(r.boxes.xyxy.cpu().numpy(), r.boxes.conf.cpu().numpy())):
            x1, y1, x2, y2 = square_box(box, pad, w, h)
            crop = image[y1:y2, x1:x2]
            folder = "unsorted"
            if clf is not None:
                res = clf.predict(crop, imgsz=det_cfg.get("cls_imgsz", 224), device=args.device, verbose=False)[0]
                folder = res.names[int(res.probs.top1)]
            imwrite(Path(args.out) / folder / f"{Path(path).stem}_{k:03d}_d{c:.2f}.jpg", crop)
            n += 1
    print(f"크롭 {n}개 저장: {Path(args.out).resolve()}")


if __name__ == "__main__":
    main()
