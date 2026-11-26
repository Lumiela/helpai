"""1단계-2: 각각의 미니 장미 구분 — 식물 세그멘테이션.

잎이 이웃 구멍으로 넘어가 겹치므로 칸 경계로 자르지 않고, 식물 색 픽셀을
"가장 가까운 식물 있는 구멍"에 배정한다(보로노이 분할).

새 방법(SAM 등)은 PlantSegmenter 를 상속해 segment() 를 구현하고 SEGMENTERS 에 등록한다.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import cv2
import numpy as np

from .layout import Hole


@dataclass
class PlantSegment:
    cell_id: str
    camera: str
    hole: Hole
    mask: np.ndarray   # bool, 잘라낸 마스크 (bbox 크기)
    offset: tuple      # mask 좌상단의 이미지 좌표 (x, y)

    @property
    def area(self):
        return int(self.mask.sum())

    def contains(self, x, y):
        """이미지 좌표 (x, y) 가 식물 마스크 안인지."""
        mx, my = int(x) - self.offset[0], int(y) - self.offset[1]
        h, w = self.mask.shape
        return 0 <= mx < w and 0 <= my < h and bool(self.mask[my, mx])

    def bbox(self):
        """식물 마스크를 감싸는 이미지 좌표 bbox (x1, y1, x2, y2). 비었으면 None."""
        if not self.mask.any():
            return None
        ys, xs = np.nonzero(self.mask)
        ox, oy = self.offset
        return ox + xs.min(), oy + ys.min(), ox + xs.max() + 1, oy + ys.max() + 1

    def overlap(self, xyxy):
        """bbox(xyxy) 안에 들어오는 식물 마스크 픽셀 수."""
        ox, oy = self.offset
        h, w = self.mask.shape
        x1, y1 = max(int(xyxy[0]) - ox, 0), max(int(xyxy[1]) - oy, 0)
        x2, y2 = min(int(np.ceil(xyxy[2])) - ox, w), min(int(np.ceil(xyxy[3])) - oy, h)
        if x2 <= x1 or y2 <= y1:
            return 0
        return int(self.mask[y1:y2, x1:x2].sum())

    def overlap_mask(self, mask, xy):
        """이미지 좌표 xy(좌상단)에 놓인 bool 마스크와 겹치는 식물 마스크 픽셀 수."""
        ox, oy = self.offset
        h, w = self.mask.shape
        mx, my = int(xy[0]), int(xy[1])
        x1, y1 = max(mx, ox), max(my, oy)
        x2, y2 = min(mx + mask.shape[1], ox + w), min(my + mask.shape[0], oy + h)
        if x2 <= x1 or y2 <= y1:
            return 0
        return int((self.mask[y1 - oy:y2 - oy, x1 - ox:x2 - ox] & mask[y1 - my:y2 - my, x1 - mx:x2 - mx]).sum())


class PlantSegmenter(ABC):
    @abstractmethod
    def segment(self, image: np.ndarray, camera: str, plant_holes: list[Hole]) -> list[PlantSegment]:
        """식물 있는 구멍들(plant_holes) 각각의 식물 마스크."""


class NearestPotSegmenter(PlantSegmenter):
    def __init__(self, hsv_ranges, max_reach=1.6, min_component_ratio=0.02, morph_kernel=5,
                 work_max_side=1280, **_):
        self.ranges = [(np.array(r["lower"], np.uint8), np.array(r["upper"], np.uint8))
                       for r in hsv_ranges]
        self.max_reach = max_reach
        self.min_component_ratio = min_component_ratio
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (morph_kernel, morph_kernel))
        self.work_max_side = work_max_side

    def plant_color_mask(self, image):
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        m = np.zeros(image.shape[:2], np.uint8)
        for lo, hi in self.ranges:
            m |= cv2.inRange(hsv, lo, hi)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, self.kernel)
        return cv2.morphologyEx(m, cv2.MORPH_CLOSE, self.kernel) > 0

    def segment(self, image, camera, plant_holes):
        if not plant_holes:
            return []
        H, W = image.shape[:2]
        s = min(1.0, self.work_max_side / max(H, W))   # 축소 비율
        small = cv2.resize(image, (round(W * s), round(H * s)), interpolation=cv2.INTER_AREA) if s < 1 else image
        color = self.plant_color_mask(small)
        h, w = color.shape

        # 각 픽셀에 가장 가까운 구멍 (도달 거리 밖이면 -1)
        yy, xx = np.arange(h, dtype=np.float32)[:, None], np.arange(w, dtype=np.float32)[None, :]
        best_d = np.full((h, w), np.inf, np.float32)
        label = np.full((h, w), -1, np.int16)
        for i, hole in enumerate(plant_holes):
            cx, cy = hole.center * s
            d = (xx - cx) ** 2 + (yy - cy) ** 2
            reach = (self.max_reach * hole.spacing * s) ** 2
            closer = (d < best_d) & (d <= reach)
            best_d[closer], label[closer] = d[closer], i
        label[~color] = -1

        segs = []
        for i, hole in enumerate(plant_holes):
            m = (label == i).astype(np.uint8)
            n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
            min_area = self.min_component_ratio * np.pi * (hole.radius * s) ** 2
            keep = np.zeros(n, bool)
            keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_area
            m = keep[lab]
            if s < 1:  # 원래 해상도로 복원 (bbox 영역만)
                ys, xs = np.nonzero(m)
                if len(xs) == 0:
                    segs.append(PlantSegment(hole.cell_id, camera, hole, np.zeros((0, 0), bool), (0, 0)))
                    continue
                x1, y1 = int(xs.min() / s), int(ys.min() / s)
                x2, y2 = min(int(np.ceil((xs.max() + 1) / s)), W), min(int(np.ceil((ys.max() + 1) / s)), H)
                full = cv2.resize(m.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST)
                segs.append(PlantSegment(hole.cell_id, camera, hole, full[y1:y2, x1:x2].astype(bool), (x1, y1)))
            else:
                segs.append(PlantSegment(hole.cell_id, camera, hole, m, (0, 0)))
        return segs


SEGMENTERS = {"nearest_pot": NearestPotSegmenter}


def build_segmenter(seg_cfg) -> PlantSegmenter:
    method = seg_cfg["method"]
    if method not in SEGMENTERS:
        raise ValueError(f"알 수 없는 segmentation.method: {method} (가능: {list(SEGMENTERS)})")
    return SEGMENTERS[method](**seg_cfg)
