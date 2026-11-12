"""카메라별 이미지 입력.

지금은 FileSource(이미지 파일)를 쓰고, 카메라 2대가 설치되면 설정의
source.type 을 camera 로 바꾸면 된다.
"""

from abc import ABC, abstractmethod

import cv2
import numpy as np

from .config import resolve


class ImageSource(ABC):
    @abstractmethod
    def read(self) -> np.ndarray:
        """BGR 이미지 한 장을 반환."""


class FileSource(ImageSource):
    def __init__(self, path):
        self.path = resolve(path)

    def read(self):
        if not self.path.exists():
            raise FileNotFoundError(f"입력 이미지가 없습니다: {self.path}")
        # cv2.imread 는 한글 경로를 못 읽으므로 imdecode 사용
        image = cv2.imdecode(np.fromfile(str(self.path), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"이미지를 읽을 수 없습니다: {self.path}")
        return image


class CameraSource(ImageSource):
    """cv2.VideoCapture 로 한 프레임 캡처. device 는 장치 번호 또는 RTSP/HTTP URL."""

    def __init__(self, device, warmup_frames=5):
        self.device = device
        self.warmup_frames = warmup_frames

    def read(self):
        cap = cv2.VideoCapture(self.device)
        try:
            if not cap.isOpened():
                raise RuntimeError(f"카메라를 열 수 없습니다: {self.device}")
            # 자동 노출이 안정될 때까지 앞 프레임은 버림
            for _ in range(self.warmup_frames):
                cap.read()
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError(f"카메라 프레임을 읽지 못했습니다: {self.device}")
            return frame
        finally:
            cap.release()


SOURCES = {"file": FileSource, "camera": CameraSource}


def build_source(source_cfg) -> ImageSource:
    cfg = dict(source_cfg)
    kind = cfg.pop("type")
    if kind not in SOURCES:
        raise ValueError(f"알 수 없는 source.type: {kind} (가능: {list(SOURCES)})")
    return SOURCES[kind](**cfg)
