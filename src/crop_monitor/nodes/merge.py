"""작물별 결과 병합 공통 모듈.

공통 단위는 생장 단계 모듈이 나눈 '칸(식물)' 입니다.
    - 생장 단계 : 칸별 최종 등급 (카메라 합친 결과)
    - 높이      : 스테레오 왼쪽 카메라의 그 칸 식물 마스크 영역을 높이 지도에서 읽음
    - 잎 이상   : 카메라별 잎 박스 중심이 그 카메라의 칸 식물 마스크 안에 있으면 그 칸의 잎
그 뒤 작물별 규칙(config/rules/*.yaml)으로 칸·작물 상태를 판정합니다.
"""

from __future__ import annotations

from typing import Any

from crop_monitor.modules.height import height as height_mod
from crop_monitor.registry import get_crop, height_config, stereo_pair
from crop_monitor.state import PipelineState

SEVERITY_ORDER = {"ok": 0, "warning": 1, "critical": 2}


def worst(*levels: str) -> str:
    return max(levels, key=lambda s: SEVERITY_ORDER.get(s, 0), default="ok")


def _contains(mask, offset, x: float, y: float) -> bool:
    mx, my = int(x) - offset[0], int(y) - offset[1]
    h, w = mask.shape
    return 0 <= mx < w and 0 <= my < h and bool(mask[my, mx])


def _assign_leaves(disease: dict | None, segments: dict) -> tuple[dict, list]:
    """{칸: [잎(+camera)]}, 칸에 속하지 않은 잎 목록."""
    by_cell: dict[str, list] = {}
    unassigned = []
    for cam, r in ((disease or {}).get("cameras") or {}).items():
        segs = segments.get(cam, {})
        for leaf in r["leaves"]:
            x0, y0, x1, y1 = leaf["box"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            cell = next((cid for cid, (m, off) in segs.items() if _contains(m, off, cx, cy)), None)
            item = {**leaf, "camera": cam}
            (by_cell.setdefault(cell, []) if cell else unassigned).append(item)
    return by_cell, unassigned


def _leaf_summary(leaves: list, rules: dict) -> dict[str, Any]:
    anomaly_rules = rules.get("anomalies", {})
    counts: dict[str, int] = {}
    anomalies = []
    for l in leaves:
        counts[l["state"]] = counts.get(l["state"], 0) + 1
        if l["state"] == "ANOMALY":
            r = anomaly_rules.get(l["anomaly_class"], {})
            item = dict(camera=l["camera"], id=l["id"], type=l["anomaly_class"],
                        label=r.get("label", l["anomaly_class"]), severity=r.get("severity", "warning"),
                        prob=l["probs"].get(l["anomaly_class"]), box=l["box"])
            if "vlm" in l:
                item["vlm"] = {k: l["vlm"][k] for k in ("label", "verdict", "confidence")}
            anomalies.append(item)
    return dict(counts=counts, anomalies=anomalies)


def merge_results(state: PipelineState) -> PipelineState:
    crop = get_crop(state["crop_id"])
    rules = crop.rules
    stage_rules = rules.get("growth_stages", {})
    alerts = rules.get("alerts", {})
    left, _ = stereo_pair()
    hcfg = height_config()

    growth, disease, hres = state.get("growth"), state.get("disease"), state.get("height")
    segments = (growth or {}).get("segments", {})
    leaves_by_cell, unassigned = _assign_leaves(disease, segments)

    cells = []
    for c in (growth or {}).get("cells", []):
        cid = c["cell"]
        if not c["present"]:
            cells.append(dict(cell=cid, row=c["row"], col=c["col"], present=c["present"], status="empty"))
            continue
        stage = c["grade"]
        stage_rule = stage_rules.get(stage, {}) if stage else {}
        status, issues = "ok", []

        # 높이 (스테레오 왼쪽 카메라 마스크 기준)
        height = None
        if hres is not None and cid in segments.get(left, {}):
            mask, offset = segments[left][cid]
            height = height_mod.region_height(hres, mask, offset, hcfg)
        h_cm = height["height_cm"] if height else None
        if h_cm is not None and "height_cm" in stage_rule:
            lo, hi = stage_rule["height_cm"]
            if not lo <= h_cm <= hi:
                issues.append(f"높이 {h_cm}cm 가 {stage_rule.get('label', stage)} 정상 범위({lo}~{hi}cm) 밖")
                status = worst(status, alerts.get("height_out_of_range", "warning"))

        # 잎 이상
        leaves = _leaf_summary(leaves_by_cell.get(cid, []), rules)
        for a in leaves["anomalies"]:
            status = worst(status, a["severity"])
        n_anom = len(leaves["anomalies"])
        crit_n = alerts.get("critical_leaf_count")
        if crit_n and n_anom >= crit_n:
            status = worst(status, "critical")
            issues.append(f"이상 잎 {n_anom}장 (기준 {crit_n}장 이상)")
        if leaves["counts"].get("UNKNOWN"):
            status = worst(status, alerts.get("unknown_leaf", "warning"))

        cells.append(dict(
            cell=cid, row=c["row"], col=c["col"], present=True,
            growth_stage=stage, growth_stage_label=stage_rule.get("label", stage),
            camera_grades=c["camera_grades"], buds=c["buds"],
            height=height, leaves=leaves, issues=issues, status=status,
        ))

    overall = worst("ok", *(c["status"] for c in cells if c["status"] != "empty"))
    return {"crop_data": dict(
        crop_id=crop.crop_id, crop_name=crop.name, status=overall,
        modules=dict(growth=growth is not None, disease=disease is not None, height=hres is not None),
        growth_table=(growth or {}).get("table"),
        height_info=hres.info if hres is not None else None,
        disease_info=dict(cls_model=disease["cls_model"], vlm=disease["vlm"],
                          counts={cam: r["counts"] for cam, r in disease["cameras"].items()}) if disease else None,
        growth_usage=(growth or {}).get("usage"),
        cells=cells,
        unassigned_leaves=_leaf_summary(unassigned, rules),
    )}
