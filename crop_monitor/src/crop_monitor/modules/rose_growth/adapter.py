"""장미 생장 단계 모듈 어댑터 — 추론은 젯슨, 검수는 Gemini (원본: helpai-Rose_Growth / rose_pipeline).

입력 : {카메라 이름: BGR 이미지}  (CL, CR — holes.yaml 의 카메라 이름과 같아야 함), paths {카메라: 원본 사진 경로}
출력 : {
    "grades":   [낮은 → 높은 등급],
    "cells":    [CellResult.to_dict() ...]   칸(R행C열)별 식물 유무·최종 등급·봉오리,
    "segments": {카메라: {칸: (mask, offset)}}   식물 마스크 (원본 이미지 좌표) — 높이·잎 이상을 칸에 묶을 때 사용,
    "table":    트레이 등급 표 (문자열),
    "usage":    후보·분류·검수 개수 + Gemini 요청·토큰 수,
    "remote":   젯슨 처리 시간·GPU 메모리,
}
"""

from __future__ import annotations

import copy
from pathlib import Path

from crop_monitor.gemini import GeminiError
from crop_monitor.keys import WHERE, read_key
from crop_monitor.registry import remote_config, resolve

from .remote_pipeline import RemoteRosePipeline
from .rose_pipeline.config import load_config
from .rose_pipeline.visualize import save_outputs


def _needs_api(det: dict) -> bool:
    return bool(det.get("review")) or bool(det.get("add_missed"))


def _prepare_config(config_path: str, has_key: bool, on_missing_key: str, use_vlm: bool = True) -> tuple[dict, list[str]]:
    cfg = copy.deepcopy(load_config(config_path))
    det = cfg["grading"]["detector"]
    warnings = []
    if not use_vlm:
        det["review"] = det["add_missed"] = False
    elif _needs_api(det) and not has_key:
        if on_missing_key != "disable_vlm":
            raise FileNotFoundError(f"생장 단계: Gemini API 키가 필요합니다 → {WHERE}")
        det["review"] = det["add_missed"] = False
        warnings.append("생장 단계: API 키가 없어 Gemini 검수·누락 추가를 끄고 YOLO 결과만 사용")
    return cfg, warnings


def run(images: dict, spec: dict, out_dir: Path | None = None, paths: dict | None = None) -> tuple[dict, list[str]]:
    config_path = str(resolve(spec.get("config", "config/rose/growth.yaml")))
    args = (config_path, read_key() is not None, spec.get("on_missing_key", "disable_vlm"))
    use_vlm = spec.get("use_vlm", True)
    cfg, warnings = _prepare_config(*args, use_vlm)
    pipeline = RemoteRosePipeline(cfg, remote_config())
    try:
        result = pipeline.run(dict(images), paths)
    except GeminiError as e:  # 검수가 실패해도 젯슨 결과로 계속 (경고에 남김)
        warnings.append(f"생장 단계: Gemini 검수를 하지 못해 YOLO 결과만 사용 ({e})")
        pipeline.detector.review = pipeline.detector.missed = None
        result = pipeline.run(dict(images), paths, reuse=True)

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
        "remote": pipeline.remote_info,
    }, list(warnings)
