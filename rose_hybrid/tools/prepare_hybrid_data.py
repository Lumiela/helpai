"""혼합형 학습 데이터 만들기. Roboflow 형식 데이터셋(data.yaml + train/valid/test)에서 두 가지를 만든다.

  <out>/det/  봉오리 1클래스 검출용. 이미지는 그대로(하드링크, 안 되면 복사), 라벨의 클래스 번호만 모두 0(bud)
  <out>/cls/  등급 분류용 크롭. train/val/test × grade_one~four + not_bud

    python tools/prepare_hybrid_data.py --data-root /content/dataset --out /content/hybrid_data
    python tools/prepare_hybrid_data.py ... --extra-cls extra_cls   # 직접 고른 크롭 추가 (extra_cls/<클래스>/*.jpg → train)

- 분할은 원래 데이터셋을 그대로 따른다 (원본 사진 단위로 나뉘어 있어 크롭도 섞이지 않음).
- 크롭 모양은 추론과 같은 square_box(박스 중심 정사각형, 여백 --pad)를 쓴다. 설정의 detector.crop_pad 와 맞출 것.
- not_bud 는 같은 이미지에서 봉오리 박스와 겹치지 않는 곳을 봉오리 크기로 무작위로 잘라 만든다.
  실제 트레이 사진의 오탐지(반사판·케이블 등)는 tools/mine_crops.py 로 모아 --extra-cls 로 넣는다.
- 분류 크롭 중복 제거: 데이터 폴더에 *tiles_manifest.csv (타일 → 원본 사진 위치)가 있으면, 타일 박스를 원본 사진
  좌표로 되돌려 같은 봉오리는 가장 크게(온전히) 보이는 타일에서 한 번만 자른다. 겹치는 타일과 copy-paste 합성
  사진(cp_<원본>_<번호>, 바탕 = 원본 사진)에 반복해 들어간 봉오리를 한 번만 세기 위함. 검출 데이터(det)는 그대로.
"""

import argparse
import csv
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rose_pipeline.config import PROJECT_ROOT, load_yaml, save_yaml  # noqa: E402
from rose_pipeline.detection import NOT_BUD, square_box  # noqa: E402
from rose_pipeline.visualize import imwrite  # noqa: E402

SPLITS = {"train": "train", "valid": "val", "test": "test"}  # 원본 폴더 → 분류 데이터 폴더 (ultralytics 이름)
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
MARKER = ".made_by_prepare_hybrid_data"


def imread(path):
    """한글 경로도 읽히도록 imdecode 사용."""
    return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)


def link_or_copy(src, dst):
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def read_labels(path):
    """YOLO 라벨 → [(클래스, cx, cy, w, h)] (정규화). 다각형 라벨은 감싸는 박스로 바꿈."""
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        v = line.split()
        if len(v) == 5:
            rows.append((int(v[0]), *map(float, v[1:])))
        elif len(v) > 5:
            pts = np.float32(v[1:]).reshape(-1, 2)
            (x1, y1), (x2, y2) = pts.min(0), pts.max(0)
            rows.append((int(v[0]), (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1))
    return rows


def overlaps(box, boxes, thr=0.1):
    """box 가 boxes 중 하나와 (작은 쪽 면적 기준) thr 이상 겹치는지."""
    for b in boxes:
        iw = min(box[2], b[2]) - max(box[0], b[0])
        ih = min(box[3], b[3]) - max(box[1], b[1])
        if iw > 0 and ih > 0:
            small = min((box[2] - box[0]) * (box[3] - box[1]), (b[2] - b[0]) * (b[3] - b[1]))
            if iw * ih >= thr * small:
                return True
    return False


def sample_negatives(rng, n, sizes, boxes, w, h, pad, tries=30):
    out = []
    for _ in range(n):
        for _ in range(tries):
            bw, bh = sizes[rng.integers(len(sizes))]
            if bw >= w or bh >= h:
                continue
            x, y = rng.uniform(0, w - bw), rng.uniform(0, h - bh)
            cand = (x, y, x + bw, y + bh)
            # 정사각형으로 잘랐을 때도 봉오리가 들어오지 않게 실제 크롭 범위로 검사
            if not overlaps(square_box(cand, pad, w, h), boxes + out):
                out.append(cand)
                break
    return out


def dedupe_plan(root, splits, iou_thr=0.5):
    """분류 크롭으로 쓸 박스. 반환: {타일 이미지 stem: 남길 박스 번호 set}. manifest 에 없는 이미지는 들어 있지 않음(전부 사용).
    같은 원본 사진·같은 클래스에서 원본 좌표 IoU 가 iou_thr 이상인 박스는 같은 봉오리로 보고 가장 큰 것 하나만 남김
    (타일 경계에서 잘린 조각보다 온전한 박스가 큼)."""
    tiles = {}
    for m in sorted(root.glob("*tiles_manifest.csv")):
        for r in csv.DictReader(open(m, encoding="utf-8-sig")):
            tiles[Path(r["tile_image"]).stem] = r
    groups = defaultdict(list)
    for split in splits:
        for lab in sorted((root / split / "labels").glob("*.txt")):
            r = tiles.get(lab.stem)
            if r is None:
                continue
            base = Path(r["source_image"]).stem
            base = re.sub(r"^cp_(.+)_\d+$", r"\1", base)  # 합성 사진의 바탕 = 원본 사진
            tx, ty, tw, th = (float(r[k]) for k in ("x", "y", "w", "h"))
            for k, (c, cx, cy, bw, bh) in enumerate(read_labels(lab)):
                box = (tx + (cx - bw / 2) * tw, ty + (cy - bh / 2) * th, tx + (cx + bw / 2) * tw, ty + (cy + bh / 2) * th)
                groups[(split, base, c)].append((bw * tw * bh * th, box, lab.stem, k))
    keep = {stem: set() for stem in tiles}
    for items in groups.values():
        kept = []
        for _, box, stem, k in sorted(items, key=lambda t: -t[0]):
            b = np.float32(box)
            if kept:
                K = np.float32(kept)
                iw = np.clip(np.minimum(K[:, 2], b[2]) - np.maximum(K[:, 0], b[0]), 0, None)
                ih = np.clip(np.minimum(K[:, 3], b[3]) - np.maximum(K[:, 1], b[1]), 0, None)
                inter = iw * ih
                union = (K[:, 2] - K[:, 0]) * (K[:, 3] - K[:, 1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
                if (inter / union).max() >= iou_thr:
                    continue
            kept.append(box)
            keep[stem].add(k)
    return keep


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", default=str(PROJECT_ROOT), help="data.yaml 과 train/valid/test 가 있는 폴더")
    ap.add_argument("--out", default=str(PROJECT_ROOT / "hybrid_data"))
    ap.add_argument("--pad", type=float, default=0.15, help="분류 크롭 여백 (설정의 detector.crop_pad 와 같게)")
    ap.add_argument("--neg-ratio", type=float, default=1.0, help="봉오리 크롭 1개당 not_bud 크롭 수")
    ap.add_argument("--min-side", type=int, default=8, help="이보다 작은 박스(px)는 분류 크롭에서 제외")
    ap.add_argument("--no-dedupe", action="store_true", help="분류 크롭 중복 제거 안 함 (타일마다 모든 박스를 자름)")
    ap.add_argument("--extra-cls", help="추가 분류 크롭 폴더 (<폴더>/<클래스>/*.jpg → cls/train/<클래스>)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    root, out = Path(args.data_root).resolve(), Path(args.out).resolve()
    names = load_yaml(root / "data.yaml")["names"]
    names = [names[k] for k in sorted(names)] if isinstance(names, dict) else list(names)
    if out.exists():
        if not (out / MARKER).exists():
            sys.exit(f"{out} 가 이 스크립트로 만든 폴더가 아니라서 덮어쓰지 않습니다.")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / MARKER).write_text("tools/prepare_hybrid_data.py 가 생성한 폴더입니다.\n", encoding="utf-8")

    rng = np.random.default_rng(args.seed)
    keep = {} if args.no_dedupe else dedupe_plan(root, [s for s in SPLITS if (root / s / "labels").exists()])
    if keep:
        print(f"분류 크롭 중복 제거: manifest 타일 {len(keep)}장 기준")
    stats = {}
    for split, cls_split in SPLITS.items():
        img_dir, lab_dir = root / split / "images", root / split / "labels"
        if not img_dir.exists():
            continue
        det_img, det_lab = out / "det" / split / "images", out / "det" / split / "labels"
        det_img.mkdir(parents=True)
        det_lab.mkdir(parents=True)
        cls_dir = out / "cls" / cls_split
        count = Counter()
        for img_path in sorted(p for p in img_dir.iterdir() if p.suffix.lower() in IMG_EXT):
            rows = read_labels(lab_dir / f"{img_path.stem}.txt")
            link_or_copy(img_path, det_img / img_path.name)
            (det_lab / f"{img_path.stem}.txt").write_text(
                "".join(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n" for _, cx, cy, bw, bh in rows), encoding="utf-8")
            count["det_images"] += 1
            count["det_boxes"] += len(rows)

            image = imread(img_path)
            if image is None:
                print("읽기 실패:", img_path)
                continue
            h, w = image.shape[:2]
            boxes = [((cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h)
                     for _, cx, cy, bw, bh in rows]
            use = keep.get(img_path.stem)  # None = manifest 에 없는 이미지 → 모든 박스 사용
            n_used = 0
            for k, ((c, *_), box) in enumerate(zip(rows, boxes)):
                if use is not None and k not in use:
                    count["skip_dup"] += 1
                    continue
                if min(box[2] - box[0], box[3] - box[1]) < args.min_side:
                    count["skip_small"] += 1
                    continue
                x1, y1, x2, y2 = square_box(box, args.pad, w, h)
                imwrite(cls_dir / names[c] / f"{img_path.stem}_{k:02d}.jpg", image[y1:y2, x1:x2])
                count[names[c]] += 1
                n_used += 1

            sizes = [(b[2] - b[0], b[3] - b[1]) for b in boxes] or [(w * 0.1, h * 0.1)]
            n_neg = round(n_used * args.neg_ratio) if boxes else 1  # 봉오리 크롭 수만큼 (중복으로 뺀 박스는 세지 않음)
            for k, box in enumerate(sample_negatives(rng, n_neg, sizes, boxes, w, h, args.pad)):
                x1, y1, x2, y2 = square_box(box, args.pad, w, h)
                imwrite(cls_dir / NOT_BUD / f"{img_path.stem}_n{k:02d}.jpg", image[y1:y2, x1:x2])
                count[NOT_BUD] += 1
        stats[split] = count

    if args.extra_cls:
        for d in sorted(p for p in Path(args.extra_cls).iterdir() if p.is_dir()):
            dst = out / "cls" / "train" / d.name
            dst.mkdir(parents=True, exist_ok=True)
            files = [p for p in d.iterdir() if p.suffix.lower() in IMG_EXT]
            for p in files:
                shutil.copy2(p, dst / f"extra_{p.name}")
            stats.setdefault("extra", Counter())[d.name] += len(files)

    det_yaml = {"path": str(out / "det"), "train": "train/images", "val": "valid/images"}
    if (out / "det" / "test").exists():  # test 분할이 없는 데이터셋도 있음
        det_yaml["test"] = "test/images"
    save_yaml({**det_yaml, "names": {0: "bud"}}, out / "det" / "data.yaml", "# prepare_hybrid_data.py 가 생성 (봉오리 1클래스)\n")

    classes = sorted(set(names) | {NOT_BUD})
    print(f"{'분할':6s} {'검출 이미지':>8s} {'박스':>6s}  " + "  ".join(f"{c:>11s}" for c in classes))
    for split, c in stats.items():
        print(f"{split:6s} {c['det_images']:>8d} {c['det_boxes']:>6d}  " + "  ".join(f"{c[k]:>11d}" for k in classes)
              + (f"   (중복 제외 {c['skip_dup']})" if c['skip_dup'] else ""))
    print(f"\n검출 데이터: {out / 'det' / 'data.yaml'}\n분류 데이터: {out / 'cls'}")


if __name__ == "__main__":
    main()
