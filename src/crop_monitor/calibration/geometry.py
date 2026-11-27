"""보정 파일 기하 도구.

보정 파일(stereo_calib.npz): K1 D1 K2 D2 R1 R2 P1 P2 Q size  (1 = 스테레오 왼쪽 C2, 2 = 오른쪽 C1)
원본 R, T 는 저장돼 있지 않아 평행화 행렬에서 되살린다:
    평행화 좌표에서 두 카메라는 x 축으로만 떨어져 있음  →  R2·(R·X1 + T) = R1·X1 + (Tx, 0, 0)
    ⇒  R = R2ᵀ·R1,   T = R2ᵀ·(Tx, 0, 0),   Tx = P2[0,3] / P2[0,0]
"""

from __future__ import annotations

import cv2
import numpy as np

ARRAY_KEYS = ("K1", "D1", "K2", "D2", "R1", "R2", "P1", "P2", "Q", "size")


def load(path) -> dict:
    d = np.load(str(path))
    return {k: d[k] for k in d.files}


def save(path, c: dict) -> None:
    np.savez(str(path), **{k: c[k] for k in ARRAY_KEYS})


def pose(c: dict) -> tuple[np.ndarray, np.ndarray]:
    tx = float(c["P2"][0, 3] / c["P2"][0, 0])
    R = c["R2"].T @ c["R1"]
    T = c["R2"].T @ np.array([[tx], [0.0], [0.0]])
    return R, T


def rectify(c: dict, R: np.ndarray, T: np.ndarray) -> dict:
    """같은 K·D 로 상대 자세 (R, T) 만 바꾼 새 보정 (stereo_calibrate.py 와 같은 stereoRectify, alpha=0)."""
    size = tuple(int(v) for v in c["size"])
    R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(c["K1"], c["D1"], c["K2"], c["D2"], size, R, T.reshape(3, 1), alpha=0)
    return dict(K1=c["K1"], D1=c["D1"], K2=c["K2"], D2=c["D2"], R1=R1, R2=R2, P1=P1, P2=P2, Q=Q, size=c["size"])


def rot_deg(R: np.ndarray) -> float:
    return float(np.degrees(np.linalg.norm(cv2.Rodrigues(np.asarray(R, np.float64))[0])))


def angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    a, b = np.ravel(a), np.ravel(b)
    return float(np.degrees(np.arccos(np.clip(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)), -1, 1))))


def to_calib_px(pts: np.ndarray, src_size, c: dict) -> np.ndarray:
    """원본 사진 좌표 → 보정 해상도 좌표 (사진 비율이 같다고 가정)."""
    W, H = src_size
    cw, ch = (int(v) for v in c["size"])
    return np.asarray(pts, np.float32).reshape(-1, 2) * np.float32([cw / W, ch / H])


def rect_points(c: dict, pl: np.ndarray, pr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """보정 해상도 좌표의 왼쪽·오른쪽 점 → 평행화 좌표."""
    a = cv2.undistortPoints(pl.reshape(-1, 1, 2).astype(np.float64), c["K1"], c["D1"], R=c["R1"], P=c["P1"]).reshape(-1, 2)
    b = cv2.undistortPoints(pr.reshape(-1, 1, 2).astype(np.float64), c["K2"], c["D2"], R=c["R2"], P=c["P2"]).reshape(-1, 2)
    return a, b


def match(left_bgr: np.ndarray, right_bgr: np.ndarray, work_width: int = 1280, max_pts: int = 6000,
          mask_left: np.ndarray | None = None, mask_right: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """SIFT + 비율 검사 + 양방향 일치. 반환: 원본 좌표의 왼쪽·오른쪽 점 (N×2)."""
    def prep(img, mask):
        s = min(1.0, work_width / img.shape[1])
        g = cv2.cvtColor(cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        m = None if mask is None else cv2.resize(mask.astype(np.uint8), (g.shape[1], g.shape[0]), interpolation=cv2.INTER_NEAREST)
        return g, m, s

    gl, ml, sl = prep(left_bgr, mask_left)
    gr, mr, sr = prep(right_bgr, mask_right)
    sift = cv2.SIFT_create(nfeatures=max_pts)
    kl, dl = sift.detectAndCompute(gl, ml)
    kr, dr = sift.detectAndCompute(gr, mr)
    empty = np.empty((0, 2), np.float32)
    if dl is None or dr is None or len(kl) < 10 or len(kr) < 10:
        return empty, empty
    bf = cv2.BFMatcher()
    fwd = bf.knnMatch(dl, dr, k=2)
    back = {m.queryIdx: m.trainIdx for m in bf.match(dr, dl)}
    good = [m for m, n in (p for p in fwd if len(p) == 2)
            if m.distance < 0.75 * n.distance and back.get(m.trainIdx) == m.queryIdx]
    pl = np.float32([kl[m.queryIdx].pt for m in good]) / sl
    pr = np.float32([kr[m.trainIdx].pt for m in good]) / sr
    return pl, pr


def pair_stats(c: dict, pl_calib: np.ndarray, pr_calib: np.ndarray) -> dict:
    """평행화 뒤 세로 어긋남·시차 (보정 해상도 px)."""
    if len(pl_calib) == 0:
        return dict(n=0, dy_median=None, disp_median=None)
    a, b = rect_points(c, pl_calib, pr_calib)
    dy = np.abs(a[:, 1] - b[:, 1])
    return dict(n=int(len(dy)), dy_median=round(float(np.median(dy)), 2),
                disp_median=round(float(np.median(a[:, 0] - b[:, 0])), 1))
