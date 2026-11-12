"""꽃봉오리 검출기.

YoloBudDetector   : train_detector.py 로 학습한 YOLOv11 (위치 + 등급을 한 모델이 판단)
HybridBudDetector : 혼합형. YOLO(봉오리 1클래스) 위치 → FastSAM 마스크 → 분류 모델 등급 (train_hybrid.py)
다른 검출기를 쓰려면 BudDetector 를 상속하고 DETECTORS 에 등록한다.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import resolve

NOT_BUD = "not_bud"  # 혼합형 분류 모델의 '봉오리 아님' 클래스


@dataclass
class BudDetection:
    class_name: str
    conf: float
    xyxy: np.ndarray  # (4,) float, 카메라 이미지 좌표
    cell_id: str | None = field(default=None)  # 할당된 식물 칸 (할당 후 채움)
    mask: np.ndarray | None = field(default=None)  # bool, 봉오리 마스크 (혼합형만). 좌상단 = mask_xy
    mask_xy: tuple | None = field(default=None)    # mask 좌상단의 이미지 좌표 (x, y)
    det_conf: float | None = field(default=None)   # 혼합형: 위치 검출 신뢰도 (conf 는 분류 신뢰도)

    @property
    def center(self):
        return (self.xyxy[0] + self.xyxy[2]) / 2, (self.xyxy[1] + self.xyxy[3]) / 2

    def shifted(self, dx, dy):
        xy = None if self.mask_xy is None else (int(self.mask_xy[0] + dx), int(self.mask_xy[1] + dy))
        return BudDetection(self.class_name, self.conf, self.xyxy + np.float32([dx, dy, dx, dy]),
                            mask=self.mask, mask_xy=xy, det_conf=self.det_conf)


def square_box(xyxy, pad, width, height):
    """박스 중심 기준 정사각형(긴 변 × (1 + 2·pad)) 정수 좌표. 이미지 밖은 잘라냄.
    분류 학습 크롭(tools/prepare_hybrid_data.py)과 추론 크롭이 같은 모양이 되도록 공용으로 사용."""
    cx, cy = (xyxy[0] + xyxy[2]) / 2, (xyxy[1] + xyxy[3]) / 2
    half = max(xyxy[2] - xyxy[0], xyxy[3] - xyxy[1]) * (1 + 2 * pad) / 2
    x1, y1 = max(int(round(cx - half)), 0), max(int(round(cy - half)), 0)
    x2, y2 = min(int(round(cx + half)), width), min(int(round(cy + half)), height)
    return x1, y1, max(x2, x1 + 1), max(y2, y1 + 1)


class BudDetector(ABC):
    @abstractmethod
    def detect(self, image: np.ndarray) -> list[BudDetection]:
        """image(BGR) 좌표계의 봉오리 검출 결과."""


class NullDetector(BudDetector):
    """검출 없음. 검출 모델이 없을 때 1단계만 확인하는 용도 (모든 식물 = 기본 클래스)."""

    def __init__(self, **_):
        pass

    def detect(self, image):
        return []


class YoloBudDetector(BudDetector):
    def __init__(self, weights, conf=0.25, imgsz=640, device=None, **_):
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise ImportError("ultralytics 가 필요합니다: pip install ultralytics") from e
        path = resolve(weights)
        if not path.exists():
            raise FileNotFoundError(
                f"검출 가중치가 없습니다: {path}\n"
                "  python train_detector.py 로 학습하거나, 설정에서 grading.detector.type 을 none 으로 바꾸세요.")
        self.model = YOLO(str(path))
        self.conf, self.imgsz, self.device = conf, imgsz, device

    def detect(self, image):
        r = self.model.predict(image, conf=self.conf, imgsz=self.imgsz, device=self.device, verbose=False)[0]
        names = r.names
        return [BudDetection(names[int(c)], float(p), xyxy.astype(np.float32))
                for xyxy, c, p in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.cls.cpu().numpy(),
                                      r.boxes.conf.cpu().numpy())]


class HybridBudDetector(BudDetector):
    """혼합형: 위치 찾기와 등급 판단을 나눈다.

    1) det (YOLO, 봉오리 1클래스): 봉오리 위치 박스. 등급은 보지 않음
    2) sam (FastSAM): 이미지 전체를 조각낸 뒤, 박스마다 가장 잘 맞는 조각을 봉오리 마스크로 사용.
       맞는 조각이 없으면(박스와 IoU < mask_iou) 박스만 사용
    3) cls (YOLO 분류): 마스크(없으면 박스) 둘레를 정사각형으로 잘라 not_bud / grade_one~four 판단.
       not_bud 이거나 확신(cls_conf)이 낮으면 버림 → 검출기 오탐지를 여기서 거름
    """

    def __init__(self, det, cls, sam=None, conf=0.15, imgsz=640, sam_imgsz=1024, sam_conf=0.4,
                 mask_iou=0.3, mask_margin=0.1, cls_imgsz=224, cls_conf=0.5, crop_pad=0.15,
                 device=None, **_):
        self.det, self.cls, self.sam = det, cls, sam
        self.conf, self.imgsz, self.device = conf, imgsz, device
        self.sam_imgsz, self.sam_conf = sam_imgsz, sam_conf
        self.mask_iou, self.mask_margin = mask_iou, mask_margin
        self.cls_imgsz, self.cls_conf, self.crop_pad = cls_imgsz, cls_conf, crop_pad

    @classmethod
    def from_config(cls, weights, cls_weights, sam_weights="FastSAM-s.pt", **cfg):
        try:
            from ultralytics import YOLO, FastSAM
        except ImportError as e:
            raise ImportError("ultralytics 가 필요합니다: pip install ultralytics") from e

        def require(path, step):
            p = resolve(path)
            if not p.exists():
                raise FileNotFoundError(f"가중치가 없습니다: {p}\n  python train_hybrid.py {step} 로 학습하세요.")
            return str(p)

        sam = None
        if sam_weights:  # 프로젝트 안에 없으면 이름만 넘겨 ultralytics 가 내려받게 함
            p = resolve(sam_weights)
            sam = FastSAM(str(p) if p.exists() else str(sam_weights))
        return cls(YOLO(require(weights, "det")), YOLO(require(cls_weights, "cls")), sam, **cfg)

    def detect(self, image):
        h, w = image.shape[:2]
        r = self.det.predict(image, conf=self.conf, imgsz=self.imgsz, device=self.device, verbose=False)[0]
        boxes = r.boxes.xyxy.cpu().numpy().astype(np.float32)
        confs = r.boxes.conf.cpu().numpy()
        if not len(boxes):
            return []

        segments = self.segment(image) if self.sam is not None else None
        dets, crops = [], []
        for box, dc in zip(boxes, confs):
            mask, xy = self.match_mask(segments, box, w, h)
            if mask is not None:  # 마스크에 맞춰 박스를 다시 잡음
                ys, xs = np.nonzero(mask)
                box = np.float32([xy[0] + xs.min(), xy[1] + ys.min(), xy[0] + xs.max() + 1, xy[1] + ys.max() + 1])
            x1, y1, x2, y2 = square_box(box, self.crop_pad, w, h)
            crops.append(image[y1:y2, x1:x2])
            dets.append(BudDetection("", 0.0, box, mask=mask, mask_xy=xy, det_conf=float(dc)))

        kept = []
        for d, res in zip(dets, self.cls.predict(crops, imgsz=self.cls_imgsz, device=self.device, verbose=False)):
            name, p = res.names[int(res.probs.top1)], float(res.probs.top1conf)
            if name != NOT_BUD and p >= self.cls_conf:
                d.class_name, d.conf = name, p
                kept.append(d)
        return kept

    def segment(self, image):
        """FastSAM 전체 조각. 반환: (masks (N,H,W) bool, 조각별 면적) 또는 None"""
        h, w = image.shape[:2]
        r = self.sam(image, imgsz=self.sam_imgsz, conf=self.sam_conf, retina_masks=True,
                     device=self.device, verbose=False)[0]
        if r.masks is None:
            return None
        masks = r.masks.data.cpu().numpy() > 0.5
        if masks.shape[1:] != (h, w):
            masks = np.stack([cv2.resize(m.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST) > 0
                              for m in masks])
        return masks, masks.reshape(len(masks), -1).sum(1)

    def match_mask(self, segments, box, w, h):
        """박스와 IoU 가 가장 큰 조각을, 박스를 mask_margin 만큼 넓힌 범위로 잘라 반환. 없으면 (None, None).
        잎과 붙은 큰 조각이 골라져도 박스 근처만 남으므로 봉오리 밖으로 크게 번지지 않음."""
        if segments is None:
            return None, None
        masks, areas = segments
        bx1, by1 = max(int(box[0]), 0), max(int(box[1]), 0)
        bx2, by2 = min(int(np.ceil(box[2])), w), min(int(np.ceil(box[3])), h)
        if bx2 <= bx1 or by2 <= by1:
            return None, None
        inside = masks[:, by1:by2, bx1:bx2].reshape(len(masks), -1).sum(1)
        iou = inside / ((bx2 - bx1) * (by2 - by1) + areas - inside + 1e-6)
        i = int(np.argmax(iou))
        if iou[i] < self.mask_iou:
            return None, None
        mx, my = int((bx2 - bx1) * self.mask_margin), int((by2 - by1) * self.mask_margin)
        x1, y1, x2, y2 = max(bx1 - mx, 0), max(by1 - my, 0), min(bx2 + mx, w), min(by2 + my, h)
        mask = masks[i, y1:y2, x1:x2].copy()  # 전체 조각 배열을 붙잡고 있지 않도록 복사
        return (mask, (x1, y1)) if mask.any() else (None, None)


DETECTORS = {"yolo": YoloBudDetector, "hybrid": HybridBudDetector.from_config, "none": NullDetector}


def build_detector(det_cfg) -> BudDetector:
    kind = det_cfg["type"]
    if kind not in DETECTORS:
        raise ValueError(f"알 수 없는 grading.detector.type: {kind} (가능: {list(DETECTORS)})")
    return DETECTORS[kind](**det_cfg)
