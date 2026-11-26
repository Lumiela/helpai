"""입력 이미지 읽기. (스테레오 정렬은 높이 모듈 안에서 함 — 다른 모듈은 입력 이미지 좌표를 씀)

사진이 cameras.input_width 보다 넓으면 그 폭으로 줄여 모든 모듈이 같은 사진·같은 좌표를 쓴다.
줄인 사진은 손실 없는 PNG 로 저장해 젯슨에도 그대로 보낸다 (팀 설정값은 2000x1500 사진 기준).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import cv2
import numpy as np

from crop_monitor.registry import cameras, input_width
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
    width, out = input_width(), state.get("out_dir")
    folder = Path(out) / "input" if out else Path(tempfile.mkdtemp(prefix="crop-input-"))
    frames, paths = {}, {}
    for cam, path in images.items():
        img = read_image(path)
        if width and img.shape[1] > width:
            img = cv2.resize(img, (width, round(img.shape[0] * width / img.shape[1])), interpolation=cv2.INTER_AREA)
            folder.mkdir(parents=True, exist_ok=True)
            path = str(folder / f"{cam}.png")
            cv2.imwrite(path, img, [cv2.IMWRITE_PNG_COMPRESSION, 3])
        frames[cam], paths[cam] = img, str(path)
    return {"frames": frames, "images": paths}
