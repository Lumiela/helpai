"""A — 체커보드 재보정. 원본 도구(scripts/height/collect_pairs.py, stereo_calibrate.py)를 그대로 실행하고
결과를 보정 이력에 넣는다.

    1) (API) collect_pairs.py --since <시각> → 체커보드가 두 사진 모두에서 잡힌 쌍만 L_NN/R_NN 으로 저장
       또는 이미 모아 둔 폴더(--dir)를 그대로 사용
    2) stereo_calibrate.py → 임시 npz + _report.json (좌우 자동 교정, 불량 쌍 제외, 교차검증 포함)
    3) 검증: 평행화 세로 어긋남 중앙값 ≤ max_dy, 사용한 쌍 수 ≥ min_pairs, 간격이 이전과 비슷한지
    4) 통과하면 이력에 source=A 로 저장하고 지금 보정으로 교체
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from crop_monitor.registry import PROJECT_ROOT

from . import geometry as geo
from .store import CalibStore

SCRIPTS = PROJECT_ROOT / "scripts" / "height"


@dataclass
class CheckerboardResult:
    ok: bool
    reason: str
    report: dict = field(default_factory=dict)
    saved_id: str | None = None
    log: str = ""


def _run(args: list[str]) -> str:
    p = subprocess.run([sys.executable, *args], cwd=str(PROJECT_ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        raise RuntimeError(f"{Path(args[0]).name} 실패 (종료 코드 {p.returncode})\n{out[-2000:]}")
    return out


def run(store: CalibStore, square_mm: float, board: str = "9x6", width: int = 640, pairs_dir: str | None = None,
        since: str | None = None, min_pairs: int = 6, max_dy_px: float = 1.5, max_baseline_change: float = 0.10,
        activate: bool = True) -> CheckerboardResult:
    log = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        if pairs_dir is None:
            if not since:
                return CheckerboardResult(False, "--dir(체커보드 사진 폴더) 또는 --since(API 촬영 시각)가 필요합니다")
            pairs_dir = str(tmp / "pairs")
            log.append(_run([str(SCRIPTS / "collect_pairs.py"), "--since", since, "--out", pairs_dir,
                             "--board", board, "--width", str(width)]))
        n = len(list(Path(pairs_dir).glob("L_*.jpg")))
        if n < min_pairs:
            return CheckerboardResult(False, f"체커보드가 잡힌 사진 쌍이 {n}개뿐 (필요 {min_pairs}개). 보드를 여러 위치·각도로 더 찍으세요",
                                      log="\n".join(log))
        out = tmp / "stereo_calib.npz"
        log.append(_run([str(SCRIPTS / "stereo_calibrate.py"), "--dir", pairs_dir, "--board", board,
                         "--square", str(square_mm), "--width", str(width), "--out", str(out),
                         "--min-pairs", str(min_pairs)]))
        report = json.loads((tmp / "stereo_calib_report.json").read_text(encoding="utf-8"))
        c = geo.load(out)

        dy = report.get("rect_dy_median_px")
        used = len(report.get("pairs_used") or [])
        reason = ""
        if dy is None or dy > max_dy_px:
            reason = f"평행화 세로 어긋남 {dy}px 가 기준 {max_dy_px}px 보다 큼 → 보드가 흔들렸거나 초점이 안 맞음. 다시 촬영"
        elif used < min_pairs:
            reason = f"쓸 수 있는 쌍이 {used}개 (필요 {min_pairs}개)"
        else:
            prev = store.meta() or {}
            pb = prev.get("baseline_mm")
            if pb and abs(report["baseline_mm"] - pb) / pb > max_baseline_change:
                reason = (f"카메라 간격이 {pb}mm → {report['baseline_mm']}mm 로 {max_baseline_change:.0%} 넘게 바뀜. "
                          f"카메라를 옮겼다면 맞는 값이니 --force 로 저장하세요")
        if reason:
            return CheckerboardResult(False, reason, report, log="\n".join(log))

        meta = dict(source="A", method=report.get("method"), rect_dy_median_px=dy, pairs_used=used,
                    stereo_rms_px=report.get("stereo_rms_px"), square_mm=square_mm, board=board,
                    swapped_lr=report.get("swapped_lr"), note="체커보드 재보정")
        cid = store.save(c, meta, activate=activate)
        # 원본 도구 보고서도 지금 보정 옆에 둠 (사용 설명서·진행정리와 같은 위치)
        if activate:
            (store.current.with_name(store.current.stem + "_report.json")).write_text(
                json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        return CheckerboardResult(True, "성공", report, cid, "\n".join(log))


def baseline_mm(c: dict) -> float:
    return float(np.linalg.norm(geo.pose(c)[1]))
