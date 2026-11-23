"""병렬 모듈 노드: 생장 단계, 이상 상태, 높이.

생장 단계·이상 상태는 작물 레지스트리(config/crops.yaml)의 module 이름으로
src/crop_monitor/modules/<module>/adapter.py 를 불러 실행합니다. 작물 N 을 추가할 때 이 파일은 수정하지 않습니다.
한 모듈이 실패해도 나머지 결과로 보고서를 만들 수 있도록 예외는 경고로 남깁니다.
"""

from __future__ import annotations

import importlib
import traceback
from pathlib import Path

from crop_monitor.modules.height import height as height_mod
from crop_monitor.registry import get_crop, height_config, stereo_pair
from crop_monitor.state import PipelineState


def _adapter(module: str):
    return importlib.import_module(f"crop_monitor.modules.{module}.adapter")


def _run_adapter(state: PipelineState, kind: str, label: str) -> PipelineState:
    spec = getattr(get_crop(state["crop_id"]), kind)
    if not spec:
        return {kind: None}
    out_dir = Path(state["out_dir"]) if state.get("out_dir") else None
    try:
        result, warnings = _adapter(spec["module"]).run(state["frames"], spec, out_dir)
    except Exception as e:
        traceback.print_exc()
        return {kind: None, "warnings": [f"[{label}] 실패: {type(e).__name__}: {e}"]}
    return {kind: result, "warnings": [f"[{label}] {w}" for w in warnings]}


def growth_stage(state: PipelineState) -> PipelineState:
    return _run_adapter(state, "growth", "생장 단계")


def disease(state: PipelineState) -> PipelineState:
    return _run_adapter(state, "disease", "이상 상태")


def height(state: PipelineState) -> PipelineState:
    cfg = height_config()
    left, right = stereo_pair()
    warnings = []
    if not (cfg.calib and cfg.calib.exists()):
        warnings.append(f"[높이] 보정 파일 없음({cfg.calib}) → 근사 모드로 계산")
    try:
        res = height_mod.compute(state["frames"][left], state["frames"][right], cfg)
    except Exception as e:
        traceback.print_exc()
        return {"height": None, "warnings": warnings + [f"[높이] 실패: {type(e).__name__}: {e}"]}

    dy = res.info["align"].get("rect_dy_median_px")
    if dy is not None and dy > cfg.max_rect_dy_px:
        warnings.append(f"[높이] 정렬 후 세로 어긋남 {dy}px (기준 {cfg.max_rect_dy_px}px 초과) → 카메라가 움직였거나 "
                        f"좌우(cameras.stereo_left/right)가 바뀌었을 수 있음. 높이 값을 믿기 어려우니 다시 보정하세요")

    if state.get("out_dir"):
        import cv2

        d = Path(state["out_dir"]) / "height"
        d.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(d / "depth_color.png"), height_mod.sp.color_depth(res.depth_cm))
        cv2.imwrite(str(d / "height_color.png"), height_mod.sp.color_depth(-res.height_cm))  # 높을수록 빨강
    return {"height": res, "warnings": warnings}
