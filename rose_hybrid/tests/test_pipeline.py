"""합성 이미지로 파이프라인 전체(식물 유무 → 세그멘테이션 → 봉오리 할당 → 카메라 합치기) 검증.

트레이(구멍 3×4, 식물 체크무늬)를 정면으로 그린 뒤 CL(1~3열)·CR(2~4열) 시점으로 원근 변환한다.
실제 YOLO 대신 봉오리 색(등급별 색)으로 찾는 가짜 검출기를 쓴다.

    python tests/test_pipeline.py                 # 검증만
    python tests/test_pipeline.py --save out_dir  # 합성 이미지·시각화 저장
"""

import argparse
import copy
import sys
import warnings
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rose_pipeline import RosePipeline  # noqa: E402
from rose_pipeline.config import load_config  # noqa: E402
from rose_pipeline.detection import BudDetection, BudDetector  # noqa: E402
from rose_pipeline.visualize import imwrite, save_outputs  # noqa: E402

CELL = 300
IMG_W, IMG_H = 1600, 1200
# 카메라별: 정면 트레이에서 보는 영역 x 범위(px), 이미지 속 네 꼭짓점(정규화)
VIEWS = {
    "CL": ((0, 3 * CELL), np.float32([[0.10, 0.06], [0.92, 0.10], [0.86, 0.95], [0.04, 0.90]])),
    "CR": ((1 * CELL, 4 * CELL), np.float32([[0.08, 0.10], [0.90, 0.05], [0.96, 0.92], [0.14, 0.96]])),
}
BUD_HUE = {"grade_one": 20, "grade_two": 150, "grade_three": 4, "grade_four": 174}
PLANTS = ["R1C1", "R1C3", "R2C2", "R2C4", "R3C1", "R3C3"]

# 두 카메라에 모두 보이는 봉오리: 칸 → [(등급, 칸 안 x, 칸 안 y)]
BUDS = {
    "R1C1": [("grade_two", 150, 120)],
    "R1C3": [("grade_one", 110, 130), ("grade_three", 190, 170)],
    "R2C2": [],
    "R2C4": [("grade_four", 150, 150)],
    "R3C1": [("grade_one", 150, 150)],
    "R3C3": [("grade_one", 120, 150)],
}
# 한 카메라에서만 보이는 봉오리 (다른 카메라에선 잎에 가려졌다고 가정)
BUDS_ONLY = {"CR": {"R3C3": [("grade_three", 190, 120)]}}

EXPECTED = [
    ["grade_two", "-", "grade_three", "-"],
    ["-", "grade_zero", "-", "grade_four"],
    ["grade_one", "-", "grade_three", "-"],   # R3C3: CL=one, CR=three → max
]


def hsv_to_bgr(h, s=230, v=220):
    return tuple(int(c) for c in cv2.cvtColor(np.uint8([[[h, s, v]]]), cv2.COLOR_HSV2BGR)[0, 0])


def draw_tray(rng, extra_buds):
    flat = np.full((3 * CELL, 4 * CELL, 3), (120, 125, 130), np.uint8)
    for r in range(3):
        for c in range(4):
            cid, ox, oy = f"R{r + 1}C{c + 1}", c * CELL, r * CELL
            cv2.circle(flat, (ox + 150, oy + 150), 110, (25, 25, 28), -1)  # 화분(검은 덮개)
            if cid not in PLANTS:
                continue
            cv2.ellipse(flat, (ox + 150, oy + 150), (100, 90), 0, 0, 360, (40, 150, 50), -1)
            for _ in range(8):
                cx, cy = ox + 150 + rng.integers(-80, 81), oy + 150 + rng.integers(-80, 81)
                cv2.ellipse(flat, (int(cx), int(cy)), (int(rng.integers(35, 60)), int(rng.integers(25, 45))),
                            float(rng.integers(0, 180)), 0, 360, (40, 150, 50), -1)
            for grade, bx, by in BUDS[cid] + extra_buds.get(cid, []):
                cv2.circle(flat, (ox + bx, oy + by), 14, hsv_to_bgr(BUD_HUE[grade]), -1)
    return flat


def make_view(flat, x_range, corners):
    x0, x1 = x_range
    src = np.float32([[x0, 0], [x1, 0], [x1, 3 * CELL], [x0, 3 * CELL]])
    H = cv2.getPerspectiveTransform(src, corners * np.float32([IMG_W, IMG_H]))
    img = cv2.warpPerspective(flat, H, (IMG_W, IMG_H), borderValue=(200, 200, 200))
    # 사진 밖 구멍도 좌표를 넣음 (0~1 범위 밖) → 가장자리에 걸친 이웃 식물 잎을 그 식물로 배정
    holes = {}
    for r in range(3):
        for c in range(4):
            p = cv2.perspectiveTransform(np.float32([[[c * CELL + 150, r * CELL + 150]]]), H)[0, 0]
            holes[f"R{r + 1}C{c + 1}"] = (p / np.float32([IMG_W, IMG_H])).round(4).tolist()
    return img, holes


class ColorBudDetector(BudDetector):
    """등급별 봉오리 색을 찾아 박스로 반환하는 가짜 검출기."""

    def detect(self, image):
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        dets = []
        for grade, hue in BUD_HUE.items():
            m = cv2.inRange(hsv, (max(hue - 3, 0), 180, 150), (min(hue + 3, 179), 255, 255))
            n, _, stats, _ = cv2.connectedComponentsWithStats(m)
            for x, y, w, h, area in stats[1:]:
                if area >= 80:
                    dets.append(BudDetection(grade, 0.9, np.float32([x, y, x + w, y + h])))
        return dets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", help="합성 이미지와 결과를 저장할 폴더")
    args = ap.parse_args()

    cfg = copy.deepcopy(load_config())
    # 합성 시점(CL = 1~3열, CR = 2~4열)에 맞춘 범위표. 실제 설정의 범위표와 무관하게 고정
    cfg["layout"] = {**cfg["layout"], "coverage": [["CL,CR", "CL,CR", "CL,CR", "CR"],
                                                   ["CL,CR", "CL,CR", "CL,CR", "CR"],
                                                   ["CL", "CL,CR", "CL,CR", "CR"]]}
    images = {}
    for name, (x_range, corners) in VIEWS.items():
        flat = draw_tray(np.random.default_rng(0), BUDS_ONLY.get(name, {}))
        images[name], cfg["holes"][name] = make_view(flat, x_range, corners)

    failures = []
    for rule in ("center_in_mask", "max_overlap"):
        for detect_on in ("plant_crop", "full_image"):
            tag = f"{rule}/{detect_on}"
            cfg["grading"]["assignment"] = rule
            cfg["grading"]["detector"]["detect_on"] = detect_on
            result = RosePipeline(cfg, detector=ColorBudDetector()).run(images)
            print(f"[{tag}]\n{result.table()}\n")
            for r, (got_row, exp_row) in enumerate(zip(result.grid(), EXPECTED), 1):
                for c, (g, e) in enumerate(zip(got_row, exp_row), 1):
                    if g != e:
                        failures.append(f"{tag} R{r}C{c}: 기대 {e}, 결과 {g}")
            r3c3 = next(c for c in result.cells if c.cell_id == "R3C3")
            if r3c3.camera_grades != {"CL": "grade_one", "CR": "grade_three"}:
                failures.append(f"{tag} R3C3 카메라별 등급: {r3c3.camera_grades}")
            if args.save:
                for name, img in images.items():
                    imwrite(Path(args.save) / f"{name}.png", img)
                print("저장:", save_outputs(result, Path(args.save) / rule / detect_on))

    # 식물 위치 고정(fixed): 표대로 판단하고, 사진 판단과 다른 칸은 경고
    cfg["grading"]["assignment"], cfg["grading"]["detector"]["detect_on"] = "center_in_mask", "plant_crop"
    cfg["presence"] = {**cfg["presence"], "method": "fixed"}
    cfg["layout"] = {**cfg["layout"], "plants": [["O", "O", "O", "X"], ["X", "O", "X", "O"], ["O", "X", "O", "X"]]}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = RosePipeline(cfg, detector=ColorBudDetector()).run(images)
    print(f"[fixed: R1C2 를 일부러 O 로 설정]\n{result.table()}\n")
    expected = [row[:] for row in EXPECTED]
    expected[0][1] = "grade_zero"
    if result.grid() != expected:
        failures.append(f"fixed 결과: {result.grid()}")
    if not any("R1C2" in str(w.message) for w in caught):
        failures.append("fixed: R1C2 불일치 경고가 없음")

    if failures:
        print("실패:\n  " + "\n  ".join(failures))
        sys.exit(1)
    print("통과: 3×4 모든 칸이 기대 결과와 일치")


if __name__ == "__main__":
    main()
