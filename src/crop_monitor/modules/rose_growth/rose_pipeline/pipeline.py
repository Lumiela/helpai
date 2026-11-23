from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from .assignment import get_assigner
from .config import load_config
from .detection import BudDetection, build_detector
from .fusion import get_fuser
from .grading import assign_buds, decide_grades, detect_buds
from .layout import Hole, all_cells, camera_holes, coverage_map, parse_cell_id
from .presence import build_presence
from .segmentation import PlantSegment, build_segmenter
from .sources import build_source


@dataclass
class CellResult:
    cell_id: str
    row: int
    col: int
    cameras: list                 # 범위표상 이 칸을 판단하는 카메라
    present: bool | None          # None = 볼 수 있는 카메라가 없음
    empty_scores: dict            # {카메라: 빈 구멍 점수}
    camera_grades: dict           # {카메라: 그 카메라에서 판단한 등급}
    grade: str | None             # 최종 등급 (빈 구멍이면 None)
    buds: list = field(default_factory=list)

    def to_dict(self):
        return {
            "cell": self.cell_id, "row": self.row, "col": self.col, "cameras": self.cameras,
            "present": self.present, "grade": self.grade,
            "empty_scores": {k: round(v, 3) for k, v in self.empty_scores.items()},
            "camera_grades": self.camera_grades,
            "buds": [{"camera": cam, "class": b.class_name, "conf": round(b.conf, 3),
                      "xyxy": [round(float(v), 1) for v in b.xyxy],
                      **({"det_conf": round(b.det_conf, 3)} if b.det_conf is not None else {}),
                      **({"cls_class": b.first_class, "cls_conf": round(b.first_conf, 3)}
                         if b.first_class is not None else {}),
                      **({"added_by": "claude"} if b.added else {}),
                      **({"mask_px": int(b.mask.sum())} if b.mask is not None else {})}
                     for cam, b in self.buds],
        }

    @property
    def symbol(self):
        if not self.present:
            return "?" if self.present is None else "-"
        return self.grade or "?"


@dataclass
class CameraFrame:
    """카메라 하나의 중간 결과 (시각화·디버깅용)."""
    name: str
    image: np.ndarray
    holes: dict[str, Hole]
    plants: list[PlantSegment]
    buds: list[BudDetection]
    vlm: dict | None = None       # vlm_plant: {칸: {summary, buds(버린 것 포함), view, view_xy}}


@dataclass
class PipelineResult:
    timestamp: str
    rows: int
    cols: int
    cells: list[CellResult]
    frames: dict[str, CameraFrame]
    grades: list = field(default_factory=list)  # 낮은 → 높은 등급 순서

    def to_dict(self):
        cells = []
        for c in self.cells:
            d = c.to_dict()
            vlm = {cam: vlm_note_dict(f.vlm[c.cell_id]) for cam, f in self.frames.items()
                   if f.vlm and c.cell_id in f.vlm}
            if vlm:
                d["vlm"] = vlm  # VLM 이 이 식물에 대해 답한 내용 (확신이 낮아 버린 봉오리 포함)
            cells.append(d)
        return {"timestamp": self.timestamp, "table": self.grid(), "cells": cells}

    def vlm_table(self):
        """VLM 판단 요약 문자열 (vlm_plant 일 때만, 아니면 빈 문자열)."""
        if not any(f.vlm for f in self.frames.values()):
            return ""
        lines = ["VLM 판단 (칸 / 카메라: 요약 | 답한 봉오리, x = 확신이 낮아 버림)"]
        for c in self.cells:
            for cam, f in self.frames.items():
                note = (f.vlm or {}).get(c.cell_id)
                if note is None:
                    continue
                buds = ", ".join(f"{b['grade']} {b['conf']:.2f}{'' if b['kept'] else ' x'}" for b in note["buds"])
                lines.append(f"{c.cell_id} {cam}: {note['summary'] or '-'} | {buds or '봉오리 없음'}")
        return "\n".join(lines)

    def grid(self):
        by = {(c.row, c.col): c.symbol for c in self.cells}
        return [[by[(r, c)] for c in range(1, self.cols + 1)] for r in range(1, self.rows + 1)]

    def table(self):
        """행×열 표 문자열. 식물 = 등급, 빈 구멍 = '-', 볼 수 없음 = '?'"""
        w = 13
        lines = [" " * 5 + "".join(f"{f'{c}열':^{w - 1}}" for c in range(1, self.cols + 1))]
        for r, row in enumerate(self.grid(), 1):
            lines.append(f"{r}행  " + "".join(f"{s:^{w}}" for s in row))
        return "\n".join(lines)

    def bud_table(self):
        """식물별 봉오리 개수 문자열. 같은 봉오리가 두 카메라에 보이면 카메라마다 따로 셈."""
        rank = {g: i for i, g in enumerate(self.grades)}
        lines = ["봉오리 (카메라별 개수)"]
        for c in self.cells:
            if not c.present:
                continue
            parts = []
            for cam in c.camera_grades:
                n = Counter(b.class_name for k, b in c.buds if k == cam)
                text = ", ".join(f"{g} {n[g]}개" for g in sorted(n, key=lambda g: -rank.get(g, -1)))
                parts.append(f"{cam}: {text or '없음'}")
            lines.append(f"{c.cell_id}  {c.symbol:<13}" + "  |  ".join(parts))
        lost = sum(b.cell_id is None for f in self.frames.values() for b in f.buds)
        if lost:
            lines.append(f"어느 식물에도 배정되지 않은 봉오리: {lost}개 (vis_*.jpg 의 '-> none')")
        return "\n".join(lines)


class RosePipeline:
    def __init__(self, config="config/rose/growth.yaml", detector=None):
        self.cfg = load_config(config) if isinstance(config, str) else config
        self.layout = self.cfg["layout"]
        self.coverage = coverage_map(self.layout)
        self.presence = build_presence(self.cfg["presence"], self.layout)
        self.segmenter = build_segmenter(self.cfg["segmentation"])
        g = self.cfg["grading"]
        self.grades = g["grades"]
        self.detector = detector or build_detector(g["detector"])
        self.assigner = get_assigner(g["assignment"])
        self.fuser = get_fuser(g.get("fusion", "max"))

    def holes_for(self, camera, image):
        return camera_holes(self.cfg["holes"].get(camera), image.shape, self.cfg.get("radius_ratio", 0.3))

    # ── 1단계: 각각의 미니 장미 구분 ──────────────────────────
    def detect_presence(self, images, holes):
        """구멍마다 식물 유무. 점수는 범위표의 카메라 중 그 구멍이 보이는 카메라에서 계산.
        반환: {칸: (True/False/None, {카메라: 점수})}"""
        result = {}
        for cid in all_cells(self.layout):
            scores = {cam: self.presence.score(images[cam], holes[cam][cid])
                      for cam in self.coverage[cid] if cam in holes and cid in holes[cam]}
            scores = {k: v for k, v in scores.items() if not np.isnan(v)}
            result[cid] = (self.presence.is_present(cid, list(scores.values())), scores)
        return result

    def separate_plants(self, camera, image, holes, presence):
        # 범위 밖이어도 식물이 있는 구멍은 모두 기준점으로 써서, 그 잎이 이웃 식물로 섞이지 않게 함
        plant_holes = [h for cid, h in holes.items() if presence[cid][0]]
        return self.segmenter.segment(image, camera, plant_holes)

    # ── 2단계: 생장 단계 판단 ─────────────────────────────────
    def grade_plants(self, image, plants):
        if hasattr(self.detector, "grade_plants"):  # vlm_plant: 식물마다 VLM 이 봉오리를 찾아 이미 식물을 정함
            buds = self.detector.grade_plants(image, plants)
            return decide_grades(plants, buds, self.grades), buds
        det_cfg = self.cfg["grading"]["detector"]
        buds = detect_buds(image, plants, self.detector,
                           det_cfg.get("detect_on", "plant_crop"), det_cfg.get("crop_padding", 0.1))
        assign_buds(buds, plants, self.assigner)
        if hasattr(self.detector, "add_missed"):  # 혼합형 Claude 검수: 놓친 봉오리 추가 (이미 식물이 정해져 있음)
            buds = self.detector.add_missed(image, plants, buds)  # 추가 + 중복 검수 봉오리 정리
        return decide_grades(plants, buds, self.grades), buds

    def run(self, images=None) -> PipelineResult:
        """images: {카메라 이름: BGR 이미지}. 생략하면 설정의 source 에서 읽음."""
        images = dict(images or {})
        for name, cam in self.cfg["cameras"].items():
            if name not in images:
                images[name] = build_source(cam["source"]).read()
        holes = {name: self.holes_for(name, img) for name, img in images.items()}

        presence = self.detect_presence(images, holes)

        frames, cam_grades = {}, {}
        for name, image in images.items():
            plants = self.separate_plants(name, image, holes[name], presence)
            grades, buds = self.grade_plants(image, plants)
            cam_grades[name] = grades
            notes = getattr(self.detector, "last_notes", None)
            frames[name] = CameraFrame(name, image, holes[name], plants, buds, dict(notes) if notes else None)

        cells = []
        for cid in all_cells(self.layout):
            row, col = parse_cell_id(cid)
            present, scores = presence[cid]
            per_cam = {cam: cam_grades[cam][cid] for cam in self.coverage[cid]
                       if cam in cam_grades and cid in cam_grades[cam]}
            grade = self.fuser(per_cam, self.grades) if present and per_cam else None
            buds = [(cam, b) for cam in per_cam for b in frames[cam].buds if b.cell_id == cid]
            cells.append(CellResult(cid, row, col, self.coverage[cid], present, scores, per_cam, grade, buds))
        return PipelineResult(datetime.now().isoformat(timespec="seconds"),
                              self.layout["rows"], self.layout["cols"], cells, frames, self.grades)


def vlm_note_dict(note):
    return {"summary": note["summary"],
            "buds": [{"class": b["grade"], "conf": round(b["conf"], 3), "kept": b["kept"],
                      "xyxy": [round(float(v), 1) for v in b["xyxy"]]} for b in note["buds"]]}
