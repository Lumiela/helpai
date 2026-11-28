"""모델 없이 도는 단위 테스트: 키 읽기, API 키 없을 때 설정, 병합 규칙."""

import numpy as np

from crop_monitor import keys
from crop_monitor.modules.height.height import HeightResult
from crop_monitor.modules.rose_growth import adapter as growth_adapter
from crop_monitor.nodes.merge import merge_results
from crop_monitor.registry import resolve


def test_read_key_formats(tmp_path, monkeypatch):
    monkeypatch.setattr(keys, "SECRETS_DIR", tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert keys.read_key() is None
    (tmp_path / "ANTHROPIC_API_KEY.txt").write_text("# 주석\nANTHROPIC_API_KEY='sk-ant-test'\n", encoding="utf-8")
    assert keys.read_key() == "sk-ant-test"


def test_growth_disables_vlm_without_key():
    cfg, warnings = growth_adapter._prepare_config(str(resolve("config/rose/growth.yaml")), False, "disable_vlm")
    det = cfg["grading"]["detector"]
    assert det["review"] is False and det["add_missed"] is False
    assert warnings
    cfg, warnings = growth_adapter._prepare_config(str(resolve("config/rose/growth.yaml")), True, "disable_vlm")
    assert cfg["grading"]["detector"]["review"] is True and not warnings


def _fake_state(n_black_spot):
    """칸 R1C1 하나: 마스크 (100..200, 100..200), 높이 지도 10cm, 흑점병 잎 n장 + 정상 잎 1장."""
    mask = np.ones((100, 100), bool)
    hmap = np.full((480, 640), np.nan, np.float32)
    hmap[50:150, 50:150] = 10.0  # 원본 1280x960 → 지도 640x480 (근사 모드: 크기만 바뀜)
    height = HeightResult(hmap, hmap, np.zeros(4), (1280, 960),
                          dict(align=dict(mode="none"), map_size=[640, 480], reference="ransac",
                               reference_center_cm=40.0, valid_ratio=0.5, calib=None))
    leaf = lambda i, state, cls: dict(id=f"L{i:04d}", box=[140, 140, 160, 160], state=state, anomaly_class=cls,
                                      probs={"black_spot": 0.9 if cls else 0.0, "healthy": 0.1})
    leaves = [leaf(i, "ANOMALY", "black_spot") for i in range(n_black_spot)] + [leaf(99, "FRESH", None)]
    return {
        "crop_id": "A",
        "frames": {},
        "growth": {"cells": [{"cell": "R1C1", "row": 1, "col": 1, "present": True, "grade": "grade_one",
                              "camera_grades": {"CL": "grade_one"}, "buds": []},
                             {"cell": "R1C2", "row": 1, "col": 2, "present": False, "grade": None,
                              "camera_grades": {}, "buds": []}],
                   "segments": {"CR": {"R1C1": (mask, (100, 100))}, "CL": {"R1C1": (mask, (100, 100))}},
                   "table": "", "usage": None},
        "disease": {"cls_model": "x.pt", "vlm": None,
                    "cameras": {"CL": {"leaves": leaves, "counts": {}, "L": 0, "timing": {}}}},
        "height": height,
    }


def test_merge_cell_status_and_height():
    d = merge_results(_fake_state(1))["crop_data"]
    c = d["cells"][0]
    assert c["growth_stage_label"].startswith("1단계")
    assert c["height"]["height_cm"] == 10.0
    assert c["leaves"]["counts"] == {"ANOMALY": 1, "FRESH": 1}
    assert c["leaves"]["anomalies"][0]["label"] == "흑점병"
    assert c["status"] == "warning" and d["status"] == "warning"
    assert d["cells"][1]["status"] == "empty"


def test_merge_escalates_on_many_anomalies():
    d = merge_results(_fake_state(3))["crop_data"]
    assert d["cells"][0]["status"] == "critical"


def _vlm(label, verdict):
    return dict(label=label, verdict=verdict, confidence="medium", symptoms="증상 설명", reason="이유 설명")


def test_merge_applies_vlm_verdicts():
    """FALSE_ALARM → 정상, OTHER_CLASS·MISSED → VLM 이 본 병, NOT_A_LEAF → 판정에서 뺌. 설명도 저장."""
    from crop_monitor.nodes.report import make_report
    s = _fake_state(4)
    leaves = s["disease"]["cameras"]["CL"]["leaves"]     # L0000~L0003 흑점병, L0099 정상
    leaves[0]["vlm"] = _vlm("healthy", "FALSE_ALARM")
    leaves[1]["vlm"] = _vlm("not_a_leaf", "NOT_A_LEAF")
    leaves[2]["vlm"] = _vlm("black_spot", "AGREE")
    leaves[3]["vlm"] = _vlm("downy_mildew", "OTHER_CLASS")
    leaves[4]["vlm"] = _vlm("insects_infected", "MISSED")
    leaves.append(dict(id="L0050", box=[140, 140, 160, 160], state="UNKNOWN", anomaly_class=None,
                       probs={}, vlm=_vlm("not_a_leaf", "NOT_A_LEAF")))
    out = merge_results(s)
    c = out["crop_data"]["cells"][0]
    lv = c["leaves"]
    assert {a["id"]: a["type"] for a in lv["anomalies"]} == {
        "L0002": "black_spot", "L0003": "downy_mildew", "L0099": "insects_infected"}
    assert lv["counts"] == {"FRESH": 1, "NOT_A_LEAF": 2, "ANOMALY": 3}
    assert lv["model_counts"] == {"ANOMALY": 4, "FRESH": 1, "UNKNOWN": 1}
    assert {a["id"]: a["state"] for a in lv["changed"]} == {
        "L0000": "FRESH", "L0001": "NOT_A_LEAF", "L0003": "ANOMALY", "L0099": "ANOMALY", "L0050": "NOT_A_LEAF"}
    assert lv["anomalies"][0]["vlm"]["reason"] == "이유 설명"
    assert lv["unknown"] == 0
    assert c["status"] == "critical"            # 이상 잎 3장 (AGREE 1 + OTHER_CLASS 1 + MISSED 1)
    report = make_report({**s, **out})["report"]
    assert "VLM 재확인으로 바뀐 잎" in report and "정상(오탐)" in report and "판정에서 뺌" in report
    assert "노균병(CL L0003, VLM 다른 병 · 모델 흑점병)" in report


def test_merge_vlm_apply_can_be_disabled(monkeypatch):
    from crop_monitor import registry
    s = _fake_state(3)
    for l in s["disease"]["cameras"]["CL"]["leaves"][:3]:
        l["vlm"] = _vlm("healthy", "FALSE_ALARM")
    crop = registry.get_crop("A")
    rules = {**crop.rules, "alerts": {**crop.rules["alerts"], "vlm_apply": []}}
    monkeypatch.setattr("crop_monitor.nodes.merge.get_crop", lambda _: type(crop)(**{**crop.__dict__, "rules": rules}))
    c = merge_results(s)["crop_data"]["cells"][0]
    assert c["status"] == "critical" and not c["leaves"]["changed"]


def test_merge_counts_anomalies_per_camera_max():
    """CL 2장 + CR 1장(같은 잎일 수 있음) → 3장이 아니라 2장으로 세어 위험으로 올리지 않음."""
    s = _fake_state(2)
    cr_leaves = [dict(s["disease"]["cameras"]["CL"]["leaves"][0])]
    s["disease"]["cameras"]["CR"] = {"leaves": cr_leaves, "counts": {}, "L": 0, "timing": {}}
    c = merge_results(s)["crop_data"]["cells"][0]
    assert len(c["leaves"]["anomalies"]) == 3
    assert c["status"] == "warning" and not c["issues"]
