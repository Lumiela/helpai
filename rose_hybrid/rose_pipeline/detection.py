"""꽃봉오리 검출기.

VlmPlantGrader    : (plant_vlm.py) 식물마다 Claude 가 봉오리 위치·등급을 직접 판단. 검출 모델 불필요 (type: vlm_plant)
YoloBudDetector   : train_detector.py 로 학습한 YOLO (위치 + 등급을 한 모델이 판단)
HybridBudDetector : 혼합형. YOLO 위치(후보) → FastSAM 마스크 → 등급 분류 → (선택) Claude 검수
                    (분류는 cls_backend 로 고름: yolo = train_hybrid.py 학습 모델, embed = 사전학습 모델 특징 + 참고 크롭,
                     vlm = Claude, cascade = yolo 뒤 애매한 것만 Claude. vlm.py)
다른 검출기를 쓰려면 BudDetector 를 상속하고 DETECTORS 에 등록한다.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import resolve
from .vlm import NOT_BUD, VlmGradeClassifier, YoloGradeClassifier, build_classifier  # NOT_BUD: '봉오리 아님' 


@dataclass
class BudDetection:
    class_name: str
    conf: float
    xyxy: np.ndarray  # (4,) float, 카메라 이미지 좌표
    cell_id: str | None = field(default=None)  # 할당된 식물 칸 (할당 후 채움)
    mask: np.ndarray | None = field(default=None)  # bool, 봉오리 마스크 (혼합형만). 좌상단 = mask_xy
    mask_xy: tuple | None = field(default=None)    # mask 좌상단의 이미지 좌표 (x, y)
    det_conf: float | None = field(default=None)   # 혼합형: 위치 검출 신뢰도 (conf 는 분류 신뢰도)
    first_class: str | None = field(default=None)  # Claude 검수 전 분류 모델의 등급 (검수했을 때만)
    first_conf: float | None = field(default=None)
    added: bool = field(default=False)  # 위치 모델이 놓쳐 Claude 검수가 추가한 봉오리
    crop_xyxy: np.ndarray | None = field(default=None)  # 혼합형: 등급 판단에 쓴 크롭 영역 (박스보다 넓음)

    @property
    def center(self):
        return (self.xyxy[0] + self.xyxy[2]) / 2, (self.xyxy[1] + self.xyxy[3]) / 2

    def shifted(self, dx, dy):
        xy = None if self.mask_xy is None else (int(self.mask_xy[0] + dx), int(self.mask_xy[1] + dy))
        return BudDetection(self.class_name, self.conf, self.xyxy + np.float32([dx, dy, dx, dy]),
                            mask=self.mask, mask_xy=xy, det_conf=self.det_conf,
                            first_class=self.first_class, first_conf=self.first_conf, added=self.added,
                            crop_xyxy=None if self.crop_xyxy is None else self.crop_xyxy + np.float32([dx, dy, dx, dy]))


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
    3) cls (분류기): 마스크(없으면 박스) 둘레를 정사각형으로 잘라 not_bud / grade_one~four 판단.
       classify(crops) 를 가진 분류기(vlm.py) 또는 ultralytics YOLO 분류 모델.
       not_bud 이거나 확신(cls_conf)이 낮으면 버림 → 검출기 오탐지를 여기서 거름
    4) review (선택, Claude): 3) 에서 남은 크롭을 Claude 가 분류 결과를 모른 채 다시 판단해 최종 등급으로 씀.
       not_bud 이거나 확신(review_conf)이 낮으면 버림. 분류 모델의 등급은 first_class / first_conf 에 남김
    5) missed (선택, Claude): 식물마다 찾은 봉오리를 박스로 그린 사진을 보내 놓친 봉오리를 추가 (plant_vlm.VlmMissedFinder).
       식물 정보가 필요해서 RosePipeline.grade_plants 가 봉오리 배정 뒤 add_missed() 를 부름

    det_classes: 위치 모델에서 쓸 클래스 번호 목록 (None = 전부). agnostic_nms: 클래스 상관없이 겹친 박스 정리.
    max_box_frac: 박스가 검출 이미지(식물 크롭) 넓이의 이 비율보다 크면 버림 (식물 전체를 잡은 박스 제거).
    max_box_px: 박스의 긴 변이 이 픽셀보다 크면 버림 (None = 제한 없음). 식물 크롭이 크면 비율 기준으로는
        식물 일부를 크게 잡은 박스가 남으므로, 실제 봉오리·꽃 크기로 직접 제한.
    누락 추가 뒤 중복 정리: 검수에서 남은 봉오리의 등급 판단 크롭 안에, 누락 추가로 찾은 같은 등급 봉오리가 있으면
        검수 답은 그 봉오리를 보고 한 것이므로 검수 봉오리를 버리고 위치가 정확한 추가 봉오리만 남김.
    """

    def __init__(self, det, cls, sam=None, conf=0.15, imgsz=640, sam_imgsz=1024, sam_conf=0.4,
                 mask_iou=0.3, mask_margin=0.1, cls_imgsz=224, cls_conf=0.5, crop_pad=0.15,
                 device=None, det_classes=None, agnostic_nms=False, max_box_frac=1.0, review=None,
                 review_conf=0.5, missed=None, max_box_px=None, **_):
        self.det, self.sam = det, sam
        self.cls = cls if hasattr(cls, "classify") else YoloGradeClassifier(cls, cls_imgsz, device)
        self.review, self.review_conf, self.missed = review, review_conf, missed
        self.det_classes, self.agnostic_nms, self.max_box_frac = det_classes, agnostic_nms, max_box_frac
        self.max_box_px = max_box_px
        self.stats = {"candidates": 0, "classified": 0, "reviewed": 0, "review_same": 0, "review_changed": 0,
                      "review_rejected": 0, "added": 0, "dup_removed": 0}
        self.conf, self.imgsz, self.device = conf, imgsz, device
        self.sam_imgsz, self.sam_conf = sam_imgsz, sam_conf
        self.mask_iou, self.mask_margin = mask_iou, mask_margin
        self.cls_imgsz, self.cls_conf, self.crop_pad = cls_imgsz, cls_conf, crop_pad

    @property
    def usage(self):
        """run_pipeline.py 출력용: 후보·분류·검수 개수 (+ Claude 요청·토큰 수)."""
        u = dict(self.stats)
        for vlm in (self.review, self.missed):
            for k, v in (getattr(vlm, "usage", None) or {}).items():
                u[k] = u.get(k, 0) + v
        return u

    @property
    def last_notes(self):
        """누락 추가 때 Claude 가 식물마다 답한 내용 (vlm_plant 와 같은 형식 → 결과판·vlm/ 에 표시)."""
        return self.missed.last_notes if self.missed is not None else None

    def add_missed(self, image, plants, buds):
        """놓친 봉오리를 추가한 전체 봉오리 목록. 검수 봉오리가 추가 봉오리와 중복이면 검수 봉오리를 뺌."""
        if self.missed is None:
            return list(buds)
        added = self.missed.find_missed(image, plants, buds)
        self.stats["added"] += len(added)
        kept = [b for b in buds if not self.duplicated_by(b, added)]
        self.stats["dup_removed"] += len(buds) - len(kept)
        return kept + added

    @staticmethod
    def duplicated_by(bud, added):
        """검수 봉오리(bud)의 등급 판단 크롭 안에 같은 등급의 추가 봉오리 중심이 있으면 True.
        추가 봉오리는 이미 찾은 박스 밖에 있으므로, 이 경우 검수 답은 박스가 아니라 크롭 가장자리의 그 봉오리를 본 것."""
        if bud.added or bud.first_class is None or bud.crop_xyxy is None:
            return False
        x1, y1, x2, y2 = bud.crop_xyxy
        for a in added:
            cx, cy = a.center
            if a.class_name == bud.class_name and x1 <= cx <= x2 and y1 <= cy <= y2:
                return True
        return False

    @classmethod
    def from_config(cls, weights, cls_weights=None, sam_weights="FastSAM-s.pt", cls_backend="yolo", vlm=None,
                    cascade_below=0.8, embed=None, review=False, add_missed=False, **cfg):
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
        cls_model = YOLO(require(cls_weights, "cls")) if cls_backend in ("yolo", "embed", "cascade") else None
        grader = build_classifier(cls_backend, cls_model, vlm, cfg.get("cls_imgsz", 224), cfg.get("device"),
                                  cascade_below, embed)
        reviewer = VlmGradeClassifier.from_config(**(vlm or {})) if review else None
        missed = None
        if add_missed:
            from .plant_vlm import VlmMissedFinder  # plant_vlm 이 이 모듈의 BudDetection 을 쓰므로 여기서 불러옴
            missed = VlmMissedFinder.from_config(vlm=vlm, crop_padding=cfg.get("crop_padding", 0.1))
        return cls(YOLO(require(weights, "det")), grader, sam, review=reviewer, missed=missed, **cfg)

    def detect(self, image):
        h, w = image.shape[:2]
        kw = {"classes": self.det_classes} if self.det_classes is not None else {}
        if self.agnostic_nms:
            kw["agnostic_nms"] = True
        r = self.det.predict(image, conf=self.conf, imgsz=self.imgsz, device=self.device, verbose=False, **kw)[0]
        boxes = r.boxes.xyxy.cpu().numpy().astype(np.float32)
        confs = r.boxes.conf.cpu().numpy()
        if len(boxes) and self.max_box_frac < 1:
            frac = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1]) / float(w * h)
            boxes, confs = boxes[frac <= self.max_box_frac], confs[frac <= self.max_box_frac]
        if len(boxes) and self.max_box_px:
            side = np.maximum(boxes[:, 2] - boxes[:, 0], boxes[:, 3] - boxes[:, 1])
            boxes, confs = boxes[side <= self.max_box_px], confs[side <= self.max_box_px]
        self.stats["candidates"] += len(boxes)
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
            dets.append(BudDetection("", 0.0, box, mask=mask, mask_xy=xy, det_conf=float(dc),
                                     crop_xyxy=np.float32([x1, y1, x2, y2])))

        kept, kept_crops = [], []
        for d, c, (name, p) in zip(dets, crops, self.cls.classify(crops)):
            if name != NOT_BUD and p >= self.cls_conf:
                d.class_name, d.conf = name, p
                kept.append(d)
                kept_crops.append(c)
        self.stats["classified"] += len(kept)
        return self.review_buds(kept, kept_crops) if self.review is not None else kept

    def review_buds(self, dets, crops):
        """Claude 검수: 분류 결과를 알려 주지 않고 크롭을 다시 판단시켜 그 답을 최종 등급으로."""
        if not dets:
            return []
        out = []
        for d, (name, p) in zip(dets, self.review.classify(crops)):
            self.stats["reviewed"] += 1
            d.first_class, d.first_conf = d.class_name, d.conf
            if name == NOT_BUD or p < self.review_conf:
                self.stats["review_rejected"] += 1
                continue
            self.stats["review_same" if name == d.class_name else "review_changed"] += 1
            d.class_name, d.conf = name, p
            out.append(d)
        return out

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


def _vlm_plant(**cfg):
    from .plant_vlm import VlmPlantGrader  # plant_vlm 이 이 모듈의 BudDetection 을 쓰므로 여기서 불러옴
    return VlmPlantGrader.from_config(**cfg)


DETECTORS = {"yolo": YoloBudDetector, "hybrid": HybridBudDetector.from_config, "vlm_plant": _vlm_plant,
             "none": NullDetector}


def build_detector(det_cfg) -> BudDetector:
    kind = det_cfg["type"]
    if kind not in DETECTORS:
        raise ValueError(f"알 수 없는 grading.detector.type: {kind} (가능: {list(DETECTORS)})")
    return DETECTORS[kind](**det_cfg)
