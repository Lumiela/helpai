"""그래프 전체 실행 (실제 모델 사용, CPU 에서 1분 안팎)."""

import pytest

from crop_monitor import build_graph

IMAGES = {"CL": "data/samples/rose_CL.jpg", "CR": "data/samples/rose_CR.jpg"}


def test_unknown_crop():
    out = build_graph().invoke({"crop_id": "Z", "images": IMAGES})
    assert "crop_data" not in out
    assert "등록되지 않은 작물" in out["report"]


@pytest.mark.slow
def test_rose_samples_end_to_end():
    out = build_graph().invoke({"crop_id": "A", "images": IMAGES})
    d = out["crop_data"]
    assert d["modules"] == {"growth": True, "disease": True, "height": True}
    present = [c for c in d["cells"] if c["present"]]
    assert len(present) == 6                      # holes.yaml 고정 배치 O 6칸
    assert all(c["growth_stage"] for c in present)
    assert all(c["height"] is not None for c in present)
    assert sum(len(c["leaves"]["anomalies"]) for c in present) >= 1
    assert "칸별 결과" in out["report"]
