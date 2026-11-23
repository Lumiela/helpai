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
