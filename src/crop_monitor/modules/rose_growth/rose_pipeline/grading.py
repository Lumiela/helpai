"""2단계: 생장 단계 판단.

1) 모든 식물에 기본 클래스(grades[0] = grade_zero) 부여
2) 꽃봉오리를 검출하고 할당 규칙으로 각 봉오리를 식물에 배정
3) 식물에 배정된 봉오리 등급 중 가장 높은 것이 그 식물의 생장 단계
"""

import warnings

import numpy as np

from .detection import BudDetection, BudDetector
from .segmentation import PlantSegment


def detect_buds(image, plants, detector: BudDetector, detect_on="plant_crop", crop_padding=0.1):
    """카메라 이미지에서 봉오리 검출. 결과 좌표는 모두 카메라 이미지 기준."""
    if detect_on == "full_image":
        return detector.detect(image)
    if detect_on != "plant_crop":
        raise ValueError(f"알 수 없는 detect_on: {detect_on} (가능: plant_crop, full_image)")

    h, w = image.shape[:2]
    dets = []
    for p in plants:
        box = p.bbox()
        if box is None:
            continue
        x1, y1, x2, y2 = box
        px, py = int((x2 - x1) * crop_padding), int((y2 - y1) * crop_padding)
        x1, y1, x2, y2 = max(x1 - px, 0), max(y1 - py, 0), min(x2 + px, w), min(y2 + py, h)
        dets += [d.shifted(x1, y1) for d in detector.detect(image[y1:y2, x1:x2])]
    # 이웃 식물 crop 이 겹치면 같은 봉오리가 두 번 잡히므로 중복 제거
    return dedupe(dets)


def dedupe(dets, iou_thr=0.5):
    dets = sorted(dets, key=lambda d: -d.conf)
    kept = []
    for d in dets:
        if all(iou(d.xyxy, k.xyxy) < iou_thr for k in kept):
            kept.append(d)
    return kept


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return float(inter / union) if union > 0 else 0.0


def assign_buds(dets: list[BudDetection], plants: list[PlantSegment], assigner):
    """각 봉오리의 cell_id 를 채움. 어느 식물에도 속하지 않으면 None."""
    for d in dets:
        p = assigner(d, plants)
        d.cell_id = p.cell_id if p is not None else None
    return dets


def decide_grades(plants: list[PlantSegment], dets: list[BudDetection], grades):
    """식물별 최종 등급. 반환: {cell_id: grade_name}"""
    rank = {g: i for i, g in enumerate(grades)}
    unknown = {d.class_name for d in dets} - rank.keys()
    if unknown:
        warnings.warn(f"grades 에 없는 클래스는 무시합니다: {sorted(unknown)}")

    result = {p.cell_id: grades[0] for p in plants}  # 기본 클래스
    for d in dets:
        if d.cell_id in result and d.class_name in rank:
            if rank[d.class_name] > rank[result[d.cell_id]]:
                result[d.cell_id] = d.class_name
    return result
