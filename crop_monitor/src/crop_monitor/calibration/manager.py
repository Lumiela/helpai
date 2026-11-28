"""보정 관리 — 상태 확인 → (필요하면) B 자동 미세 조정 → (실패하면) A 체커보드 재보정 요청.

상태 (세로 어긋남 중앙값, 보정 해상도 px)
    ok      : ≤ ok_dy_px (기본 2.0, 보고서 경고 기준과 같음)       → 아무것도 안 함
    drift   : ≤ escalate_dy_px (기본 15)                          → B 시도
    bad     : 그보다 큼, 또는 매칭점이 거의 없음                    → B 시도는 하되 실패하면 A 요청
    swapped : 지금 좌우로는 시차가 음수, 바꾸면 양수                  → B 하지 않고 좌우 설정 확인 요청
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from crop_monitor.registry import resolve

from . import geometry as geo
from .refine import RefineConfig, RefineResult, refine, tray_distance_cm
from .store import CalibStore


@dataclass
class AutoCalibConfig:
    enabled: bool = True
    in_pipeline: bool = True          # 파이프라인 실행 중 세로 어긋남 경고가 나면 그 사진 쌍으로 B 시도
    ok_dy_px: float = 2.0
    escalate_dy_px: float = 15.0
    reference_cm: float | None = None # 줄자로 잰 카메라 → 트레이 거리(cm). 없으면 이력의 거리 기준 사용
    history_dir: str = "config/stereo/history"
    refine: dict = field(default_factory=dict)   # RefineConfig 값 덮어쓰기


@dataclass
class MaintainResult:
    action: str                       # none | refined | escalate | swapped | failed
    status: str                       # ok | drift | bad | swapped
    message: str
    health: dict = field(default_factory=dict)
    refine: RefineResult | None = None
    saved_id: str | None = None


def load_config() -> AutoCalibConfig:
    from crop_monitor.registry import _raw

    raw = dict(((_raw().get("height") or {}).get("autocalib")) or {})
    return AutoCalibConfig(**raw)


def get_store(height_cfg, cfg: AutoCalibConfig | None = None) -> CalibStore:
    cfg = cfg or load_config()
    store = CalibStore(Path(height_cfg.calib), resolve(cfg.history_dir))
    store.ensure_initial()
    return store


def health(c: dict, pairs, match_width: int = 1280) -> dict:
    """쌍마다 지금 보정의 세로 어긋남·시차 + 좌우 바뀜 검사. pairs: [(스테레오 왼쪽, 오른쪽)]."""
    per = []
    for L, R in pairs:
        pl, pr = geo.match(L, R, match_width)
        src = (L.shape[1], L.shape[0])
        a, b = geo.to_calib_px(pl, src, c), geo.to_calib_px(pr, src, c)
        s = geo.pair_stats(c, a, b)
        sw = geo.pair_stats(c, b, a)  # 좌우를 바꿔 넣었을 때
        s["swapped_dy_median"], s["swapped_disp_median"] = sw["dy_median"], sw["disp_median"]
        per.append(s)
    dys = [p["dy_median"] for p in per if p["dy_median"] is not None]
    dy = round(float(np.median(dys)), 2) if dys else None
    disp = [p["disp_median"] for p in per if p["disp_median"] is not None]
    sw_disp = [p["swapped_disp_median"] for p in per if p["swapped_disp_median"] is not None]
    sw_dy = [p["swapped_dy_median"] for p in per if p["swapped_dy_median"] is not None]
    # 시차 부호가 가장 확실한 신호: 지금 좌우로는 음수, 바꾸면 양수 (카메라가 크게 움직였으면 세로 어긋남은 둘 다 클 수 있음)
    swapped = bool(disp and sw_disp and np.median(disp) < -5 and np.median(sw_disp) > 5)
    return dict(dy_median=dy, pairs=per, swapped=swapped,
                swapped_dy_median=round(float(np.median(sw_dy)), 2) if sw_dy else None)


def classify(h: dict, cfg: AutoCalibConfig) -> str:
    if h.get("swapped"):
        return "swapped"
    dy = h.get("dy_median")
    if dy is None:
        return "bad"
    if dy <= cfg.ok_dy_px:
        return "ok"
    return "drift" if dy <= cfg.escalate_dy_px else "bad"


A_HINT = ("체커보드 재보정(A)이 필요합니다: 보드를 여러 위치·각도로 동시 촬영한 뒤 "
          "uv run python scripts/height/calib_manager.py checkerboard --since <촬영 시작 시각> --square 24.2")


def maintain(pairs, height_cfg, cfg: AutoCalibConfig | None = None, dry_run: bool = False,
             force: bool = False) -> MaintainResult:
    """pairs: [(스테레오 왼쪽 BGR, 오른쪽 BGR)]. 상태가 ok 가 아니면(또는 force) B 를 시도하고 결과를 저장."""
    cfg = cfg or load_config()
    store = get_store(height_cfg, cfg)
    c = store.load()
    h = health(c, pairs)
    status = classify(h, cfg)
    if status == "swapped":
        sdy = h.get("swapped_dy_median")
        tail = (f" 바꿔도 세로 어긋남이 {sdy}px 라 카메라도 움직였습니다 → 좌우를 바꾼 뒤 " + A_HINT
                if sdy is not None and sdy > cfg.escalate_dy_px else
                " 바꾸면 세로 어긋남 " + str(sdy) + "px → 바꾼 뒤 auto 를 다시 실행하세요")
        return MaintainResult("swapped", status, "좌우가 바뀐 것 같습니다 (지금 좌우로는 시차가 음수, 바꾸면 양수). "
                              "config/crops.yaml 의 cameras.stereo_left/right 를 서로 바꾸세요." + tail, h)
    if status == "ok" and not force:
        return MaintainResult("none", status, f"보정 정상 (세로 어긋남 {h['dy_median']}px ≤ {cfg.ok_dy_px}px)", h)

    rcfg = RefineConfig(**cfg.refine)
    ref = cfg.reference_cm if cfg.reference_cm is not None else store.reference_cm()
    r = refine(c, pairs, rcfg, reference_cm=ref, height_cfg=height_cfg)
    if not r.ok:
        hint = A_HINT if status == "bad" or "A)" in r.reason else "다른 시간의 사진 쌍으로 다시 시도하거나 " + A_HINT
        return MaintainResult("escalate", status, f"자동 미세 조정(B) 실패: {r.reason}. {hint}", h, r)
    if not force and h["dy_median"] is not None and h["dy_median"] - r.after["dy_median"] < rcfg.min_improvement:
        return MaintainResult("none", status, f"자동 미세 조정(B) 결과가 지금과 거의 같아 바꾸지 않음 ({r.summary()})", h, r)
    if dry_run:
        return MaintainResult("refined", status, f"[시험] 자동 미세 조정(B) 가능: {r.summary()} (저장 안 함)", h, r)

    meta = dict(source="B", note="자동 미세 조정", before_dy_px=r.before.get("dy_median"),
                rect_dy_median_px=r.after.get("dy_median"), rotation_change_deg=r.rotation_change_deg,
                yaw_change_deg=r.yaw_change_deg, n_points=r.n_points, pairs=len(pairs))
    if ref is not None:
        meta["reference_cm"] = ref   # 거리 기준은 다음 버전으로 이어짐
    cid = store.save(r.calib, meta, activate=True)
    return MaintainResult("refined", status, f"자동 미세 조정(B) 적용 [{cid}]: {r.summary()}", h, r, cid)


def set_reference(pair, height_cfg, cfg: AutoCalibConfig | None = None, value_cm: float | None = None) -> tuple[str, float]:
    """지금 보정이 맞을 때 트레이 거리를 기준으로 기록 (B 가 좌우 돌아감을 맞출 때 씀).
    value_cm 을 주면(줄자 실측) 그 값을 기록. 반환: (보정 id, 기준 cm)."""
    cfg = cfg or load_config()
    store = get_store(height_cfg, cfg)
    cm = value_cm if value_cm is not None else tray_distance_cm(store.load(), pair, height_cfg)
    if cm is None:
        raise ValueError("트레이 거리를 잴 수 없습니다")
    cid = store.current_id()
    store.update_meta(cid, reference_cm=round(float(cm), 1),
                      reference_set=datetime.now().isoformat(timespec="seconds"),
                      reference_source="줄자 실측" if value_cm is not None else "트레이 거리 측정")
    return cid, round(float(cm), 1)
