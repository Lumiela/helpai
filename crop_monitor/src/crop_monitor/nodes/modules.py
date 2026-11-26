"""병렬 모듈 노드: 생장 단계, 이상 상태, 높이.

생장 단계·이상 상태는 작물 레지스트리(config/crops.yaml)의 module 이름으로
src/crop_monitor/modules/<module>/adapter.py 를 불러 실행합니다. 작물 N 을 추가할 때 이 파일은 수정하지 않습니다.
한 모듈이 실패해도 나머지 결과로 보고서를 만들 수 있도록 예외는 경고로 남깁니다.
"""

from __future__ import annotations

import importlib
import tempfile
import traceback
from pathlib import Path

import numpy as np

from crop_monitor import remote
from crop_monitor.modules.height import height as height_mod
from crop_monitor.registry import get_crop, height_config, remote_config, stereo_pair
from crop_monitor.state import PipelineState


def _adapter(module: str):
    return importlib.import_module(f"crop_monitor.modules.{module}.adapter")


def _run_adapter(state: PipelineState, kind: str, label: str) -> PipelineState:
    spec = getattr(get_crop(state["crop_id"]), kind)
    if not spec:
        return {kind: None}
    out_dir = Path(state["out_dir"]) if state.get("out_dir") else None
    try:
        spec = {**spec, "use_vlm": state.get("use_vlm", True)}
        result, warnings = _adapter(spec["module"]).run(state["frames"], spec, out_dir, paths=state["images"])
    except Exception as e:
        traceback.print_exc()
        return {kind: None, "warnings": [f"[{label}] 실패: {type(e).__name__}: {e}"]}
    return {kind: result, "warnings": [f"[{label}] {w}" for w in warnings]}


def growth_stage(state: PipelineState) -> PipelineState:
    return _run_adapter(state, "growth", "생장 단계")


def disease(state: PipelineState) -> PipelineState:
    return _run_adapter(state, "disease", "이상 상태")


HEIGHT_KEYS = ("mode", "disp_offset", "block", "min_dist_cm", "max_dist_cm", "resize", "baseline_mm", "hfov_deg", "reference",
               "reference_cm", "ransac_thresh_cm", "top_percentile", "min_valid_px", "max_rect_dy_px")


def height(state: PipelineState) -> PipelineState:
    """스테레오 높이 지도를 젯슨에서 받아 온다. 보정 파일은 젯슨 한 곳에만 둔다."""
    cfg, rcfg = height_config(), remote_config()
    left, right = stereo_pair()
    try:
        resp, blob = remote.height(rcfg, state["images"][left], state["images"][right], {k: getattr(cfg, k) for k in HEIGHT_KEYS})
    except Exception as e:
        traceback.print_exc()
        return {"height": None, "warnings": [f"[높이] 실패: {type(e).__name__}: {e}"]}
    r = resp["result"]

    # 보정이 지금 카메라와 맞지 않으면 높이를 내지 않는다 (틀린 숫자를 보고서에 싣지 않기 위함)
    chk, bad = r["check"], []
    if chk["mode"] != "calib":
        bad.append(f"보정 모드가 아님({chk['mode']})")
    if chk["dy_px"] is None:
        bad.append(f"정렬 확인용 점이 부족함({chk['matches']}개)")
    else:
        if chk["dy_px"] > cfg.max_rect_dy_px:
            bad.append(f"정렬 뒤 세로 어긋남 {chk['dy_px']}px (기준 {cfg.max_rect_dy_px}px)")
        if chk["disparity_px"] <= 0:
            bad.append(f"시차가 음수 {chk['disparity_px']}px (좌우가 바뀜)")
    if bad:
        msg = "[높이] 스테레오 보정이 지금 카메라와 맞지 않음: " + ", ".join(bad) + " → 다시 보정하세요"
        if rcfg.on_bad_calib == "skip":
            return {"height": None, "warnings": [msg + " (높이는 내지 않음)"]}
        warnings = [msg]
    else:
        warnings = []

    out = Path(state["out_dir"]) / "height" if state.get("out_dir") else Path(tempfile.mkdtemp(prefix="crop-height-"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "height.npz").write_bytes(blob)
    d = np.load(out / "height.npz")
    np.savez(out / "calib.npz", **{k[6:]: d[k] for k in d.files if k.startswith("calib_")})
    info = {**r["info"], "calib": str(out / "calib.npz"), "check": chk, "jetson_s": resp["elapsed_s"]}
    res = height_mod.HeightResult(d["height_cm"], d["depth_cm"], d["plane"], tuple(r["src_size"]), info)
    if state.get("out_dir"):
        import cv2

        cv2.imwrite(str(out / "depth_color.png"), height_mod.sp.color_depth(res.depth_cm))
        cv2.imwrite(str(out / "height_color.png"), height_mod.sp.color_depth(-res.height_cm))  # 높을수록 빨강
    return {"height": res, "warnings": warnings}
