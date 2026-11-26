"""생장 단계: 모델 추론만 젯슨에 맡기는 RosePipeline (2026-11-24).

원본과 같은 순서를 지킨다.
    칸·식물 나누기 (네이버, CPU) → 봉오리 위치·마스크·등급 분류 (젯슨, GPU) → Gemini 검수 (네이버)
    → 중복 정리 → 식물 배정 → Gemini 누락 추가 → 식물별 등급 → 카메라 합치기
원본과 다른 점은 젯슨 요청을 두 카메라 한 번으로 묶은 것과, 검수 크롭을 카메라 단위로 묶어 보내는 것이다.
"""

from __future__ import annotations

import base64

import cv2
import numpy as np

from crop_monitor import remote

from .rose_pipeline import RosePipeline
from .rose_pipeline.detection import BudDetection, HybridBudDetector
from .rose_pipeline.grading import assign_buds, decide_grades, dedupe
from .rose_pipeline.plant_vlm import VlmMissedFinder
from .rose_pipeline.vlm import VlmGradeClassifier

# 젯슨에 넘겨 원본 설정(growth.yaml)과 같은 값으로 추론하게 하는 항목
DETECTOR_KEYS = ("conf", "imgsz", "sam_imgsz", "sam_conf", "mask_iou", "mask_margin", "cls_imgsz", "cls_conf", "crop_pad",
                 "det_classes", "agnostic_nms", "max_box_frac", "max_box_px")


class _OnJetson:
    """등급 분류는 젯슨에서 이미 했으므로 여기서는 부르지 않는다."""

    def classify(self, crops):
        raise RuntimeError("등급 분류는 젯슨 추론 창구에서 합니다")


class RemoteHybridDetector(HybridBudDetector):
    @classmethod
    def from_config(cls, weights=None, cls_weights=None, sam_weights=None, cls_backend="yolo", vlm=None, cascade_below=0.8,
                    embed=None, review=False, add_missed=False, type=None, **cfg):
        if cls_backend != "yolo":
            raise ValueError(f"젯슨 추론에서는 cls_backend: yolo 만 지원합니다 (지금 {cls_backend})")
        reviewer = VlmGradeClassifier.from_config(**(vlm or {})) if review else None
        missed = VlmMissedFinder.from_config(vlm=vlm, crop_padding=cfg.get("crop_padding", 0.1)) if add_missed else None
        return cls(None, _OnJetson(), None, review=reviewer, missed=missed, **cfg)

    def detect(self, image):
        raise RuntimeError("봉오리 검출은 젯슨 추론 창구에서 합니다 (RemoteRosePipeline 을 쓰세요)")


def to_detection(item: dict) -> BudDetection:
    mask = None
    if item.get("mask_png"):
        mask = cv2.imdecode(np.frombuffer(base64.b64decode(item["mask_png"]), np.uint8), cv2.IMREAD_GRAYSCALE) > 127
    return BudDetection(item["cls"], float(item["conf"]), np.float32(item["xyxy"]), mask=mask,
                        mask_xy=tuple(item["mask_xy"]) if mask is not None else None, det_conf=item.get("det_conf"),
                        crop_xyxy=None if item.get("crop_xyxy") is None else np.float32(item["crop_xyxy"]))


class RemoteRosePipeline(RosePipeline):
    def __init__(self, cfg, remote_cfg: remote.RemoteConfig):
        det = dict(cfg["grading"]["detector"])
        if det.get("type") != "hybrid":
            raise ValueError(f"젯슨 추론에서는 detector.type: hybrid 만 지원합니다 (지금 {det.get('type')})")
        super().__init__(cfg, detector=RemoteHybridDetector.from_config(**det))
        self.remote_cfg = remote_cfg
        self.paths: dict[str, str] = {}
        self._found: dict[str, list] = {}
        self.remote_info: dict = {}

    # ── 젯슨 요청 ─────────────────────────────────────────────────────
    def plant_rects(self, image, plants):
        """detect_buds 와 같은 영역: 식물 bbox + 여백. 반환 [(식물 순서의 사각형)] (마스크가 빈 식물은 뺌)."""
        h, w = image.shape[:2]
        det = self.cfg["grading"]["detector"]
        if det.get("detect_on", "plant_crop") == "full_image":
            return [(0, 0, w, h)]
        pad, out = det.get("crop_padding", 0.1), []
        for p in plants:
            box = p.bbox()
            if box is None:
                continue
            x1, y1, x2, y2 = (int(v) for v in box)
            px, py = int((x2 - x1) * pad), int((y2 - y1) * pad)
            out.append((max(x1 - px, 0), max(y1 - py, 0), min(x2 + px, w), min(y2 + py, h)))
        return out

    def run(self, images=None, paths=None, reuse=False):
        """reuse=True 면 젯슨에 다시 묻지 않고 앞서 받은 결과로 다시 판단한다 (Gemini 검수가 실패했을 때)."""
        self._order = list(images)
        self._next = 0
        if reuse:
            return super().run(images)
        self.paths = dict(paths or {})
        holes = {name: self.holes_for(name, img) for name, img in images.items()}
        presence = self.detect_presence(images, holes)
        rects = {name: self.plant_rects(img, self.separate_plants(name, img, holes[name], presence)) for name, img in images.items()}

        self._found, self.remote_info = {}, {}
        ask = {cam: r for cam, r in rects.items() if r}
        if ask:
            det = self.cfg["grading"]["detector"]
            resp = remote.growth(self.remote_cfg, {cam: self.paths[cam] for cam in ask}, ask,
                                 {k: det[k] for k in DETECTOR_KEYS if k in det})
            res = resp["result"]
            for cam, r in ask.items():
                got = res["cameras"][cam]
                h, w = images[cam].shape[:2]
                if got["size"] != [w, h] or len(got["groups"]) != len(r):
                    raise remote.RemoteError(f"{cam}: 젯슨 결과가 요청과 맞지 않습니다 (크기 {got['size']}, 영역 {len(got['groups'])})")
                self._found[cam] = list(zip(r, got["groups"]))
            for k in ("candidates", "classified"):
                self.detector.stats[k] += res["stats"][k]
            self.remote_info = dict(elapsed_s=resp["elapsed_s"], timing=res["timing"], gpu=res["gpu"])
        return super().run(images)

    # ── 등급 판단 (RosePipeline.grade_plants 대신) ────────────────────
    def grade_plants(self, image, plants):
        cam = self._order[self._next]       # RosePipeline.run 이 images 순서대로 부른다
        self._next += 1
        dets, crops = [], []
        for (x1, y1, _, _), group in self._found.get(cam, []):
            for item in group:
                d = to_detection(item).shifted(x1, y1)
                cx1, cy1, cx2, cy2 = (int(round(v)) for v in d.crop_xyxy)
                dets.append(d)
                crops.append(image[cy1:cy2, cx1:cx2])
        if self.detector.review is not None:
            dets = self.detector.review_buds(dets, crops)
        buds = dedupe(dets)
        assign_buds(buds, plants, self.assigner)
        buds = self.detector.add_missed(image, plants, buds)
        return decide_grades(plants, buds, self.grades), buds
