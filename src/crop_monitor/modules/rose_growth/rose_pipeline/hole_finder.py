"""구멍(화분) 위치 자동 찾기 — 트레이 전체를 격자 모델로 사진에 맞춘다.

생각
    트레이를 위에서 보면 rows×cols 화분이 일정한 간격으로 놓인 직사각형이다.
    그래서 구멍을 하나씩 찾지 않고, '트레이 직사각형 + 그 안의 격자' 를 한 번에 사진에 맞춘다.
    잎에 가려진 구멍이 있어도 보이는 트레이 부분만으로 12개 위치가 모두 정해진다.

점수 (클수록 잘 맞음, 최대 2)
    트레이 안쪽  : 어두운 픽셀 비율   (트레이·화분은 짙은 회색·검정)
    트레이 바깥 띠: 밝은 픽셀 비율    (은색 반사 바닥·벽)
    잎·꽃·전선처럼 채도가 높은 픽셀은 어느 쪽에도 세지 않는다 (가려도 점수가 깎이지 않게).
    어둡다/밝다 기준은 사진마다 Otsu 로 정한다.
    트레이가 사진 밖으로 나간 면적은 '어둡지 않음' 으로 친다 (밖으로 키워서 점수를 얻는 것 방지).
    바깥 띠는 네 변(위·오른쪽·아래·왼쪽)마다 밝은 비율을 구해 평균한다.
    깎는 항목:
      - 세로/가로 간격 비가 1 에서 벗어난 정도 × aspect_weight
      - 이전 구멍 위치(holes.yaml) 간격과 다른 정도 × pitch_prior_weight
        트레이 한쪽 바깥이 검은 전선·벽이면 그 변의 위치를 밝기로 정할 수 없어 간격이 줄어드는 일이 있음
        (2026-11-27 CL2/CR2: 오른쪽 바깥 검은 전선 → 가로 간격이 5% 작게 잡혀 4열 원이 화분에서 벗어남).
        카메라 높이가 그대로면 사진 속 간격도 그대로이므로 이전 간격으로 잡아 준다.

찾는 순서
    1) 작은 사진(기본 가로 320px)에서 크기·위치를 넓게 훑기 (회전 0)
    2) 이전 구멍 위치(holes.yaml)가 있으면 그것도 출발점으로 추가
    3) 출발점마다 위치·간격·회전을 조금씩 바꿔 가며 점수를 올림 (좌표 하강)
    4) 가장 높은 점수의 격자 → 구멍 중심 = 격자 교점

가정
    - R1C1 이 사진 왼쪽 위 (트레이를 180° 돌려 넣으면 칸 번호가 바뀜 → 방향 표시를 해 두세요)
    - 트레이 가장자리는 바깥 구멍 중심에서 margin(기본 0.5) × 간격만큼 떨어져 있음
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class TrayFit:
    ok: bool
    score: float
    params: tuple            # 원본 이미지 px: (cx, cy, pitch_x, pitch_y, theta_rad)
    holes: dict              # {칸: [x, y] 정규화 좌표 (0~1, 이미지 크기 대비)}
    corners: list            # 트레이 네 모서리 원본 px [[x, y] ...] (왼위, 오위, 오아래, 왼아래)
    reason: str = ""
    shift_ratio: float | None = None   # 이전 구멍 위치 대비 평균 이동 / 간격
    info: dict = field(default_factory=dict)


def _cell(r: int, c: int) -> str:
    return f"R{r + 1}C{c + 1}"


class HoleFinder:
    def __init__(self, rows: int = 3, cols: int = 4, margin: float = 0.5, band: float = 0.3,
                 work_width: int = 320, pitch_range=(0.15, 0.26), aspect_range=(0.92, 1.08),
                 max_rotation_deg: float = 10.0, min_score: float = 1.4, saturation_ignore: int = 85,
                 coarse_search: bool = True, aspect_weight: float = 0.5,
                 pitch_prior_weight: float = 3.0, **_):
        self.rows, self.cols = int(rows), int(cols)
        self.margin, self.band = float(margin), float(band)
        self.work_width = int(work_width)
        self.pitch_range = tuple(pitch_range)
        self.aspect_range = tuple(aspect_range)
        self.max_rot = np.radians(max_rotation_deg)
        self.min_score = float(min_score)
        self.sat_ignore = int(saturation_ignore)
        self.coarse_search = bool(coarse_search)
        self.aspect_weight = float(aspect_weight)
        self.pitch_prior_weight = float(pitch_prior_weight)

    # ── 격자 기하 ─────────────────────────────────────────────
    def _grid(self, p):
        cx, cy, px, py, th = p
        c, s = np.cos(th), np.sin(th)
        ax, ay = np.array([c, s]) * px, np.array([-s, c]) * py
        o = np.array([cx, cy])

        def g(u, v):  # (열, 행) 0부터
            return o + (u - (self.cols - 1) / 2) * ax + (v - (self.rows - 1) / 2) * ay
        return g

    def _poly(self, p, m):
        g = self._grid(p)
        return np.array([g(-m, -m), g(self.cols - 1 + m, -m), g(self.cols - 1 + m, self.rows - 1 + m),
                         g(-m, self.rows - 1 + m)], np.float32)

    # ── 점수 ─────────────────────────────────────────────────
    def _maps(self, small):
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        S, V = hsv[..., 1], hsv[..., 2]
        ignore = S > self.sat_ignore
        vals = V[~ignore]
        if vals.size < 100:
            raise ValueError("트레이·배경으로 쓸 픽셀이 너무 적습니다 (잎이 화면을 거의 덮음)")
        t, _ = cv2.threshold(vals.reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        dark = ((V <= t) & ~ignore).astype(np.float32)
        bright = ((V > t) & ~ignore).astype(np.float32)
        code = (dark + 2 * bright).astype(np.int32).ravel()      # 0 잎(무시) / 1 어두움 / 2 밝음 — 점수 합산용
        return dark, bright, (~ignore).astype(np.float32), float(t), code

    def _side_quads(self, p):
        """바깥 띠를 네 변(위·오른쪽·아래·왼쪽)으로 나눈 사각형들 — 모서리 부분은 빼서 변끼리 겹치지 않게."""
        g = self._grid(p)
        m, b = self.margin, self.margin + self.band
        c1, r1 = self.cols - 1, self.rows - 1
        q = [(g(-m, -b), g(c1 + m, -b), g(c1 + m, -m), g(-m, -m)),               # 위
             (g(c1 + m, -m), g(c1 + b, -m), g(c1 + b, r1 + m), g(c1 + m, r1 + m)),  # 오른쪽
             (g(-m, r1 + m), g(c1 + m, r1 + m), g(c1 + m, r1 + b), g(-m, r1 + b)),  # 아래
             (g(-b, -m), g(-m, -m), g(-m, r1 + m), g(-b, r1 + m))]                # 왼쪽
        return [np.round(np.array(x)).astype(np.int32) for x in q]

    def _score(self, p, maps):
        dark, _, _, _, code, pitch0 = maps
        h, w = dark.shape
        inner_poly = self._poly(p, self.margin)
        pts = np.round(inner_poly).astype(np.int32)
        # 영역 번호 그림에 안쪽(1)·바깥 띠 네 변(2~5)을 그리고 (번호×3 + 어두움/밝음 코드)를 한 번에 셈
        reg = np.zeros((h, w), np.int32)
        for k, quad in enumerate(self._side_quads(p)):
            cv2.fillConvexPoly(reg, quad, 2 + k)
        cv2.fillConvexPoly(reg, pts, 1)
        cnt = np.bincount(reg.ravel() * 3 + code, minlength=18).reshape(6, 3)   # [영역, 잎/어두움/밝음]
        n_val = cnt[:, 1] + cnt[:, 2]
        if n_val[1] < 50:
            return -1.0
        off = max(cv2.contourArea(inner_poly) - cnt[1].sum(), 0.0)
        inner_score = cnt[1, 1] / (n_val[1] + off)
        # 바깥 띠: 변마다 밝은 비율 → 평균 (보이는 픽셀이 너무 적은 변은 빼고)
        sides = [cnt[k, 2] / n_val[k] for k in range(2, 6) if n_val[k] >= 10]
        if len(sides) < 2:
            return -1.0
        # 화분 간격은 가로·세로가 거의 같음 → 1 에서 벗어날수록 조금씩 깎음
        pen = self.aspect_weight * abs(np.log(p[3] / p[2]))
        # 이전 구멍 위치의 간격과 다를수록 깎음 — 카메라 높이가 그대로면 사진 속 간격도 거의 그대로이므로
        # (트레이 한쪽 테두리가 잎·전선에 가려 밝기만으로는 간격이 정해지지 않을 때 이것이 간격을 잡아 줌)
        if pitch0 is not None and self.pitch_prior_weight > 0:
            pen += self.pitch_prior_weight * (abs(np.log(p[2] / pitch0[0])) + abs(np.log(p[3] / pitch0[1])))
        return float(inner_score + np.mean(sides) - pen)

    def _allowed(self, p, w):
        px, py, th = p[2], p[3], p[4]
        return (self.pitch_range[0] * w <= px <= self.pitch_range[1] * w
                and self.aspect_range[0] <= py / px <= self.aspect_range[1] and abs(th) <= self.max_rot)

    # ── 탐색 ─────────────────────────────────────────────────
    def _coarse(self, maps):
        h, w = maps[0].shape
        best = (-1.0, None)
        lo, hi = self.pitch_range[0] * w, self.pitch_range[1] * w
        pitch0 = maps[-1]
        if pitch0 is not None and self.pitch_prior_weight > 0:     # 이전 간격 ±10% 만 훑기 (빠르게)
            m0 = float(np.sqrt(pitch0[0] * pitch0[1]))
            pitches = np.clip(np.linspace(0.9 * m0, 1.1 * m0, 5), lo, hi)
        else:
            pitches = np.linspace(lo, hi, 12)
        for px in np.unique(pitches):
            for asp in sorted({float(np.clip(a, *self.aspect_range)) for a in (0.96, 1.0, 1.04)}):
                py = px * asp
                for cx in np.arange(0.2 * w, 0.8 * w, px / 6):
                    for cy in np.arange(0.2 * h, 0.8 * h, px / 6):
                        p = (cx, cy, px, py, 0.0)
                        s = self._score(p, maps)
                        if s > best[0]:
                            best = (s, p)
        return best

    def _refine(self, p, maps):
        w = maps[0].shape[1]
        p = np.array(p, float)
        steps = np.array([p[2] / 8, p[2] / 8, p[2] / 20, p[3] / 20, np.radians(1.5)])
        s0 = self._score(p, maps)
        for _ in range(6):
            improved = True
            while improved:
                improved = False
                for i in range(5):
                    for d in (-1, 1):
                        q = p.copy()
                        q[i] += d * steps[i]
                        if not self._allowed(q, w):
                            continue
                        s = self._score(q, maps)
                        if s > s0 + 1e-6:
                            p, s0, improved = q, s, True
            steps /= 2
        return s0, p

    def _params_from_holes(self, holes_norm, w, h):
        """정규화 구멍 좌표(이전 holes.yaml) → 격자 파라미터 (작은 사진 px). 칸이 부족하면 None."""
        pts, uv = [], []
        for r in range(self.rows):
            for c in range(self.cols):
                xy = (holes_norm or {}).get(_cell(r, c))
                if xy is not None:
                    pts.append([xy[0] * w, xy[1] * h])
                    uv.append([c - (self.cols - 1) / 2, r - (self.rows - 1) / 2, 1.0])
        if len(pts) < 4:
            return None
        A, *_ = np.linalg.lstsq(np.array(uv), np.array(pts), rcond=None)   # [ax; ay; o]
        ax, ay, o = A[0], A[1], A[2]
        th = float(np.arctan2(ax[1], ax[0]))
        return (float(o[0]), float(o[1]), float(np.linalg.norm(ax)), float(np.linalg.norm(ay)), th)

    def find(self, image: np.ndarray, prior: dict | None = None) -> TrayFit:
        """image: BGR 원본. prior: 이전 구멍 위치 {칸: [x, y] 정규화} (있으면 출발점·이동량 비교에 사용)."""
        H, W = image.shape[:2]
        k = self.work_width / W
        small = cv2.resize(image, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
        h, w = small.shape[:2]
        pp = self._params_from_holes(prior, w, h) if prior else None
        pitch0 = (pp[2], pp[3]) if pp is not None else None
        maps = self._maps(small) + (pitch0,)

        starts = []
        if self.coarse_search:
            s, p = self._coarse(maps)
            if p is not None:
                starts.append(("search", p))
        if pp is not None:
            pp = (pp[0], pp[1], float(np.clip(pp[2], self.pitch_range[0] * w, self.pitch_range[1] * w)),
                  pp[3], float(np.clip(pp[4], -self.max_rot, self.max_rot)))
            pp = (pp[0], pp[1], pp[2], float(np.clip(pp[3], pp[2] * self.aspect_range[0], pp[2] * self.aspect_range[1])), pp[4])
            starts.append(("prior", pp))
        if not starts:
            return TrayFit(False, -1.0, (), {}, [], reason="출발점이 없습니다 (coarse_search 꺼짐 + 이전 위치 없음)")

        best = (-1.0, None, "")
        for name, p0 in starts:
            s, p = self._refine(p0, maps)
            if s > best[0]:
                best = (s, p, name)
        score, p, start = best

        g = self._grid(p)
        holes = {_cell(r, c): [round(float(g(c, r)[0] / w), 4), round(float(g(c, r)[1] / h), 4)]
                 for r in range(self.rows) for c in range(self.cols)}
        params = (float(p[0] / k), float(p[1] / k), float(p[2] / k), float(p[3] / k), float(p[4]))
        corners = (self._poly(p, self.margin) / k).round(1).tolist()

        shift = None
        if prior:
            d = [np.hypot((holes[c][0] - prior[c][0]) * W, (holes[c][1] - prior[c][1]) * H)
                 for c in holes if c in prior]
            shift = round(float(np.mean(d) / params[2]), 3) if d else None

        ok = score >= self.min_score
        reason = "" if ok else f"트레이 맞춤 점수 {score:.2f} 가 기준 {self.min_score} 미만"
        return TrayFit(ok, round(score, 3), params, holes, corners, reason, shift,
                       info=dict(start=start, otsu=maps[3], work_size=[w, h], image_size=[W, H],
                                 pitch_px=round(params[2], 1), rotation_deg=round(float(np.degrees(params[4])), 2)))


def draw_fit(image: np.ndarray, fit: TrayFit, radius_ratio: float = 0.42, title: str = "") -> np.ndarray:
    """확인용 그림: 트레이 테두리(빨강), 구멍 원(청록), 칸 이름."""
    vis = image.copy()
    H, W = vis.shape[:2]
    t = max(2, W // 600)
    if fit.corners:
        cv2.polylines(vis, [np.round(np.array(fit.corners)).astype(np.int32)], True, (0, 0, 255), t + 1)
    r = int(radius_ratio * fit.params[2]) if fit.params else 20
    for cid, (x, y) in fit.holes.items():
        c = (int(x * W), int(y * H))
        cv2.circle(vis, c, r, (255, 255, 0), t)
        cv2.putText(vis, cid, (c[0] - 3 * r // 4, c[1]), cv2.FONT_HERSHEY_SIMPLEX, W / 1600, (0, 0, 255), t)
    label = f"{title} score {fit.score:.2f} {'OK' if fit.ok else 'FAIL'}"
    if fit.shift_ratio is not None:
        label += f" shift {fit.shift_ratio:.2f} pitch"
    cv2.putText(vis, label, (20, 30 + 10 * t), cv2.FONT_HERSHEY_SIMPLEX, W / 1400, (0, 0, 255), t + 1)
    return vis
