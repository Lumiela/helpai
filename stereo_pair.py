#!/usr/bin/env python3
"""stereo_pair.py — 좌·우 사진 2장으로 스테레오 거리 맵 계산 (젯슨·서버용, 화면·업로드 없음)

stereo_colab_v3.ipynb(최신) 의 3~5번 셀(정렬 → 시차 → 거리)을 명령줄로 옮긴 것. 계산 방식·기본값(72°, 50mm, 보정 4.1)은 v3 와 같다.

    python stereo_pair.py LEFT.jpg RIGHT.jpg --out 결과폴더
        [--calib stereo_calib.npz] [--mode auto|y-align|none]
        [--baseline 50] [--hfov 72] [--resize 640] [--block 5] [--max-dist-cm 500] [--disp-offset 4.1]
        [--point X Y ...] [--roi 9]

결과(--out 폴더):
    depth_cm.npy      거리 맵 (cm, 측정 불가 = NaN)
    depth_color.png   거리 색 지도 (빨강 = 가까움, 검정 = 측정 불가)
    align_check.png   정렬 확인용 좌|우 나란히 그림 (초록 가로줄 기준 같은 높이면 정상)
    result.json       설정·정렬·시차 범위·유효 비율·거리 통계·지정 지점 거리

의존성: opencv-python, numpy (opencv-contrib 가 있으면 WLS 필터가 자동 적용됨)
"""
import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np


def load_img(path):
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit("이미지를 읽을 수 없습니다: %s" % path)
    return img


def match_points(a, b, max_pts=4000):
    """SIFT 특징점 매칭 (Lowe ratio test). 반환: 왼쪽 점들, 오른쪽 점들 (N×2)"""
    ga, gb = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    sift = cv2.SIFT_create(nfeatures=max_pts)
    ka, da = sift.detectAndCompute(ga, None)
    kb, db = sift.detectAndCompute(gb, None)
    if da is None or db is None or len(ka) < 10 or len(kb) < 10:
        return np.empty((0, 2), np.float32), np.empty((0, 2), np.float32)
    knn = cv2.BFMatcher().knnMatch(da, db, k=2)
    good = [m for m, n in (p for p in knn if len(p) == 2) if m.distance < 0.75 * n.distance]
    pa = np.float32([ka[m.queryIdx].pt for m in good])
    pb = np.float32([kb[m.trainIdx].pt for m in good])
    return pa, pb


def align(L0, R0, calib_path, mode_req, resize_width, hfov_deg, baseline_mm):
    """노트북 3번 셀: 캘리브레이션 평행화 / 세로 정렬(y-align) / 정렬 없음. 반환: L, R, Q, 정보"""
    info = {}
    if L0.shape != R0.shape:
        info["note_size"] = "두 사진 크기가 달라 오른쪽을 왼쪽 크기로 맞춤"
        R0 = cv2.resize(R0, (L0.shape[1], L0.shape[0]))

    calib = None
    if mode_req == "auto" and calib_path:
        calib = np.load(calib_path)
        cw, ch = (int(v) for v in calib["size"])
        if (L0.shape[1], L0.shape[0]) != (cw, ch):
            if abs(L0.shape[1] / L0.shape[0] - cw / ch) < 0.01:
                info["note_calib_resize"] = "사진을 캘리브레이션 크기 %dx%d 로 맞춤" % (cw, ch)
                L0 = cv2.resize(L0, (cw, ch), interpolation=cv2.INTER_AREA)
                R0 = cv2.resize(R0, (cw, ch), interpolation=cv2.INTER_AREA)
            else:
                info["note_calib_skipped"] = "사진 비율이 캘리브레이션과 달라 y-align 으로 진행"
                calib = None
    mode = "calib" if calib is not None else ("none" if mode_req == "none" else "y-align")

    if mode == "calib":
        size = (cw, ch)
        m1 = cv2.initUndistortRectifyMap(calib["K1"], calib["D1"], calib["R1"], calib["P1"], size, cv2.CV_32FC1)
        m2 = cv2.initUndistortRectifyMap(calib["K2"], calib["D2"], calib["R2"], calib["P2"], size, cv2.CV_32FC1)
        L = cv2.remap(L0, *m1, cv2.INTER_LINEAR)
        R = cv2.remap(R0, *m2, cv2.INTER_LINEAR)
        Q = calib["Q"]
        info.update(mode=mode, calib=str(calib_path))
        return L, R, Q, info

    if resize_width and L0.shape[1] > resize_width:
        s = resize_width / L0.shape[1]
        L = cv2.resize(L0, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        R = cv2.resize(R0, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    else:
        L, R = L0.copy(), R0.copy()
    h, w = L.shape[:2]

    if mode == "y-align":
        # 오른쪽 사진의 '세로 위치'만 보정 (yL = yR + a + b·x + c·yR). 가로는 건드리지 않음 — '무한히 먼 곳 = 시차 0' 유지
        pl, pr = match_points(L, R)
        if len(pl) < 20:
            info["y_align"] = "매칭점 %d개뿐이라 세로 보정을 건너뜀" % len(pl)
        else:
            dy = pl[:, 1] - pr[:, 1]
            A = np.c_[np.ones(len(pr)), pr[:, 0], pr[:, 1]]
            keep = np.abs(dy - np.median(dy)) < 3 * (np.median(np.abs(dy - np.median(dy))) + 1)
            for _ in range(5):   # 이상치 제거하며 반복 최소제곱
                coef, *_ = np.linalg.lstsq(A[keep], dy[keep], rcond=None)
                res = dy - A @ coef
                keep = np.abs(res) < max(2.0, 3 * np.median(np.abs(res[keep])))
            xs, ys = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
            map_y = ((ys - coef[0] - coef[1] * xs) / (1 + coef[2])).astype(np.float32)
            R = cv2.remap(R, xs, map_y, cv2.INTER_LINEAR)
            info["y_align"] = dict(matches_used=int(keep.sum()), matches=int(len(pl)),
                                   dy_before_px=round(float(np.median(np.abs(dy))), 2),
                                   dy_after_px=round(float(np.median(np.abs(res[keep]))), 2))
    else:
        info["y_align"] = "정렬 없이 진행"

    f_px = (w / 2) / np.tan(np.radians(hfov_deg) / 2)      # 화각으로 초점거리 추정 (근사 측정)
    Q = np.float32([[1, 0, 0, -w / 2],
                    [0, 1, 0, -h / 2],
                    [0, 0, 0, f_px],
                    [0, 0, 1 / baseline_mm, 0]])
    info.update(mode=mode, f_px=round(float(f_px), 1), hfov_deg=hfov_deg, baseline_mm=baseline_mm)
    return L, R, Q, info


def disparity(L, R, block, allow_swap=True, disp_range=None):
    """노트북 4번 셀: 매칭점으로 탐색 범위 자동 설정, 좌우 바뀜 감지, 왼쪽 여백 처리, SGBM(+WLS). 반환: L, R, 시차, 유효 마스크, 정보
    allow_swap=False (캘리브레이션 모드): 좌우는 캘리브레이션이 정한 것이므로 매칭점 부호로 바꾸지 않는다.
    disp_range=(min_disp, num_disp, scene_min_d): 캘리브레이션 모드에서 거리 범위로 계산한 탐색 범위(반복 무늬 바닥에서 매칭점 추정이 틀리는 사고 방지, 2026-11-11)"""
    gl, gr = cv2.cvtColor(L, cv2.COLOR_BGR2GRAY), cv2.cvtColor(R, cv2.COLOR_BGR2GRAY)
    h, w = gl.shape
    info = {}

    if disp_range is not None:
        pl = pr = np.empty((0, 2), np.float32)
        d_pts = np.array([])
    else:
        pl, pr = match_points(L, R)
        d_pts = pl[:, 0] - pr[:, 0] if len(pl) else np.array([])
        if len(pl):
            d_pts = d_pts[np.abs(pl[:, 1] - pr[:, 1]) < 2]
    swapped = False
    if disp_range is not None:
        min_disp, num_disp, scene_min_d = disp_range
        info["range_from"] = "calib distance range"
    elif len(d_pts) >= 10:
        if allow_swap and np.median(d_pts) < 0:        # 시차가 음수 → 좌우 사진이 바뀐 것 (근사 모드에서만)
            swapped = True
            L, R, gl, gr = R, L, gr, gl
            d_pts = -d_pts
        lo, hi = np.percentile(d_pts, [1, 99.5])
        min_disp = int(max(0, np.floor(lo) - 8))
        scene_min_d = lo                # 장면에서 가장 먼 곳의 시차
        num_disp = int(np.ceil((hi * 1.5 + 16 - min_disp) / 16) * 16)
    else:
        info["note"] = "매칭점이 부족해 기본 탐색 범위(0~128) 사용"
        min_disp, num_disp, scene_min_d = 0, 128, 0
    num_disp = int(min(max(num_disp, 16), (w - min_disp) // 16 * 16))

    sgbm = cv2.StereoSGBM_create(
        minDisparity=min_disp, numDisparities=num_disp, blockSize=block,
        P1=8 * 3 * block ** 2, P2=32 * 3 * block ** 2, disp12MaxDiff=1,
        uniquenessRatio=10, speckleWindowSize=100, speckleRange=2,
        mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY)
    pad = min_disp + num_disp            # 왼쪽 가장자리도 계산되도록 검은 여백을 붙였다가 잘라냄
    glp = cv2.copyMakeBorder(gl, 0, 0, pad, 0, cv2.BORDER_CONSTANT, value=0)
    grp = cv2.copyMakeBorder(gr, 0, 0, pad, 0, cv2.BORDER_CONSTANT, value=0)
    raw = sgbm.compute(glp, grp)
    wls = False
    if hasattr(cv2, "ximgproc"):
        right_m = cv2.ximgproc.createRightMatcher(sgbm)
        f = cv2.ximgproc.createDisparityWLSFilter(sgbm)
        f.setLambda(8000)
        f.setSigmaColor(1.2)
        raw = f.filter(raw, glp, disparity_map_right=right_m.compute(grp, glp))
        wls = True
    raw = raw[:, pad:]
    disp = raw.astype(np.float32) / 16.0
    valid = disp > max(min_disp, 0.5)
    valid &= (np.arange(w)[None, :] - disp) >= 0          # 짝이 오른쪽 사진 밖 → 제외
    valid[:, :int(np.ceil(scene_min_d))] = False          # 가장 먼 배경보다 왼쪽 열 → 제외
    cols = np.where(valid.any(axis=0))[0]
    info.update(swapped=swapped, min_disp=min_disp, max_disp=min_disp + num_disp, wls=wls,
                valid_ratio=round(float(valid.mean()), 3),
                x_range=[int(cols[0]), int(cols[-1])] if len(cols) else None)
    return L, R, disp, valid, info


def depth_map(disp, valid_disp, Q, disp_offset, max_dist_cm):
    """노트북 5번 셀: 시차(+보정값) → 거리(cm). 측정 불가는 NaN"""
    disp_corr = np.where(valid_disp, disp + disp_offset, 0).astype(np.float32)
    pts3d = cv2.reprojectImageTo3D(disp_corr, Q)
    z_cm = pts3d[:, :, 2] / 10.0
    valid = valid_disp & np.isfinite(z_cm) & (z_cm > 0) & (z_cm < max_dist_cm)
    return np.where(valid, z_cm, np.nan)


def distance_at(depth_cm, x, y, roi=7):
    """(x, y) 주변 roi×roi 의 유효 거리 중앙값(cm). 유효 값이 1/4 미만이면 None"""
    h, w = depth_cm.shape
    x, y = int(np.clip(x, 0, w - 1)), int(np.clip(y, 0, h - 1))
    r = roi // 2
    patch = depth_cm[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1]
    vals = patch[np.isfinite(patch)]
    return float(np.median(vals)) if vals.size >= max(1, patch.size // 4) else None


def color_depth(depth_cm):
    """거리 색 지도: 가까울수록 빨강(TURBO), 측정 불가는 검정"""
    ok = np.isfinite(depth_cm)
    norm = np.zeros(depth_cm.shape, np.uint8)
    if ok.any():
        lo, hi = float(np.nanmin(depth_cm)), float(np.nanmax(depth_cm))
        norm[ok] = (255 * (1 - (depth_cm[ok] - lo) / max(hi - lo, 1e-6))).astype(np.uint8)
    vis = cv2.applyColorMap(norm, cv2.COLORMAP_TURBO)
    vis[~ok] = 0
    return vis


def align_check(L, R):
    pair = np.hstack([L, R])
    for y in np.linspace(0, L.shape[0], 12)[1:-1]:
        cv2.line(pair, (0, int(y)), (pair.shape[1] - 1, int(y)), (0, 255, 0), 1)
    return pair


def main():
    ap = argparse.ArgumentParser(description="좌·우 사진 2장으로 스테레오 거리 맵 계산")
    ap.add_argument("left")
    ap.add_argument("right")
    ap.add_argument("--out", required=True, help="결과 폴더")
    ap.add_argument("--calib", help="stereo_calib.npz (있으면 정확 측정 모드)")
    ap.add_argument("--mode", default="auto", choices=["auto", "y-align", "none"])
    ap.add_argument("--baseline", type=float, default=50.0, help="카메라 간격 mm (기본 50)")
    ap.add_argument("--hfov", type=float, default=72.0, help="가로 화각 도 (기본 72)")
    ap.add_argument("--resize", type=int, default=640, help="계산용 가로 해상도 (0 = 원본)")
    ap.add_argument("--block", type=int, default=5, help="SGBM 블록 크기(홀수)")
    ap.add_argument("--max-dist-cm", type=float, default=500.0)
    ap.add_argument("--disp-offset", type=float, default=4.1, help="시차 보정값 px (기본 4.1 = v3 설정, 보정 없음은 0)")
    ap.add_argument("--point", type=int, nargs=2, action="append", metavar=("X", "Y"), help="거리를 알고 싶은 지점(여러 번 가능)")
    ap.add_argument("--roi", type=int, default=9, help="지점 거리 계산 영역 크기(홀수)")
    ap.add_argument("--min-dist-cm", type=float, default=20.0, help="캘리브레이션 모드의 탐색 범위: 이보다 가까운 건 안 잰다")
    a = ap.parse_args()

    t0 = time.time()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    L0, R0 = load_img(a.left), load_img(a.right)
    L, R, Q, info_align = align(L0, R0, a.calib, a.mode, a.resize, a.hfov, a.baseline)
    if info_align["mode"] == "calib":
        # 평행화 뒤 좌우는 확정이고, 탐색 범위는 거리 범위(--min-dist-cm ~ --max-dist-cm)에서 f·B/Z 로 계산
        f_px, base_mm = float(Q[2, 3]), 1.0 / abs(float(Q[3, 2]))
        max_d = f_px * base_mm / (a.min_dist_cm * 10)
        scene_min_d = f_px * base_mm / (a.max_dist_cm * 10)
        num_disp = int(min(max(int(np.ceil((max_d + 16) / 16) * 16), 16), (L.shape[1] // 16) * 16))
        info_align.update(rect_f_px=round(f_px, 1), rect_baseline_mm=round(base_mm, 2))
        L, R, disp, valid_disp, info_disp = disparity(L, R, a.block, allow_swap=False, disp_range=(0, num_disp, scene_min_d))
    else:
        L, R, disp, valid_disp, info_disp = disparity(L, R, a.block)
    depth_cm = depth_map(disp, valid_disp, Q, a.disp_offset, a.max_dist_cm)
    h, w = depth_cm.shape

    center = distance_at(depth_cm, w // 2, h // 2, roi=21)
    points = []
    for x, y in (a.point or []):
        points.append(dict(x=x, y=y, distance_cm=distance_at(depth_cm, x, y, a.roi)))
    finite = np.isfinite(depth_cm)
    result = dict(
        left=str(a.left), right=str(a.right), size=[int(w), int(h)],
        settings=dict(baseline_mm=a.baseline, hfov_deg=a.hfov, resize=a.resize, block=a.block,
                      max_dist_cm=a.max_dist_cm, disp_offset_px=a.disp_offset),
        align=info_align, disparity=info_disp,
        depth=dict(valid_ratio=round(float(finite.mean()), 3),
                   min_cm=round(float(np.nanmin(depth_cm)), 1) if finite.any() else None,
                   max_cm=round(float(np.nanmax(depth_cm)), 1) if finite.any() else None,
                   center_cm=round(center, 1) if center is not None else None),
        points=points, elapsed_s=round(time.time() - t0, 2),
    )

    np.save(str(out / "depth_cm.npy"), depth_cm)
    cv2.imwrite(str(out / "depth_color.png"), color_depth(depth_cm))
    cv2.imwrite(str(out / "align_check.png"), align_check(L, R))
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    d = result["depth"]
    print("%s | %s | 시차 %d~%dpx | 유효 %.0f%% | 거리 %s~%s cm | 중앙 %s cm | %.1fs → %s" % (
        Path(a.left).name, info_align["mode"], info_disp["min_disp"], info_disp["max_disp"],
        d["valid_ratio"] * 100, d["min_cm"], d["max_cm"], d["center_cm"], result["elapsed_s"], out), flush=True)
    if not finite.any():
        print("유효한 거리값이 없습니다. align_check.png 와 설정값(--baseline, --hfov)을 확인하세요.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
