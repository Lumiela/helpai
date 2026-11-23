"""VLM 예시 사진 폴더 만들기: 학습 크롭(<data>/cls/train/<클래스>/)에서 클래스마다 몇 장을 복사.

    python tools/make_vlm_examples.py --data hybrid_data --per-class 4

결과 폴더(기본 vlm_examples/)를 열어 등급을 잘 보여주는 사진만 남기고 나머지는 지우세요.
VLM 은 실행할 때 이 폴더에서 클래스마다 vlm.examples_per_class 장을 골라 예시로 씁니다.
평가(tools/eval_classifier.py)는 val 크롭으로 하므로 예시와 겹치지 않습니다.
"""

import argparse
import random
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rose_pipeline.config import PROJECT_ROOT  # noqa: E402
from rose_pipeline.vlm import CLASSES, IMG_EXT  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(PROJECT_ROOT / "hybrid_data"))
    ap.add_argument("--out", default=str(PROJECT_ROOT / "vlm_examples"))
    ap.add_argument("--per-class", type=int, default=4)
    ap.add_argument("--min-side", type=int, default=48, help="이보다 작은 크롭은 흐려서 예시로 쓰지 않음")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    import cv2
    import numpy as np

    rng = random.Random(args.seed)
    src, out = Path(args.data) / "cls" / "train", Path(args.out)
    for c in CLASSES:
        files = sorted(p for p in (src / c).glob("*") if p.suffix.lower() in IMG_EXT) if (src / c).is_dir() else []
        rng.shuffle(files)
        picked = []
        for p in files:
            img = cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR)
            if img is not None and min(img.shape[:2]) >= args.min_side:
                picked.append(p)
            if len(picked) >= args.per_class:
                break
        (out / c).mkdir(parents=True, exist_ok=True)
        for p in picked:
            shutil.copy2(p, out / c / p.name)
        print(f"{c}: {len(picked)}장")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
