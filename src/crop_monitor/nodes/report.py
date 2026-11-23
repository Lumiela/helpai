"""보고서 생성 (규칙 + 템플릿 방식).

LLM 요약이 필요해지면 make_report 를 교체하거나 뒤에 노드를 하나 더 붙이면 됩니다.
"""

from __future__ import annotations

from crop_monitor.state import PipelineState

STATUS_LABEL = {"ok": "정상", "warning": "주의", "critical": "위험", "empty": "빈 칸"}


def _fmt_counts(counts: dict) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "-"


def make_report(state: PipelineState) -> PipelineState:
    d = state["crop_data"]
    m = d["modules"]
    lines = [
        f"# 작물 모니터링 보고서 — {d['crop_name']} ({d['crop_id']})",
        "",
        f"- 종합 상태: **{STATUS_LABEL.get(d['status'], d['status'])}**",
        f"- 식물 수: {sum(1 for c in d['cells'] if c['present'])} / 칸 {len(d['cells'])}",
        "- 실행 모듈: " + ", ".join(f"{n} {'O' if ok else 'X'}" for n, ok in
                                     (("생장 단계", m["growth"]), ("이상 상태", m["disease"]), ("높이", m["height"]))),
    ]
    if d["height_info"]:
        hi = d["height_info"]
        lines.append(f"- 높이 측정: {hi['align']['mode']} 모드, 기준면 {hi['reference']}"
                     f" (화면 중앙 기준면 거리 {hi['reference_center_cm']}cm), 유효 픽셀 {hi['valid_ratio'] * 100:.0f}%")
    if d["disease_info"]:
        di = d["disease_info"]
        lines.append(f"- 잎 분류 모델: {di['cls_model']} / VLM 재확인: "
                     + (f"{di['vlm']['backend']} (토큰 {di['vlm']['tokens']})" if di["vlm"] else "사용 안 함"))

    if d["growth_table"]:
        lines += ["", "## 트레이 생장 단계", "```", d["growth_table"], "```"]

    lines += ["", "## 칸별 결과", "",
              "| 칸 | 상태 | 생장 단계 | 높이(cm) | 잎 상태 | 이상 |",
              "|---|---|---|---|---|---|"]
    for c in d["cells"]:
        if not c["present"]:
            lines.append(f"| {c['cell']} | {STATUS_LABEL['empty']} | - | - | - | - |")
            continue
        h = c["height"]["height_cm"] if c["height"] else "-"
        anoms = ", ".join(f"{a['label']}({a['camera']} {a['id']})" for a in c["leaves"]["anomalies"]) or "없음"
        lines.append(f"| {c['cell']} | {STATUS_LABEL[c['status']]} | {c['growth_stage_label'] or '-'} | {h} "
                     f"| {_fmt_counts(c['leaves']['counts'])} | {anoms} |")
    issues = [(c["cell"], msg) for c in d["cells"] if c.get("issues") for msg in c["issues"]]
    if issues:
        lines += ["", "## 판정 사유"] + [f"- {cid}: {msg}" for cid, msg in issues]

    u = d["unassigned_leaves"]
    if u["counts"]:
        lines += ["", "## 칸에 속하지 않은 잎", f"- {_fmt_counts(u['counts'])}"]
        lines += [f"- {a['label']} ({a['camera']} {a['id']})" for a in u["anomalies"]]

    if state.get("warnings"):
        lines += ["", "## 처리 경고"] + [f"- {w}" for w in state["warnings"]]

    return {"report": "\n".join(lines)}


def unknown_crop(state: PipelineState) -> PipelineState:
    msg = f"등록되지 않은 작물 ID: {state.get('crop_id')!r} (config/crops.yaml 에 추가 필요)"
    return {"report": f"# 작물 모니터링 보고서\n\n처리 실패: {msg}", "warnings": [msg]}
