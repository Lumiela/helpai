"""작물 높이 공통 모듈 — 천장 카메라 2대 스테레오.

거리 계산은 stereo_pair.py (helpai-Height 원본) 의 정렬 → 시차 → 거리 계산을 그대로 쓰고,
여기서는 그 위에 '기준면(바닥/트레이) 대비 높이' 계산을 더한다.

    거리(cm)   : 카메라 → 표면 (stereo_pair.depth_map 과 같은 값)
    높이(cm)   : 기준면 → 표면. 기준면은 RANSAC 으로 찾은 가장 넓은 평면, 또는 설정한 고정 거리
    좌표       : 결과 지도는 '정렬된 왼쪽(C2) 이미지' 좌표 (캘리브레이션 크기, 예: 640x480).
                 원본 왼쪽 이미지 좌표는 to_map_coords() 로 바꿔서 읽는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from . import stereo_pair as sp


@dataclass
class HeightConfig:
    calib: Path | None              # stereo_calib.npz. None/없음 → 근사 모드 (y-align + 화각·간격 추정)
    mode: str = "auto"              # auto | y-align | none (stereo_pair --mode)
    disp_offset: float = 0.0        # 시차 보정값 px. 캘리브레이션 모드 최신값(v4) = 0, 근사 모드 v3 = 4.1
    block: int = 5
    min_dist_cm: float = 20.0
    max_dist_cm: float = 500.0
    resize: int = 640               # 근사 모드 계산 해상도
    baseline_mm: float = 33.3       # 근사 모드에서만 사용 (캘리브레이션 실측값)
    hfov_deg: float = 56.0          # 근사 모드에서만 사용 (캘리브레이션 실측값)
    reference: str = "ransac"       # ransac | fixed
    reference_cm: float | None = None   # fixed: 카메라 → 기준면 거리(cm, 줄자 실측)
    ransac_thresh_cm: float = 1.0
    top_percentile: float = 5.0     # 식물 영역 높이 중 상위 몇 %를 꼭대기로 볼지
    min_valid_px: int = 30          # 식물 영역에서 유효 높이 픽셀이 이보다 적으면 측정 불가
    max_rect_dy_px: float = 2.0     # 정렬 후 세로 어긋남이 이보다 크면 경고 (보정 당시 0.68px)


@dataclass
class HeightResult:
    height_cm: np.ndarray           # (h, w) 기준면 대비 높이, 측정 불가 NaN
    depth_cm: np.ndarray            # (h, w) 카메라 → 표면 거리, 측정 불가 NaN
    plane: np.ndarray               # (a, b, c, d) cm. 높이 = (a,b,c)·P + d
    src_size: tuple[int, int]       # 원본 왼쪽 이미지 (W, H)
    info: dict = field(default_factory=dict)


@lru_cache(maxsize=4)
def _load_calib(path: str) -> dict:
    d = np.load(path)
    return {k: d[k] for k in d.files}


def _fit_plane(pts: np.ndarray, thresh: float, iters: int = 300, sample: int = 20000, seed: int = 0) -> np.ndarray:
    """RANSAC 평면. 카메라 원점이 평면의 + 쪽이 되도록 부호를 맞춤 → 높이 양수."""
    pts = pts[np.isfinite(pts).all(axis=1)]
    if len(pts) < 50:
        raise ValueError("유효한 3D 점이 부족해 기준면을 찾을 수 없습니다")
    rng = np.random.default_rng(seed)
    if len(pts) > sample:
        pts = pts[rng.choice(len(pts), sample, replace=False)]
    best, best_n = None, -1
    for _ in range(iters):
        p = pts[rng.choice(len(pts), 3, replace=False)]
        n = np.cross(p[1] - p[0], p[2] - p[0])
        norm = np.linalg.norm(n)
        if norm < 1e-9:
            continue
        n /= norm
        d = -n @ p[0]
        k = int((np.abs(pts @ n + d) < thresh).sum())
        if k > best_n:
            best, best_n = np.append(n, d), k
    # 인라이어로 다시 최소제곱
    inl = pts[np.abs(pts @ best[:3] + best[3]) < thresh]
    c = inl.mean(axis=0)
    n = np.linalg.svd(inl - c)[2][-1]
    plane = np.append(n, -n @ c)
    return -plane if plane[3] < 0 else plane


def compute(left_bgr: np.ndarray, right_bgr: np.ndarray, cfg: HeightConfig) -> HeightResult:
    """스테레오 왼쪽(C2), 오른쪽(C1) 원본 이미지 → 높이 지도."""
    calib = str(cfg.calib) if cfg.calib and Path(cfg.calib).exists() else None
    L, R, Q, info_align = sp.align(left_bgr, right_bgr, calib, cfg.mode, cfg.resize, cfg.hfov_deg, cfg.baseline_mm)

    if info_align["mode"] == "calib":
        # 정렬 품질 확인: 정렬 뒤 같은 점의 세로 위치 차이 (보정 당시 0.68px). 크면 카메라가 움직였거나 좌우가 바뀐 것
        pl, pr = sp.match_points(L, R)
        if len(pl) >= 20:
            info_align["rect_dy_median_px"] = round(float(np.median(np.abs(pl[:, 1] - pr[:, 1]))), 2)
        # stereo_pair.main 과 같은 방식: 거리 범위로 시차 탐색 범위를 정함
        f_px, base_mm = float(Q[2, 3]), 1.0 / abs(float(Q[3, 2]))
        max_d = f_px * base_mm / (cfg.min_dist_cm * 10)
        scene_min_d = f_px * base_mm / (cfg.max_dist_cm * 10)
        num_disp = int(min(max(int(np.ceil((max_d + 16) / 16) * 16), 16), (L.shape[1] // 16) * 16))
        info_align.update(rect_f_px=round(f_px, 1), rect_baseline_mm=round(base_mm, 2))
        L, R, disp, valid, info_disp = sp.disparity(L, R, cfg.block, allow_swap=False,
                                                    disp_range=(0, num_disp, scene_min_d))
    else:
        L, R, disp, valid, info_disp = sp.disparity(L, R, cfg.block)

    depth_cm = sp.depth_map(disp, valid, Q, cfg.disp_offset, cfg.max_dist_cm)

    # 3D 점 (cm) — depth_map 과 같은 시차·보정값
    disp_corr = np.where(valid, disp + cfg.disp_offset, 0).astype(np.float32)
    pts = cv2.reprojectImageTo3D(disp_corr, Q).astype(np.float32) / 10.0
    pts[~np.isfinite(depth_cm)] = np.nan

    if cfg.reference == "fixed":
        if cfg.reference_cm is None:
            raise ValueError("reference: fixed 이면 reference_cm(카메라 → 기준면 거리)이 필요합니다")
        plane = np.array([0.0, 0.0, -1.0, float(cfg.reference_cm)])
    else:
        plane = _fit_plane(pts.reshape(-1, 3), cfg.ransac_thresh_cm)

    height_cm = pts @ plane[:3] + plane[3]
    h, w = depth_cm.shape
    ref_center = float(plane[3] / -plane[2]) if abs(plane[2]) > 1e-6 else None  # 화면 중앙선 위 기준면 거리(근사)
    info = dict(align=info_align, disparity=info_disp, reference=cfg.reference,
                reference_center_cm=round(ref_center, 1) if ref_center else None,
                valid_ratio=round(float(np.isfinite(height_cm).mean()), 3),
                map_size=[w, h], calib=calib)
    return HeightResult(height_cm, depth_cm, plane, (left_bgr.shape[1], left_bgr.shape[0]), info)


def to_map_coords(res: HeightResult, xy: np.ndarray) -> np.ndarray:
    """원본 왼쪽 이미지 좌표 (N,2) → 높이 지도 좌표 (N,2)."""
    xy = np.asarray(xy, np.float32).reshape(-1, 2)
    W, H = res.src_size
    mw, mh = res.info["map_size"]
    if res.info["align"]["mode"] == "calib":
        c = _load_calib(res.info["calib"])
        cw, ch = (int(v) for v in c["size"])
        scaled = xy * np.float32([cw / W, ch / H])
        out = cv2.undistortPoints(scaled.reshape(-1, 1, 2), c["K1"], c["D1"], R=c["R1"], P=c["P1"])
        return out.reshape(-1, 2)
    return xy * np.float32([mw / W, mh / H])  # 근사 모드: 왼쪽은 크기만 바뀜


def region_height(res: HeightResult, mask: np.ndarray, offset: tuple[int, int], cfg: HeightConfig,
                  max_samples: int = 8000, seed: int = 0) -> dict | None:
    """원본 왼쪽 이미지의 영역(mask, 좌상단 offset) 안 높이.
    반환: {height_cm(상위 top_percentile%), median_cm, valid_px} 또는 None(측정 불가)"""
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    if len(xs) > max_samples:
        idx = np.random.default_rng(seed).choice(len(xs), max_samples, replace=False)
        xs, ys = xs[idx], ys[idx]
    m = np.rint(to_map_coords(res, np.c_[xs + offset[0], ys + offset[1]])).astype(int)
    h, w = res.height_cm.shape
    ok = (m[:, 0] >= 0) & (m[:, 0] < w) & (m[:, 1] >= 0) & (m[:, 1] < h)
    vals = res.height_cm[m[ok, 1], m[ok, 0]]
    vals = vals[np.isfinite(vals)]
    if len(vals) < cfg.min_valid_px:
        return None
    return dict(height_cm=round(float(np.percentile(vals, 100 - cfg.top_percentile)), 1),
                median_cm=round(float(np.median(vals)), 1), valid_px=int(len(vals)))
