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


VLM_KEYS = ("label", "verdict", "confidence", "symptoms", "reason", "error")
# VLM 재확인 판정 → 최종 잎 상태
#   FALSE_ALARM : 모델은 병, VLM 은 정상          → FRESH (정상)
#   MISSED      : 모델은 정상, VLM 은 병          → ANOMALY (VLM 이 본 병)
#   OTHER_CLASS : 둘 다 병, 종류가 다름           → ANOMALY (VLM 이 본 병)
#   NOT_A_LEAF  : 잎이 아님                      → 판정에서 뺌 (NOT_A_LEAF)
DEFAULT_VLM_APPLY = ["FALSE_ALARM", "MISSED", "OTHER_CLASS", "NOT_A_LEAF"]
NOT_A_LEAF = "NOT_A_LEAF"


def _final_state(l: dict, apply: set) -> tuple[str, str | None, str | None]:
    """(최종 상태, 최종 이상 유형, 적용한 VLM 판정 또는 None)."""
    v = l.get("vlm") or {}
    verdict = v.get("verdict")
    if verdict not in apply:
        return l["state"], l.get("anomaly_class"), None
    if verdict == "FALSE_ALARM":
        return "FRESH", None, verdict
    if verdict in ("MISSED", "OTHER_CLASS"):
        return "ANOMALY", v["label"], verdict
    if verdict == "NOT_A_LEAF":
        return NOT_A_LEAF, None, verdict
    return l["state"], l.get("anomaly_class"), None


def _leaf_summary(leaves: list, rules: dict) -> dict[str, Any]:
    """VLM 재확인 판정(alerts.vlm_apply)을 적용한 최종 잎 상태로 요약.

    counts      : 최종 상태별 잎 수 (VLM 이 잎이 아니라고 본 것은 NOT_A_LEAF)
    model_counts: 분류 모델 상태별 잎 수 (VLM 적용 전)
    anomalies   : 상태 판정에 쓰는 이상 잎 (최종)
    changed     : VLM 판정으로 상태가 바뀐 잎 (모델 판정, VLM 판정, 바뀐 결과)
    unknown     : 상태 판정에 쓰는 판단 보류(UNKNOWN) 잎 수"""
    anomaly_rules = rules.get("anomalies", {})
    apply = set((rules.get("alerts") or {}).get("vlm_apply", DEFAULT_VLM_APPLY) or [])

    def describe(state, cls):
        if state == "ANOMALY":
            return anomaly_rules.get(cls, {}).get("label", cls)
        return {"FRESH": "정상", "UNKNOWN": "판단 보류", NOT_A_LEAF: "잎 아님"}.get(state, state)

    counts: dict[str, int] = {}
    model_counts: dict[str, int] = {}
    anomalies, changed, unknown = [], [], 0
    for l in leaves:
        model_counts[l["state"]] = model_counts.get(l["state"], 0) + 1
        state, cls, applied = _final_state(l, apply)
        counts[state] = counts.get(state, 0) + 1
        item = dict(camera=l["camera"], id=l["id"], state=state, box=l["box"])
        if "vlm" in l:
            item["vlm"] = {k: l["vlm"][k] for k in VLM_KEYS if l["vlm"].get(k)}
        if applied:
            item["model"] = dict(state=l["state"], type=l.get("anomaly_class"),
                                 label=describe(l["state"], l.get("anomaly_class")))
            changed.append({**item, "type": cls, "label": describe(state, cls)})
        if state == "ANOMALY":
            r = anomaly_rules.get(cls, {})
            item.update(type=cls, label=r.get("label", cls), severity=r.get("severity", "warning"),
                        prob=(l.get("probs") or {}).get(cls))  # VLM 이 바꾼 병이면 모델 확률은 참고용
            anomalies.append(item)
        elif state == "UNKNOWN":
            unknown += 1
    return dict(counts=counts, model_counts=model_counts, anomalies=anomalies, changed=changed, unknown=unknown)


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
        # 두 카메라가 같은 잎을 각각 잡을 수 있으므로 합계가 아니라 카메라별 개수 중 최댓값으로 센다
        per_cam: dict[str, int] = {}
        for a in leaves["anomalies"]:
            per_cam[a["camera"]] = per_cam.get(a["camera"], 0) + 1
        n_anom = max(per_cam.values(), default=0)
        crit_n = alerts.get("critical_leaf_count")
        if crit_n and n_anom >= crit_n:
            status = worst(status, "critical")
            cams = ", ".join(f"{k} {v}" for k, v in sorted(per_cam.items()))
            issues.append(f"이상 잎 {n_anom}장 (카메라별 최대, {cams} / 기준 {crit_n}장 이상)")
        if leaves["unknown"]:
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
