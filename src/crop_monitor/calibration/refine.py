"""B — 평소 사진 쌍으로 두 카메라 상대 자세를 다시 맞추기 (자동 미세 조정).

그대로 두는 것 : 렌즈 특성 K1 D1 K2 D2, 두 카메라 간격 |T| (체커보드 보정 A 의 값)
다시 맞추는 것 : 오른쪽 카메라가 제자리에서 돈 양 (R' = Rδ·R, T' = Rδ·T)
    ① 세로 어긋남으로 맞추는 회전 2개 — 간격 축(x) 둘레 '끄덕임', 광축(z) 둘레 '기울어짐'
       같은 점이 평행화 뒤 같은 높이(y)에 오도록 세로 어긋남 dy 를 최소로.
    ② 세로 축(y) 둘레 '좌우 돌아감(yaw)' 은 세로 어긋남에 거의 나타나지 않는다. 대신 모든 시차를 똑같이 밀어
       거리를 크게 바꾼다 (1° 에 시차 약 11px → 42cm 거리가 몇 cm 씩 틀어짐).
       그래서 ① 로는 건드리지 않고, '거리 기준'(보정이 맞을 때 잰 트레이 면 거리, 또는 줄자 실측)이 있을 때만
       그 거리에 맞도록 따로 맞춘다 (fit_yaw).
    (세 회전을 한꺼번에 세로 어긋남으로 풀면 dy 는 0 이 되지만 큐브 거리가 56.7cm → 82cm 로 틀어지는 것을 확인함)

순서
    1) 쌍마다 SIFT 매칭 (원본 좌표, 양방향 일치) → 보정 해상도 좌표로
    2) 지금 보정으로 평행화했을 때 dy 가 prior_window 보다 큰 짝, 시차가 음수인 짝은 버림
       (반복되는 화분 무늬·반사 때문에 엉뚱하게 짝지어진 점 제거. 카메라가 크게 틀어졌다면 B 가 아니라 A 가 필요)
    3) 회전 2개를 Levenberg–Marquardt 로 맞춤 (Huber 가중치 + 이전 보정에서 멀어지지 않게 약한 제약)
       → 큰 잔차 제거 후 한 번 더
    4) 검증: 세로 어긋남 중앙값·쌍별 값, 회전 변화량, 시차가 양수인지
"""

from __future__ import annotations

import dataclasses
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from . import geometry as geo


@dataclass
class RefineConfig:
    prior_window_px: float = 25.0     # 지금 보정 기준 dy 가 이보다 큰 짝은 버림 (보정 해상도 px)
    min_points: int = 150             # 쓸 수 있는 짝이 이보다 적으면 실패
    target_dy_px: float = 1.5         # 맞춘 뒤 세로 어긋남 중앙값이 이 값 이하여야 성공
    max_pair_dy_px: float = 3.0       # 쌍마다 세로 어긋남 중앙값 상한 (한 쌍만 맞고 다른 쌍은 안 맞는 경우 방지)
    max_rotation_change_deg: float = 5.0   # 이보다 크게 돌아갔으면 B 로 고치지 않고 A 요청
    min_improvement: float = 0.5      # 이전 대비 세로 어긋남이 이만큼(px) 이상 줄어야 교체 (작은 차이로 매번 바꾸지 않게)
    huber_px: float = 1.0
    prior_sigma_deg: float = 2.0      # 회전 변화에 주는 약한 제약
    iterations: int = 40
    match_width: int = 1280
    # 좌우 돌아감(yaw) — 거리 기준이 있을 때만
    yaw_max_deg: float = 3.0          # 이 범위 안에서 찾음
    yaw_tolerance_cm: float = 0.5     # 기준 거리와 이만큼 이내면 yaw 는 그대로 둠


@dataclass
class RefineResult:
    ok: bool
    reason: str
    calib: dict | None = None
    before: dict = field(default_factory=dict)     # {dy_median, pairs:[{n, dy_median, disp_median}]}
    after: dict = field(default_factory=dict)
    rotation_change_deg: float | None = None
    yaw_change_deg: float | None = None
    distance: dict = field(default_factory=dict)  # {reference_cm, before_cm, after_cm} (거리 기준이 있을 때)
    n_points: int = 0

    def summary(self) -> str:
        b, a = self.before.get("dy_median"), self.after.get("dy_median")
        s = f"세로 어긋남 {b}px → {a}px" if a is not None else f"세로 어긋남 {b}px"
        if self.rotation_change_deg is not None:
            s += f", 회전 변화 {self.rotation_change_deg:.2f}°"
        if self.yaw_change_deg is not None:
            s += f", 좌우 돌아감 보정 {self.yaw_change_deg:+.2f}°"
        d = self.distance
        if d.get("reference_cm") is not None:
            s += f", 트레이 거리 {d.get('before_cm')}→{d.get('after_cm')}cm (기준 {d['reference_cm']}cm)"
        return s + f", 사용한 점 {self.n_points}개"


# ── 자세 파라미터 ────────────────────────────────────────────
def _axes(c: dict) -> np.ndarray:
    """평행화 좌표의 x(간격)·y(세로)·z(광축) 축을 오른쪽 카메라 좌표로 (행 = 축)."""
    return c["R2"].T.T  # R2ᵀ 의 열 = 평행화 축 → 행으로


def rotate_right(c: dict, wx: float = 0.0, wy: float = 0.0, wz: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """오른쪽 카메라를 평행화 축 기준으로 (wx, wy, wz) rad 만큼 제자리 회전한 (R, T)."""
    R0, T0 = geo.pose(c)
    ax = _axes(c)
    w = wx * ax[0] + wy * ax[1] + wz * ax[2]
    Rd = cv2.Rodrigues(w.reshape(3, 1))[0]
    return Rd @ R0, Rd @ T0


def _residuals(x, c, pl, pr, prior_w=0.0, sigma=None):
    R, T = rotate_right(c, wx=x[0], wz=x[1])
    cn = geo.rectify(c, R, T)
    a, b = geo.rect_points(cn, pl, pr)
    r = a[:, 1] - b[:, 1]
    if prior_w > 0:
        r = np.concatenate([r, prior_w * np.asarray(x) / sigma])
    return r


def _solve(c, pl, pr, cfg: RefineConfig, x0=None):
    n = 2
    x = np.zeros(n) if x0 is None else np.array(x0, float)
    lam, eps = 1e-3, np.full(n, 1e-5)
    sigma = np.radians(cfg.prior_sigma_deg) if cfg.prior_sigma_deg else None
    prior_w = float(np.sqrt(len(pl))) * 0.5 if sigma else 0.0

    def res(xx):
        return _residuals(xx, c, pl, pr, prior_w, sigma)

    def cost(r):
        a = np.abs(r)
        k = cfg.huber_px
        return float(np.sum(np.where(a <= k, 0.5 * a ** 2, k * (a - 0.5 * k))))

    r = res(x)
    f = cost(r)
    for _ in range(cfg.iterations):
        J = np.empty((len(r), n))
        for i in range(n):
            xd = x.copy()
            xd[i] += eps[i]
            J[:, i] = (res(xd) - r) / eps[i]
        w = np.where(np.abs(r) <= cfg.huber_px, 1.0, cfg.huber_px / np.maximum(np.abs(r), 1e-9))
        A = J.T @ (J * w[:, None])
        g = J.T @ (w * r)
        improved, step = False, np.zeros(n)
        for _ in range(10):
            step = np.linalg.solve(A + lam * np.diag(np.diag(A) + 1e-12), -g)
            rn = res(x + step)
            fn = cost(rn)
            if fn < f:
                x, r, f, lam, improved = x + step, rn, fn, max(lam / 3, 1e-7), True
                break
            lam *= 4
        if not improved or np.linalg.norm(step) < 1e-9:
            break
    return x, r[:len(pl)]


# ── 매칭점 모으기 ────────────────────────────────────────────
def collect(c: dict, pairs, cfg: RefineConfig, masks=None):
    """pairs: [(왼쪽 BGR, 오른쪽 BGR)] → 쌍마다 (보정 해상도 왼쪽 점, 오른쪽 점, 지금 보정 통계)."""
    out = []
    for i, (L, R) in enumerate(pairs):
        ml, mr = masks[i] if masks else (None, None)
        pl, pr = geo.match(L, R, cfg.match_width, mask_left=ml, mask_right=mr)
        src = (L.shape[1], L.shape[0])
        pl, pr = geo.to_calib_px(pl, src, c), geo.to_calib_px(pr, src, c)
        stats = geo.pair_stats(c, pl, pr)
        if len(pl):
            a, b = geo.rect_points(c, pl, pr)
            keep = (np.abs(a[:, 1] - b[:, 1]) < cfg.prior_window_px) & (a[:, 0] - b[:, 0] > -2)
            pl, pr = pl[keep], pr[keep]
        out.append((pl, pr, stats))
    return out


# ── 거리 기준으로 좌우 돌아감(yaw) 맞추기 ─────────────────────
def tray_distance_cm(c: dict, pair, height_cfg) -> float | None:
    """이 보정으로 계산한 트레이(가장 넓은 평면) 거리 — height.compute 의 reference_center_cm."""
    from crop_monitor.modules.height import height as H

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "calib.npz"
        geo.save(p, c)
        cfg = dataclasses.replace(height_cfg, calib=p, reference="ransac")
        try:
            res = H.compute(pair[0], pair[1], cfg)
        finally:
            H._load_calib.cache_clear()
    return res.info.get("reference_center_cm")


def fit_yaw(c: dict, pair, reference_cm: float, height_cfg, cfg: RefineConfig) -> tuple[dict, float, float | None]:
    """트레이 거리가 reference_cm 이 되도록 오른쪽 카메라 yaw 를 이분 탐색. 반환: (새 보정, yaw 도, 맞춘 거리)."""
    def dist(deg):
        R, T = rotate_right(c, wy=np.radians(deg))
        return tray_distance_cm(geo.rectify(c, R, T), pair, height_cfg)

    # 거리 Z 는 yaw 에 직선이 아니지만, 시차 ∝ 1/Z 는 yaw 에 거의 직선 (yaw 1° ≈ 시차 f·tan1° px).
    # 그래서 1/Z 로 바꿔 할선법 → height.compute 몇 번이면 맞춰짐 (한 번에 수 초)
    def inv(z):
        return None if z is None or z <= 0 else 1.0 / z

    target = 1.0 / reference_cm
    x0, x1 = 0.0, 0.5
    d0, d1 = dist(x0), dist(x1)
    best = (x0, d0)
    for _ in range(6):
        y0, y1 = inv(d0), inv(d1)
        if y0 is None or y1 is None or abs(y1 - y0) < 1e-12:
            break
        if d1 is not None and abs(d1 - reference_cm) < abs((best[1] or 1e9) - reference_cm):
            best = (x1, d1)
        if abs(d1 - reference_cm) < cfg.yaw_tolerance_cm / 5:
            break
        x2 = float(np.clip(x1 + (target - y1) * (x1 - x0) / (y1 - y0), -cfg.yaw_max_deg, cfg.yaw_max_deg))
        x0, d0, x1, d1 = x1, d1, x2, dist(x2)
    if d1 is not None and abs(d1 - reference_cm) < abs((best[1] or 1e9) - reference_cm):
        best = (x1, d1)
    if best[1] is None:
        return c, 0.0, None
    R, T = rotate_right(c, wy=np.radians(best[0]))
    return geo.rectify(c, R, T), best[0], best[1]


# ── B 전체 ───────────────────────────────────────────────────
def refine(c: dict, pairs, cfg: RefineConfig | None = None, masks=None,
           reference_cm: float | None = None, height_cfg=None) -> RefineResult:
    """pairs: [(스테레오 왼쪽 BGR, 오른쪽 BGR)].
    reference_cm: 트레이 거리 기준 (있으면 좌우 돌아감까지 맞춤. 없으면 세로 어긋남만)."""
    cfg = cfg or RefineConfig()
    data = collect(c, pairs, cfg, masks)
    before_pairs = [s for _, _, s in data]
    dys = [s["dy_median"] for s in before_pairs if s["dy_median"] is not None]
    before = dict(dy_median=round(float(np.median(dys)), 2) if dys else None, pairs=before_pairs)

    pl = np.concatenate([d[0] for d in data]) if data else np.empty((0, 2))
    pr = np.concatenate([d[1] for d in data]) if data else np.empty((0, 2))
    idx = np.concatenate([np.full(len(d[0]), i) for i, d in enumerate(data)]) if data else np.empty(0)
    if len(pl) < cfg.min_points:
        return RefineResult(False, f"쓸 수 있는 매칭점이 {len(pl)}개뿐 (필요 {cfg.min_points}개). "
                                   f"카메라가 크게 움직였거나(→ 체커보드 재보정 A) 좌우가 바뀌었거나 사진이 어둡거나 흐림",
                            before=before, n_points=int(len(pl)))

    x, r = _solve(c, pl, pr, cfg)
    keep = np.abs(r) < max(3 * cfg.huber_px, 3.0)
    if keep.sum() >= cfg.min_points:
        x, r = _solve(c, pl[keep], pr[keep], cfg, x0=x)
        pl, pr, idx = pl[keep], pr[keep], idx[keep]

    R, T = rotate_right(c, wx=x[0], wz=x[1])
    cn = geo.rectify(c, R, T)
    R0, _ = geo.pose(c)
    res = RefineResult(False, "", cn, before, {}, round(geo.rot_deg(R @ R0.T), 3), None, {}, int(len(pl)))

    # 거리 기준이 있으면 좌우 돌아감(yaw) 확인·보정
    if reference_cm is not None and height_cfg is not None and pairs:
        before_cm = tray_distance_cm(cn, pairs[0], height_cfg)
        res.distance = dict(reference_cm=reference_cm, before_cm=before_cm, after_cm=before_cm)
        if before_cm is not None and abs(before_cm - reference_cm) > cfg.yaw_tolerance_cm:
            cy, yaw, after_cm = fit_yaw(cn, pairs[0], reference_cm, height_cfg, cfg)
            if after_cm is not None:
                cn, res.calib, res.yaw_change_deg = cy, cy, round(yaw, 3)
                res.distance["after_cm"] = after_cm
                R, _ = geo.pose(cn)
                res.rotation_change_deg = round(geo.rot_deg(R @ R0.T), 3)

    after_pairs = [geo.pair_stats(cn, pl[idx == i], pr[idx == i]) for i in range(len(data))]
    a, b = geo.rect_points(cn, pl, pr)
    res.after = dict(dy_median=round(float(np.median(np.abs(a[:, 1] - b[:, 1]))), 2), pairs=after_pairs,
                     disp_median=round(float(np.median(a[:, 0] - b[:, 0])), 1))

    pair_dys = [p["dy_median"] for p in after_pairs if p["dy_median"] is not None]
    d = res.distance
    if res.rotation_change_deg > cfg.max_rotation_change_deg:
        res.reason = f"회전 변화가 너무 큼 ({res.rotation_change_deg:.1f}°) → 카메라가 크게 움직임. 체커보드 재보정(A) 필요"
    elif res.after["dy_median"] > cfg.target_dy_px:
        res.reason = f"맞춘 뒤에도 세로 어긋남 {res.after['dy_median']}px (기준 {cfg.target_dy_px}px) → 체커보드 재보정(A) 필요"
    elif pair_dys and max(pair_dys) > cfg.max_pair_dy_px:
        res.reason = f"사진 쌍마다 결과가 다름 (최대 {max(pair_dys)}px) → 촬영 중 움직임이 있었을 수 있음"
    elif res.after["disp_median"] <= 0:
        res.reason = "시차가 음수 → 스테레오 좌우(cameras.stereo_left/right)가 바뀐 것 같음"
    elif d.get("reference_cm") is not None and (d.get("after_cm") is None
                                               or abs(d["after_cm"] - d["reference_cm"]) > 2 * cfg.yaw_tolerance_cm):
        res.reason = (f"트레이 거리 {d.get('after_cm')}cm 를 기준 {d['reference_cm']}cm 에 맞추지 못함 "
                      f"(좌우 돌아감 ±{cfg.yaw_max_deg}° 밖) → 체커보드 재보정(A) 필요")
    else:
        res.ok = True
        res.reason = "성공" + ("" if d.get("reference_cm") is not None else
                             " (거리 기준이 없어 좌우 돌아감은 확인하지 않음 — set-reference 로 기준을 정하세요)")
    return res
