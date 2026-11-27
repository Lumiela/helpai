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
    warnings = list(warnings)
    holes_info = _hole_report(pipeline, images, out_dir, warnings)
    return {
        "grades": list(result.grades),
        "cells": [c.to_dict() for c in result.cells],
        "segments": segments,
        "table": result.table(),
        "usage": dict(usage) if usage else None,
        "holes": holes_info,
    }, warnings


def _hole_report(pipeline, images: dict, out_dir: Path | None, warnings: list) -> dict | None:
    """구멍 자동 찾기 결과 요약 + 확인 그림(growth/holes_<카메라>.jpg) + 경고."""
    fits = getattr(pipeline, "hole_fits", None)
    if not fits:
        return None
    auto = pipeline.cfg.get("holes_auto") or {}
    warn_shift = float(auto.get("warn_shift_ratio", 0.25))
    info = {}
    for cam, f in fits.items():
        info[cam] = {k: f.get(k) for k in ("used", "ok", "score", "shift_ratio", "reason", "holes")}
        if not f.get("ok"):
            warnings.append(f"구멍 자동 찾기 실패({cam}): {f.get('reason')} → holes.yaml 위치 사용")
        elif f.get("shift_ratio") is not None and f["shift_ratio"] > warn_shift:
            warnings.append(f"구멍 위치({cam})가 holes.yaml 과 간격의 {f['shift_ratio']:.2f}배 다름 → 트레이·카메라가 "
                            f"움직였음. 자동 위치 사용 중 (holes.yaml 갱신: scripts/rose_growth/find_holes.py --write)")
        if out_dir is not None and f.get("fit") is not None:
            import cv2

            from .rose_pipeline.hole_finder import draw_fit

            d = Path(out_dir) / "growth"
            d.mkdir(parents=True, exist_ok=True)
            vis = draw_fit(images[cam], f["fit"], title=cam)
            s = 1280 / vis.shape[1]
            cv2.imwrite(str(d / f"holes_{cam}.jpg"), cv2.resize(vis, None, fx=s, fy=s, interpolation=cv2.INTER_AREA))
    return info
