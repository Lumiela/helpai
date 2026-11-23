"""장미 생장 단계 모듈 어댑터 (helpai-Rose_Growth / rose_pipeline).

입력 : {카메라 이름: BGR 이미지}  (CL, CR — holes.yaml 의 카메라 이름과 같아야 함)
출력 : {
    "grades":   [낮은 → 높은 등급],
    "cells":    [CellResult.to_dict() ...]   칸(R행C열)별 식물 유무·최종 등급·봉오리,
    "segments": {카메라: {칸: (mask, offset)}}   식물 마스크 (원본 이미지 좌표) — 높이·잎 이상을 칸에 묶을 때 사용,
    "table":    트레이 등급 표 (문자열),
    "usage":    Claude 요청·토큰 수 등,
}
"""

from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path

from crop_monitor.keys import read_key
from crop_monitor.registry import resolve

from .rose_pipeline import RosePipeline
from .rose_pipeline.config import load_config
from .rose_pipeline.visualize import save_outputs


def _needs_api(det: dict) -> bool:
    if det.get("type") == "vlm_plant":
        return True
    return det.get("type") == "hybrid" and (
        bool(det.get("review")) or bool(det.get("add_missed")) or det.get("cls_backend") in ("vlm", "cascade"))


def _prepare_config(config_path: str, has_key: bool, on_missing_key: str) -> tuple[dict, list[str]]:
    cfg = copy.deepcopy(load_config(config_path))
    det = cfg["grading"]["detector"]
    warnings = []
    if _needs_api(det) and not has_key:
        if on_missing_key != "disable_vlm" or det.get("type") == "vlm_plant":
            raise FileNotFoundError("생장 단계: Claude API 키가 필요합니다 → secrets/ANTHROPIC_API_KEY 에 키를 넣으세요")
        det["review"] = False
        det["add_missed"] = False
        if det.get("cls_backend") in ("vlm", "cascade"):
            det["cls_backend"] = "yolo"
        warnings.append("생장 단계: API 키가 없어 Claude 검수·누락 추가를 끄고 YOLO 결과만 사용")
    return cfg, warnings


@lru_cache(maxsize=4)
def _pipeline(config_path: str, has_key: bool, on_missing_key: str) -> tuple[RosePipeline, tuple[str, ...]]:
    cfg, warnings = _prepare_config(config_path, has_key, on_missing_key)
    return RosePipeline(cfg), tuple(warnings)


def run(images: dict, spec: dict, out_dir: Path | None = None) -> tuple[dict, list[str]]:
    config_path = str(resolve(spec.get("config", "config/rose/growth.yaml")))
    pipeline, warnings = _pipeline(config_path, read_key() is not None, spec.get("on_missing_key", "disable_vlm"))

    result = pipeline.run(dict(images))

    if out_dir is not None:
        out = pipeline.cfg.get("output", {})
        save_outputs(result, Path(out_dir) / "growth", out.get("save_visualization", True), out.get("save_masks", True))

    segments = {cam: {p.cell_id: (p.mask, p.offset) for p in f.plants} for cam, f in result.frames.items()}
    usage = getattr(pipeline.detector, "usage", None)
    return {
        "grades": list(result.grades),
        "cells": [c.to_dict() for c in result.cells],
        "segments": segments,
        "table": result.table(),
        "usage": dict(usage) if usage else None,
    }, list(warnings)
