"""1단계-1: 구멍마다 식물 유무 판단.

  dark_cover : 사진에서 자동 판단. 구멍 중심 원 안의 어두운 픽셀 비율(빈 구멍 점수)을
               범위표의 카메라들에서 평균해 판단
  fixed      : layout.plants 의 O/X 표를 그대로 사용. 빈 구멍 점수는 참고용으로 계산하고,
               사진 판단과 표가 다르면 경고

새 방법은 PresenceDetector 를 상속해 score()/is_present() 를 구현하고 PRESENCE 에 등록한다.
"""

import warnings
from abc import ABC, abstractmethod

import cv2
import numpy as np

from .layout import Hole, circle_mask, coverage_map


class PresenceDetector(ABC):
    @abstractmethod
    def score(self, image: np.ndarray, hole: Hole) -> float:
        """빈 구멍일수록 큰 값 (0~1). 판단할 수 없으면 NaN."""

    @abstractmethod
    def is_present(self, cell_id: str, scores: list[float]) -> bool | None:
        """칸 하나의 최종 판단. True = 식물, False = 빈 구멍, None = 판단 불가."""


class DarkCoverPresence(PresenceDetector):
    """빈 화분은 검은 덮개가 보이므로, 원 안의 어두운 픽셀 비율이 높으면 빈 구멍."""

    def __init__(self, dark_v_max=70, empty_ratio=0.38, **_):
        self.dark_v_max = dark_v_max
        self.empty_ratio = empty_ratio

    def score(self, image, hole):
        h, w = image.shape[:2]
        r = int(np.ceil(hole.radius))
        cx, cy = int(hole.center[0]), int(hole.center[1])
        x1, y1, x2, y2 = max(cx - r, 0), max(cy - r, 0), min(cx + r + 1, w), min(cy + r + 1, h)
        if x2 <= x1 or y2 <= y1:
            return float("nan")
        crop = image[y1:y2, x1:x2]
        m = circle_mask(crop.shape, hole.center - np.float32([x1, y1]), hole.radius)
        v = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[..., 2][m]
        return float((v <= self.dark_v_max).mean()) if v.size else float("nan")

    def is_present(self, cell_id, scores):
        valid = [s for s in scores if not np.isnan(s)]
        if not valid:
            return None
        return float(np.mean(valid)) < self.empty_ratio


class FixedPresence(DarkCoverPresence):
    """layout.plants 의 O/X 표로 고정. 점수 계산은 DarkCoverPresence 와 같음(참고·경고용)."""

    def __init__(self, layout, warn=True, **cfg):
        super().__init__(**cfg)
        plants = layout.get("plants")
        if plants is None:
            raise ValueError("presence.method: fixed 를 쓰려면 layout.plants (O/X 표) 가 필요합니다")
        # 표 크기 검사는 coverage_map 과 같은 규칙을 재사용
        table = coverage_map({**layout, "coverage": plants})
        self.plants = {}
        for cid, v in table.items():
            mark = "".join(v).upper()
            if mark not in ("O", "X"):
                raise ValueError(f"layout.plants 의 {cid} 값은 O 또는 X 여야 합니다: {v}")
            self.plants[cid] = mark == "O"
        self.warn = warn

    def is_present(self, cell_id, scores):
        fixed = self.plants[cell_id]
        auto = super().is_present(cell_id, scores)
        if self.warn and auto is not None and auto != fixed:
            warnings.warn(f"{cell_id}: 고정 설정은 {'식물' if fixed else '빈 구멍'}, "
                          f"사진 판단은 {'식물' if auto else '빈 구멍'} (점수 {np.nanmean(scores):.2f})",
                          stacklevel=2)
        return fixed


PRESENCE = {"dark_cover": DarkCoverPresence, "fixed": FixedPresence}


def build_presence(cfg, layout) -> PresenceDetector:
    method = cfg["method"]
    if method not in PRESENCE:
        raise ValueError(f"알 수 없는 presence.method: {method} (가능: {list(PRESENCE)})")
    return PRESENCE[method](layout=layout, **cfg)
