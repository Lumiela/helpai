#!/usr/bin/env python3
"""stereo_calibrate.py — calib/L_NN.jpg·R_NN.jpg 로 스테레오 캘리브레이션 → stereo_calib.npz (화면 없음)

    python stereo_calibrate.py --dir calib --board 9x6 --square 24.2 --width 640 --out stereo_calib.npz [--min-pairs 6] [--max-dy 3]

노트북 C2·C3 와 같은 계산에 세 가지를 더했다 (2026-11-11 실데이터에서 필요했던 것):
  1) 좌우 자동 교정: 쌍별 상대 자세로 구한 간격 벡터의 x 가 양수면 L/R 이 뒤바뀐 것 → 내부에서 바꿔 계산하고 알려준다
  2) 불량 쌍 제외: 쌍별 상대 자세의 중앙값으로 평행화했을 때 코너 세로 어긋남(중앙값)이 --max-dy px 를 넘는 쌍은 제외
     (두 사진 사이에 보드나 카메라가 움직인 쌍 — 한 쌍만 있어도 stereoCalibrate 가 엉뚱한 답으로 수렴한다)
  3) 교차검증: stereoCalibrate 결과와 쌍별 중앙값 자세를 평행화 세로 어긋남으로 비교해 더 나은 쪽을 저장
     (두 해의 간격이 5% 넘게 다르면 쌍별 자세를 저장 — 세로 어긋남은 간격에 둔감하다. --method stereo|median 으로 강제 가능)
  4) 쌍별 보드 중심 거리(보드 자세 PnP / 스테레오 코너 시차)를 출력·report 에 저장 → 줄자 실측과 대조
결과: stereo_calib.npz (K1 D1 K2 D2 R1 R2 P1 P2 Q size — stereo.py measure / stereo_pair.py --calib 형식), *_report.json, *_rectified.jpg
"""
import argparse
import glob
import json
import os
import re
import sys

import cv2
import numpy as np


def natural_key(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", os.path.basename(p))]


def load(path, width):
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is not None and width and img.shape[1] != width:
        s = width / img.shape[1]
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    return img


def find_corners(gray, board, crit):
    ok, c = cv2.findChessboardCornersSB(gray, board, cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_EXHAUSTIVE)
    if not ok:
        ok, c = cv2.findChessboardCorners(gray, board, cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE)
        if ok:
            c = cv2.cornerSubPix(gray, c, (11, 11), (-1, -1), crit)
    if ok:
        c = c.reshape(-1, 1, 2).astype(np.float32)
    return ok, c


def rot_deg(R):
    return float(np.degrees(np.arccos(np.clip((np.trace(R) - 1) / 2, -1, 1))))


def rel_poses(rvL, tvL, rvR, tvR):
    """뷰마다 (왼쪽→오른쪽 카메라) 상대 자세. 반환 [(회전벡터, T)]"""
    out = []
    for i in range(len(rvL)):
        RL, RR = cv2.Rodrigues(rvL[i])[0], cv2.Rodrigues(rvR[i])[0]
        Rr = RR @ RL.T
        out.append((cv2.Rodrigues(Rr)[0].ravel(), (tvR[i] - Rr @ tvL[i]).ravel()))
    return out


def median_pose(rels):
    rv = np.median([r[0] for r in rels], axis=0)
    T = np.median([r[1] for r in rels], axis=0).reshape(3, 1)
    return cv2.Rodrigues(rv)[0], T


def rect_dy(L, R, K1, D1, K2, D2, size, Rs, T):
    """R,T 로 평행화했을 때 쌍별 코너 세로 어긋남 중앙값(px)과 시차 중앙값(px)"""
    R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(K1, D1, K2, D2, size, Rs, T, alpha=0)
    dys, disps = [], []
    for i in range(len(L)):
        ra = cv2.undistortPoints(L[i], K1, D1, R=R1, P=P1).reshape(-1, 2)
        rb = cv2.undistortPoints(R[i], K2, D2, R=R2, P=P2).reshape(-1, 2)
        dys.append(float(np.median(np.abs(ra[:, 1] - rb[:, 1]))))
        disps.append(float(np.median(ra[:, 0] - rb[:, 0])))
    return dys, disps, (R1, R2, P1, P2, Q)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="calib")
    ap.add_argument("--board", default="9x6", help="내부 코너 수 가로x세로")
    ap.add_argument("--square", type=float, required=True, help="칸 크기 mm (자로 잰 값)")
    ap.add_argument("--width", type=int, default=640, help="캘리브레이션 해상도(가로). 측정도 같은 크기로 맞춰진다")
    ap.add_argument("--out", default="stereo_calib.npz")
    ap.add_argument("--min-pairs", type=int, default=6)
    ap.add_argument("--max-dy", type=float, default=3.0, help="불량 쌍 기준: 평행화 뒤 코너 세로 어긋남 중앙값 px")
    ap.add_argument("--method", default="auto", choices=("auto", "stereo", "median"),
                    help="저장할 해: auto(교차검증) | stereo(stereoCalibrate) | median(쌍별 중앙값 자세)")
    a = ap.parse_args()
    board = tuple(int(v) for v in a.board.lower().split("x"))
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 100, 1e-5)
    objp = np.zeros((board[0] * board[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0:board[0], 0:board[1]].T.reshape(-1, 2) * a.square

    # ---- 코너 ----
    names, L, R, imgs = [], [], [], []
    size = None
    for lp in sorted(glob.glob(os.path.join(a.dir, "L_*.*")), key=natural_key):
        rp = os.path.join(a.dir, "R_" + os.path.basename(lp)[2:])
        if not os.path.exists(rp):
            continue
        A, B = load(lp, a.width), load(rp, a.width)
        if A is None or B is None or A.shape != B.shape:
            print("제외 %s: 읽기 실패/크기 다름" % os.path.basename(lp)); continue
        size = size or (A.shape[1], A.shape[0])
        oka, ca = find_corners(cv2.cvtColor(A, cv2.COLOR_BGR2GRAY), board, crit)
        okb, cb = find_corners(cv2.cvtColor(B, cv2.COLOR_BGR2GRAY), board, crit)
        if not (oka and okb):
            print("제외 %s: 코너 인식 실패 (L %s R %s)" % (os.path.basename(lp), oka, okb)); continue
        if np.dot((ca[-1] - ca[0]).ravel(), (cb[-1] - cb[0]).ravel()) < 0:
            cb = cb[::-1].copy()
        names.append(os.path.basename(lp)[:-4]); L.append(ca); R.append(cb); imgs.append((A, B))
    print("코너 인식 성공: %d 쌍" % len(names))
    if len(names) < a.min_pairs:
        print("쌍이 부족합니다 (최소 %d). 더 촬영하세요." % a.min_pairs); return 2

    # ---- 단일 카메라 + 쌍별 상대 자세 → 좌우 확인 ----
    swapped = False
    for attempt in range(2):
        n = len(names)
        eL, K1, D1, rvL, tvL = cv2.calibrateCamera([objp] * n, L, size, None, None)
        eR, K2, D2, rvR, tvR = cv2.calibrateCamera([objp] * n, R, size, None, None)
        rels = rel_poses(rvL, tvL, rvR, tvR)
        Rm, Tm = median_pose(rels)
        if Tm[0, 0] > 0 and attempt == 0:
            print("⚠ 왼쪽/오른쪽이 뒤바뀜(간격 벡터 x = %+.1f mm) → L 과 R 을 바꿔서 계산합니다. 측정 때도 L=%s 쪽이 실제 오른쪽 카메라입니다." % (Tm[0, 0], "현재 L"))
            L, R = R, L
            imgs = [(b, a_) for a_, b in imgs]
            swapped = True
            continue
        break
    bases = [float(np.linalg.norm(r[1])) for r in rels]
    print("단일 카메라 오차   : 왼쪽 %.3fpx / 오른쪽 %.3fpx" % (eL, eR))
    print("쌍별 상대 자세     : 간격 %.1f~%.1f mm, 회전 %.2f~%.2f° → 중앙값 T %s mm, 간격 %.1f mm, 회전 %.2f°" % (
        min(bases), max(bases), min(np.degrees(np.linalg.norm(r[0])) for r in rels), max(np.degrees(np.linalg.norm(r[0])) for r in rels),
        np.round(Tm.ravel(), 1), np.linalg.norm(Tm), rot_deg(Rm)))

    # ---- 불량 쌍 제외 (중앙값 자세로 평행화했을 때 세로 어긋남) ----
    dys, disps, _ = rect_dy(L, R, K1, D1, K2, D2, size, Rm, Tm)
    keep = [i for i in range(n) if dys[i] <= a.max_dy]
    dropped = [(names[i], round(dys[i], 2)) for i in range(n) if i not in keep]
    for nm, dy in dropped:
        print("  제외: %s (평행화 세로 어긋남 %.2fpx — 두 사진 사이에 보드/카메라가 움직였을 가능성)" % (nm, dy))
    if len(keep) < a.min_pairs:
        print("일관된 쌍이 부족합니다 (%d < %d). 더 촬영하세요." % (len(keep), a.min_pairs)); return 2
    Lk, Rk, names_k = [L[i] for i in keep], [R[i] for i in keep], [names[i] for i in keep]
    rels_k = [rels[i] for i in keep]
    Rm, Tm = median_pose(rels_k)

    # ---- stereoCalibrate vs 중앙값 자세 교차검증 ----
    rms, _, _, _, _, Rs, Ts, _, _ = cv2.stereoCalibrate([objp] * len(keep), Lk, Rk, K1, D1, K2, D2, size, criteria=crit, flags=cv2.CALIB_FIX_INTRINSIC)
    dy_sc, disp_sc, rect_sc = rect_dy(Lk, Rk, K1, D1, K2, D2, size, Rs, Ts)
    dy_md, disp_md, rect_md = rect_dy(Lk, Rk, K1, D1, K2, D2, size, Rm, Tm)
    print("stereoCalibrate    : RMS %.3fpx, 간격 %.1f mm, 회전 %.2f°, 평행화 세로 어긋남 중앙값 %.2fpx" % (rms, np.linalg.norm(Ts), rot_deg(Rs), np.median(dy_sc)))
    print("쌍별 중앙값 자세   : 간격 %.1f mm, 회전 %.2f°, 평행화 세로 어긋남 중앙값 %.2fpx" % (np.linalg.norm(Tm), rot_deg(Rm), np.median(dy_md)))
    base_gap = abs(np.linalg.norm(Ts) - np.linalg.norm(Tm)) / np.linalg.norm(Tm)
    if a.method == "auto":
        if base_gap > 0.05:
            # 세로 어긋남은 간격(x 성분)에 둔감하다. 두 해의 간격이 5% 넘게 다르면 쌍별 자세(직접 측정)를 믿는다.
            print("⚠ 두 해의 간격이 %.0f%% 다름 → 쌍별 중앙값 자세를 저장 (stereoCalibrate 해는 --method stereo 로 강제 가능)" % (base_gap * 100))
            use_stereo = False
        else:
            use_stereo = np.median(dy_sc) <= np.median(dy_md)
    else:
        use_stereo = a.method == "stereo"
    if use_stereo:
        method, Rf, Tf, dyf, dispf, rect = "stereoCalibrate", Rs, Ts, dy_sc, disp_sc, rect_sc
    else:
        method, Rf, Tf, dyf, dispf, rect = "median-of-pairs", Rm, Tm, dy_md, disp_md, rect_md
    R1, R2, P1, P2, Q = rect
    np.savez(a.out, K1=K1, D1=D1, K2=K2, D2=D2, R1=R1, R2=R2, P1=P1, P2=P2, Q=Q, size=np.array(size))

    baseline = float(np.linalg.norm(Tf))
    fx1, fx2 = float(K1[0, 0]), float(K2[0, 0])
    hfov = lambda f: float(np.degrees(2 * np.arctan(size[0] / 2 / f)))
    med_dy = float(np.median(dyf))
    grade = "우수" if med_dy < 0.5 else ("보통" if med_dy < 1.5 else "나쁨 → 재촬영 권장")
    report = dict(method=method, swapped_lr=swapped, pairs_used=names_k, dropped=dropped, size=size, square_mm=a.square, board=board,
                  stereo_rms_px=round(float(rms), 3), rect_dy_median_px=round(med_dy, 3), rect_dy_per_pair=[round(v, 2) for v in dyf],
                  disparity_per_pair=[round(v, 1) for v in dispf], err_left_px=round(float(eL), 3), err_right_px=round(float(eR), 3),
                  baseline_mm=round(baseline, 2), T_mm=[round(float(v), 2) for v in Tf.ravel()], rot_deg=round(rot_deg(Rf), 3),
                  fx_left=round(fx1, 1), fx_right=round(fx2, 1), hfov_left_deg=round(hfov(fx1), 2), hfov_right_deg=round(hfov(fx2), 2),
                  rect_focal_px=round(float(P2[0, 0]), 1), grade=grade, npz=os.path.abspath(a.out))
    with open(os.path.splitext(a.out)[0] + "_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print("\n===== 캘리브레이션 결과 (%s) =====" % method)
    print("사용한 사진 쌍     : %d (%s)%s" % (len(keep), ", ".join(names_k), "  ※ 좌우 바꿈" if swapped else ""))
    print("해상도             : %d x %d" % size)
    print("평행화 세로 어긋남 : 중앙값 %.2fpx  (%s)   쌍별 %s" % (med_dy, grade, [round(v, 2) for v in dyf]))
    print("측정된 카메라 간격 : %.1f mm   (T = %s)" % (baseline, report["T_mm"]))
    print("초점거리 / 화각    : 왼쪽 %.1fpx ≈ %.1f° / 오른쪽 %.1fpx ≈ %.1f° (평행화 후 %.1fpx)" % (fx1, hfov(fx1), fx2, hfov(fx2), P2[0, 0]))
    print("시차(평행화, 쌍별) : %s px  — 전부 양수여야 정상" % [round(v, 1) for v in dispf])
    # 쌍별 보드 중심 거리: 보드 자세(왼쪽 카메라 PnP, 보드 크기 기준) vs 스테레오(평행화 코너 시차 → Q). 줄자 실측과 대조용
    dist = []
    ctr_obj = objp.mean(axis=0).reshape(3, 1)
    for j, i in enumerate(keep):
        Rb = cv2.Rodrigues(rvL[i])[0]
        z_pnp = float((Rb @ ctr_obj + tvL[i].reshape(3, 1))[2, 0])
        ra = cv2.undistortPoints(Lk[j], K1, D1, R=R1, P=P1).reshape(-1, 2)
        rb = cv2.undistortPoints(Rk[j], K2, D2, R=R2, P=P2).reshape(-1, 2)
        d = ra[:, 0] - rb[:, 0]
        z_st = float(np.mean(Q[2, 3] / (Q[3, 2] * d + Q[3, 3])))
        tilt = float(np.degrees(np.arccos(abs(Rb[2, 2]))))
        dist.append(dict(pair=names_k[j], pnp_cm=round(z_pnp / 10, 1), stereo_cm=round(z_st / 10, 1), tilt_deg=round(tilt, 1)))
    print("쌍별 보드 중심 거리: 보드자세(PnP) / 스테레오  [기울기]")
    for dd in dist:
        print("  %-5s %5.1f / %5.1f cm  [%4.1f°]" % (dd["pair"], dd["pnp_cm"], dd["stereo_cm"], dd["tilt_deg"]))
    report["pair_distances"] = dist
    with open(os.path.splitext(a.out)[0] + "_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print("저장               : %s" % a.out)

    A, B = imgs[keep[0]]
    m1 = cv2.initUndistortRectifyMap(K1, D1, R1, P1, size, cv2.CV_32FC1)
    m2 = cv2.initUndistortRectifyMap(K2, D2, R2, P2, size, cv2.CV_32FC1)
    pair = np.hstack([cv2.remap(A, *m1, cv2.INTER_LINEAR), cv2.remap(B, *m2, cv2.INTER_LINEAR)])
    for y in np.linspace(0, size[1], 14)[1:-1]:
        cv2.line(pair, (0, int(y)), (pair.shape[1] - 1, int(y)), (0, 255, 0), 1)
    cv2.imwrite(os.path.splitext(a.out)[0] + "_rectified.jpg", pair)
    return 0


if __name__ == "__main__":
    sys.exit(main())
