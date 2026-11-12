"""혼합형 검출기(HybridBudDetector) 검증 — 모델 없이 가짜 det / FastSAM / 분류 모델로 확인.

1) 단위: 박스 → 마스크 매칭, not_bud·낮은 확신 버림, 마스크 기준 식물 배정
2) 전체: test_pipeline.py 의 합성 트레이로 3×4 결과가 기대값과 같은지 (FastSAM 있음/없음 × plant_crop/full_image)

    python tests/test_hybrid.py
"""

import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rose_pipeline import RosePipeline  # noqa: E402
from rose_pipeline.assignment import mask_overlap  # noqa: E402
from rose_pipeline.config import load_config  # noqa: E402
from rose_pipeline.detection import NOT_BUD, BudDetection, HybridBudDetector  # noqa: E402
from rose_pipeline.layout import Hole  # noqa: E402
from rose_pipeline.segmentation import PlantSegment  # noqa: E402
from rose_pipeline.visualize import draw_board  # noqa: E402
from test_pipeline import BUD_HUE, BUDS_ONLY, EXPECTED, VIEWS, draw_tray, make_view  # noqa: E402

CLS_NAMES = {0: "grade_four", 1: "grade_one", 2: "grade_three", 3: "grade_two", 4: NOT_BUD}
CLS_INDEX = {v: k for k, v in CLS_NAMES.items()}


class T:
    """torch 텐서 흉내 (.cpu().numpy())."""

    def __init__(self, a):
        self.a = np.asarray(a)

    def cpu(self):
        return self

    def numpy(self):
        return self.a


class FakeDet:
    def __init__(self, fn):
        self.fn = fn  # image -> [(x1, y1, x2, y2, conf)]

    def predict(self, image, **_):
        b = np.float32(self.fn(image)).reshape(-1, 5)
        return [SimpleNamespace(names={0: "bud"},
                                boxes=SimpleNamespace(xyxy=T(b[:, :4]), conf=T(b[:, 4]), cls=T(np.zeros(len(b)))))]


class FakeSam:
    def __init__(self, fn):
        self.fn = fn  # image -> (N, H, W) bool 또는 None

    def __call__(self, image, **_):
        m = self.fn(image)
        return [SimpleNamespace(masks=None if m is None else SimpleNamespace(data=T(m.astype(np.float32))))]


class FakeCls:
    def __init__(self, fn):
        self.fn = fn  # crop -> (클래스 이름, 확신)
        self.seen = []

    def predict(self, crops, **_):
        out = []
        for c in crops:
            self.seen.append(c.shape[:2])
            name, p = self.fn(c)
            out.append(SimpleNamespace(names=CLS_NAMES, probs=SimpleNamespace(top1=CLS_INDEX[name], top1conf=p)))
        return out


# ── 합성 트레이용 가짜 모델: 봉오리 색으로 판단 ───────────────────────────
def bud_color_mask(image, hue):
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, (max(hue - 3, 0), 180, 150), (min(hue + 3, 179), 255, 255)) > 0


def color_boxes(image):
    """등급 구분 없이 봉오리 위치만 (박스를 일부러 6px 크게 = 부정확한 검출기)."""
    boxes = []
    for hue in BUD_HUE.values():
        n, _, stats, _ = cv2.connectedComponentsWithStats(bud_color_mask(image, hue).astype(np.uint8))
        for x, y, w, h, area in stats[1:]:
            if area >= 80:
                boxes.append((x - 6, y - 6, x + w + 6, y + h + 6, 0.8))
    boxes.append((5, 5, 35, 35, 0.3))  # 오탐지 (구석 배경) → 분류에서 not_bud 로 버려져야 함
    return boxes


def color_segments(image):
    """FastSAM 흉내: 봉오리 하나하나 + 잎 전체를 조각으로."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    segs = [cv2.inRange(hsv, (30, 80, 60), (90, 255, 255)) > 0]  # 잎 덩어리
    for hue in BUD_HUE.values():
        n, lab = cv2.connectedComponents(bud_color_mask(image, hue).astype(np.uint8))
        segs += [lab == i for i in range(1, n)]
    return np.stack(segs)


def color_grade(crop):
    counts = {g: int(bud_color_mask(crop, hue).sum()) for g, hue in BUD_HUE.items()}
    g = max(counts, key=counts.get)
    return (g, 0.9) if counts[g] >= 30 else (NOT_BUD, 0.95)


# ── 1) 단위 검사 ─────────────────────────────────────────────────────────
def unit_checks(failures):
    img = np.full((200, 300, 3), 120, np.uint8)
    bud = np.zeros((200, 300), bool)
    cv2.circle(bud.view(np.uint8), (100, 100), 20, 1, -1)
    leaf = np.zeros((200, 300), bool)
    leaf[:, 150:] = True
    boxes = [(70, 70, 130, 130, 0.9),     # 봉오리 (박스가 마스크보다 큼) → grade_three, 박스가 마스크에 맞게 줄어야 함
             (160, 20, 190, 50, 0.4),     # 잎 위 오탐지 → not_bud
             (10, 150, 40, 180, 0.5)]     # 조각 없음 → 마스크 없이 박스만, 확신 낮음 → 버림
    answers = iter([("grade_three", 0.8), (NOT_BUD, 0.9), ("grade_one", 0.3)])
    cls = FakeCls(lambda crop: next(answers))
    det = HybridBudDetector(FakeDet(lambda im: boxes), cls, FakeSam(lambda im: np.stack([leaf, bud])))
    dets = det.detect(img)
    if [d.class_name for d in dets] != ["grade_three"]:
        failures.append(f"단위: 남은 봉오리 {[d.class_name for d in dets]} (기대 ['grade_three'])")
    else:
        d = dets[0]
        if d.mask is None or abs(int(d.mask.sum()) - int(bud.sum())) > 5:
            failures.append("단위: 봉오리 마스크가 FastSAM 조각과 다름")
        if not np.allclose(d.xyxy, [80, 80, 121, 121], atol=1):
            failures.append(f"단위: 마스크로 다시 잡은 박스 {d.xyxy} (기대 약 [80, 80, 121, 121])")
        if d.det_conf != np.float32(0.9) or d.conf != 0.8:
            failures.append(f"단위: 신뢰도 det={d.det_conf} cls={d.conf}")
    if any(abs((h - w)) > 1 for h, w in cls.seen):
        failures.append(f"단위: 분류 크롭이 정사각형이 아님 {cls.seen}")

    # 마스크가 없으면(=FastSAM 생략) 박스 그대로
    cls2 = FakeCls(lambda crop: ("grade_two", 0.9))
    dets = HybridBudDetector(FakeDet(lambda im: boxes[:1]), cls2, sam=None).detect(img)
    if len(dets) != 1 or dets[0].mask is not None or not np.allclose(dets[0].xyxy, [70, 70, 130, 130]):
        failures.append("단위: FastSAM 생략 시 박스를 그대로 써야 함")

    # 배정: 박스 중심은 A 식물인데 마스크는 대부분 B 식물 → mask_overlap 은 B
    hole = Hole("R1C1", 1, 1, np.float32([0, 0]), 10.0, 30.0)
    a = PlantSegment("R1C1", "CL", hole, np.ones((100, 50), bool), (0, 0))      # x 0~49
    b = PlantSegment("R1C2", "CL", hole, np.ones((100, 50), bool), (50, 0))     # x 50~99
    m = np.zeros((20, 40), bool)
    m[:, 25:] = True                                                             # 마스크는 x 55~69
    bud_det = BudDetection("grade_one", 0.9, np.float32([30, 10, 70, 30]), mask=m, mask_xy=(30, 10))
    if mask_overlap(bud_det, [a, b]) is not b:
        failures.append("단위: mask_overlap 이 마스크가 더 많이 겹친 식물을 고르지 않음")
    shifted = bud_det.shifted(100, 5)
    if shifted.mask_xy != (130, 15) or shifted.mask is not m:
        failures.append(f"단위: shifted 후 마스크 위치 {shifted.mask_xy}")


# ── 2) 전체 파이프라인 ───────────────────────────────────────────────────
def pipeline_checks(failures):
    cfg = copy.deepcopy(load_config())
    # 합성 시점(CL = 1~3열, CR = 2~4열)에 맞춘 범위표. 실제 설정의 범위표와 무관하게 고정
    cfg["layout"] = {**cfg["layout"], "coverage": [["CL,CR", "CL,CR", "CL,CR", "CR"],
                                                   ["CL,CR", "CL,CR", "CL,CR", "CR"],
                                                   ["CL", "CL,CR", "CL,CR", "CR"]]}
    images = {}
    for name, (x_range, corners) in VIEWS.items():
        flat = draw_tray(np.random.default_rng(0), BUDS_ONLY.get(name, {}))
        images[name], cfg["holes"][name] = make_view(flat, x_range, corners)

    cfg["grading"]["assignment"] = "mask_overlap"
    for use_sam in (True, False):
        for detect_on in ("plant_crop", "full_image"):
            tag = f"{'FastSAM' if use_sam else '박스만'}/{detect_on}"
            cfg["grading"]["detector"]["detect_on"] = detect_on
            detector = HybridBudDetector(FakeDet(color_boxes), FakeCls(color_grade),
                                         FakeSam(color_segments) if use_sam else None)
            result = RosePipeline(cfg, detector=detector).run(images)
            print(f"[혼합형 {tag}]\n{result.table()}\n")
            if result.grid() != EXPECTED:
                failures.append(f"{tag}: {result.grid()}")
            buds = [b for f in result.frames.values() for b in f.buds]
            board = draw_board(result)
            if board.std() < 5:
                failures.append(f"{tag}: 최종 결과판이 비어 있음")
            if "R1C3  grade_three" not in result.bud_table() or "grade_one 1개" not in result.bud_table():
                failures.append(f"{tag}: 봉오리 표 {result.bud_table()}")
            if any(b.class_name == NOT_BUD for b in buds):
                failures.append(f"{tag}: not_bud 가 결과에 남음")
            if use_sam and not all(b.mask is not None for b in buds):
                failures.append(f"{tag}: 마스크 없는 봉오리가 있음")


def main():
    failures = []
    unit_checks(failures)
    pipeline_checks(failures)
    if failures:
        print("실패:\n  " + "\n  ".join(failures))
        sys.exit(1)
    print("통과: 혼합형 검출기 단위 검사 + 3×4 전체 결과 일치")


if __name__ == "__main__":
    main()
