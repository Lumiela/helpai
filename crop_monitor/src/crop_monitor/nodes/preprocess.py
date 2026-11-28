"""입력 이미지 읽기. (스테레오 정렬은 높이 모듈 안에서 함 — 다른 모듈은 원본 좌표를 씀)"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from crop_monitor.registry import cameras
from crop_monitor.state import PipelineState


def read_image(path: str) -> np.ndarray:
    # cv2.imread 는 한글 경로를 못 읽으므로 imdecode 사용
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"이미지가 없습니다: {path}")
    img = cv2.imdecode(np.fromfile(str(p), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"이미지를 읽을 수 없습니다: {path}")
    return img


def preprocess(state: PipelineState) -> PipelineState:
    images = state.get("images") or {}
    missing = [c for c in cameras() if c not in images]
    if missing:
        raise ValueError(f"카메라 이미지가 빠졌습니다: {missing} (필요: {list(cameras())})")
    return {"frames": {cam: read_image(path) for cam, path in images.items()}}
