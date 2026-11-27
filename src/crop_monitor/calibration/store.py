"""보정 이력 저장소.

    config/stereo/stereo_calib.npz          ← 지금 쓰는 보정 (높이 모듈이 읽는 파일, 경로 그대로)
    config/stereo/history/<id>.npz          ← 버전마다 보관
    config/stereo/history/<id>.json         ← 정보: 출처(A/B/initial), 시각, 세로 어긋남, 거리 기준 등
    config/stereo/history/current.json      ← 지금 쓰는 버전 id

id 예: 20261127T103012_B
교체는 임시 파일에 쓴 뒤 바꿔치기(원자적)라 실행 중인 파이프라인이 반쯤 쓴 파일을 읽지 않는다.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

import numpy as np

from . import geometry as geo


class CalibStore:
    def __init__(self, current: Path, history_dir: Path | None = None):
        self.current = Path(current)
        self.dir = Path(history_dir) if history_dir else self.current.parent / "history"

    # ── 읽기 ─────────────────────────────────────────────────
    def list(self) -> list[dict]:
        if not self.dir.exists():
            return []
        out = []
        for p in sorted(self.dir.glob("*.json")):
            if p.name == "current.json":
                continue
            out.append(json.loads(p.read_text(encoding="utf-8")))
        return sorted(out, key=lambda m: m["id"])

    def current_id(self) -> str | None:
        p = self.dir / "current.json"
        return json.loads(p.read_text(encoding="utf-8")).get("id") if p.exists() else None

    def meta(self, cid: str | None = None) -> dict | None:
        cid = cid or self.current_id()
        p = self.dir / f"{cid}.json" if cid else None
        return json.loads(p.read_text(encoding="utf-8")) if p and p.exists() else None

    def load(self, cid: str | None = None) -> dict:
        return geo.load(self.dir / f"{cid}.npz" if cid else self.current)

    def reference_cm(self) -> float | None:
        """지금 보정의 트레이 거리 기준 (set-reference 또는 A 직후 기록). 없으면 None."""
        m = self.meta() or {}
        return m.get("reference_cm")

    # ── 쓰기 ─────────────────────────────────────────────────
    def ensure_initial(self) -> str | None:
        """이력이 비어 있으면 지금 파일을 'initial' 로 등록 (처음 한 번)."""
        if self.list() or not self.current.exists():
            return self.current_id()
        report = self.current.with_name(self.current.stem + "_report.json")
        extra = {}
        if report.exists():
            r = json.loads(report.read_text(encoding="utf-8"))
            extra = {k: r.get(k) for k in ("method", "rect_dy_median_px", "baseline_mm", "stereo_rms_px")}
        cid = self.save(geo.load(self.current), dict(source="initial", note="기존 stereo_calib.npz", **extra),
                        activate=False, stamp=datetime.fromtimestamp(self.current.stat().st_mtime))
        # 지금 파일은 그대로 두고 '지금 버전' 표시만 함 (같은 내용을 다시 쓰지 않음)
        (self.dir / "current.json").write_text(json.dumps(dict(id=cid), ensure_ascii=False), encoding="utf-8")
        return cid

    def save(self, c: dict, meta: dict, activate: bool = True, stamp: datetime | None = None) -> str:
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = stamp or datetime.now()
        cid = f"{stamp.strftime('%Y%m%dT%H%M%S')}_{meta.get('source', 'X')}"
        n = 1
        while (self.dir / f"{cid}.npz").exists():
            n += 1
            cid = f"{stamp.strftime('%Y%m%dT%H%M%S')}_{meta.get('source', 'X')}{n}"
        R, T = geo.pose(c)
        info = dict(meta)
        info.update(id=cid, created=stamp.isoformat(timespec="seconds"), parent=self.current_id(),
                    baseline_mm=round(float(np.linalg.norm(T)), 2), rot_deg=round(geo.rot_deg(R), 3),
                    size=[int(v) for v in c["size"]])
        geo.save(self.dir / f"{cid}.npz", c)
        (self.dir / f"{cid}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        if activate:
            self.activate(cid)
        return cid

    def update_meta(self, cid: str, **kv) -> None:
        p = self.dir / f"{cid}.json"
        m = json.loads(p.read_text(encoding="utf-8"))
        m.update(kv)
        p.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")

    def activate(self, cid: str) -> None:
        src = self.dir / f"{cid}.npz"
        if not src.exists():
            raise FileNotFoundError(f"보정 버전이 없습니다: {cid}")
        self.current.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.current.with_suffix(".tmp.npz")
        shutil.copy2(src, tmp)
        os.replace(tmp, self.current)
        (self.dir / "current.json").write_text(json.dumps(dict(id=cid), ensure_ascii=False), encoding="utf-8")
        _clear_caches()

    def rollback(self, to: str | None = None) -> str:
        """to 를 주면 그 버전으로, 없으면 지금 버전의 parent 로."""
        target = to or (self.meta() or {}).get("parent")
        if not target:
            raise ValueError("되돌릴 이전 버전이 없습니다")
        self.activate(target)
        return target


def _clear_caches() -> None:
    try:
        from crop_monitor.modules.height import height as H
        H._load_calib.cache_clear()
    except Exception:
        pass
