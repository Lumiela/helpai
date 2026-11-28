"""구멍(화분) 위치 자동 찾기 테스트.

rose_CL/CR : holes.yaml 을 만들 때 쓴 배치 → 자동 결과가 holes.yaml 과 가까워야 함
CL1/CR1    : 2026-11-27 사진 (트레이·카메라가 움직여 holes.yaml 과 다름). 빈 화분 R1C1, R1C3, R2C2, R3C1 은
             검은 덮개가 잘 보이므로, 자동 위치의 원 안이 어두워야(빈 칸 점수가 높아야) 제자리에 맞은 것
"""

import pytest

from crop_monitor.modules.rose_growth.rose_pipeline.config import load_config
from crop_monitor.modules.rose_growth.rose_pipeline.hole_finder import HoleFinder
from crop_monitor.modules.rose_growth.rose_pipeline.layout import camera_holes
from crop_monitor.modules.rose_growth.rose_pipeline.presence import DarkCoverPresence
from crop_monitor.nodes.preprocess import read_image


@pytest.fixture(scope="module")
def cfg():
    return load_config("config/rose/growth.yaml")


@pytest.fixture(scope="module")
def finder(cfg):
    return HoleFinder(rows=3, cols=4, **cfg["holes_auto"])


@pytest.mark.parametrize("cam", ["CL", "CR"])
def test_matches_holes_yaml_on_original_samples(finder, cfg, cam):
    fit = finder.find(read_image(f"data/samples/rose_{cam}.jpg"), prior=cfg["holes"][cam])
    assert fit.ok and fit.score > 1.6
    assert fit.shift_ratio < 0.15          # holes.yaml 과 간격의 15% 이내


@pytest.mark.parametrize("cam", ["CL", "CR"])
def test_finds_moved_tray_without_prior(finder, cam):
    """이전 위치 없이(사진만으로) 찾기 — 빈 화분 4곳의 원 안이 어두워야 함."""
    img = read_image(f"data/samples/{cam}1.jpg")
    fit = finder.find(img)
    assert fit.ok
    holes = camera_holes(fit.holes, img.shape, 0.3)
    dark = DarkCoverPresence(dark_v_max=100)
    for cid in ("R1C1", "R1C3", "R2C2", "R3C1"):
        assert dark.score(img, holes[cid]) > 0.5, cid


def test_reports_shift_from_holes_yaml(finder, cfg):
    fit = finder.find(read_image("data/samples/CL1.jpg"), prior=cfg["holes"]["CL"])
    assert fit.ok and fit.shift_ratio > 0.25   # 트레이·카메라가 움직였음을 알아챔


@pytest.mark.parametrize("cam,c1,c4", [("CL", 300, 1065), ("CR", 192, 920)])
def test_spacing_not_shrunk_by_dark_right_side(finder, cfg, cam, c1, c4):
    """CL2/CR2: 트레이 오른쪽 바깥이 검은 전선 → 예전에는 가로 간격이 5% 작게 잡혀 4열 원이 화분 왼쪽으로 빠짐.
    c1, c4 = 사진(가로 1280 기준)에서 눈으로 잰 1열·4열 화분 중심 x."""
    fit = finder.find(read_image(f"data/samples/{cam}2.jpg"), prior=cfg["holes"][cam])
    tol = 0.12 * fit.params[2] / 2           # 간격의 12% (1280 기준 px)
    assert abs(fit.holes["R1C1"][0] * 1280 - c1) < tol
    assert abs(fit.holes["R1C4"][0] * 1280 - c4) < tol


PLANTS = {
    "rose": ("rose_CL", "rose_CR", {"R1C1", "R1C3", "R2C2", "R2C4", "R3C1", "R3C3"}),
    "set1": ("CL1", "CR1", {"R1C2", "R1C4", "R2C1", "R2C3", "R3C2", "R3C4"}),
    "set2": ("CL2", "CR2", {"R1C2", "R2C1", "R2C3", "R2C4", "R3C2", "R3C4"}),
}


@pytest.mark.parametrize("name", list(PLANTS))
def test_presence_with_auto_holes(cfg, finder, name):
    """빈 화분 판정 (설정 presence + 자동 구멍 위치): 흙이 많이 보이는 식물 칸도 식물로 봐야 함."""
    from crop_monitor.modules.rose_growth.rose_pipeline.presence import build_presence

    cl, cr, plants = PLANTS[name]
    det = build_presence(cfg["presence"], cfg["layout"])
    scores = {}
    for cam, f in (("CL", cl), ("CR", cr)):
        img = read_image(f"data/samples/{f}.jpg")
        holes = camera_holes(finder.find(img, prior=cfg["holes"][cam]).holes, img.shape, cfg.get("radius_ratio", 0.3))
        for cid, h in holes.items():
            scores.setdefault(cid, []).append(det.score(img, h))
    got = {cid for cid, s in scores.items() if det.is_present(cid, s)}
    assert got == plants, {cid: [round(v, 2) for v in s] for cid, s in scores.items()}


def test_pipeline_uses_auto_holes_and_falls_back(cfg):
    """holes_for: auto 성공 → 자동 위치 / 사진이 엉뚱하면(전부 잎색) → holes.yaml 로 대신."""
    import copy

    import numpy as np

    from crop_monitor.modules.rose_growth.rose_pipeline.pipeline import RosePipeline

    p = RosePipeline.__new__(RosePipeline)
    p.cfg, p.layout, p.hole_fits = copy.deepcopy(cfg), cfg["layout"], {}
    img = read_image("data/samples/CL1.jpg")
    holes = p.holes_for("CL", img)
    assert p.hole_fits["CL"]["used"] == "auto" and len(holes) == 12
    green = np.zeros((960, 1280, 3), np.uint8)
    green[:] = (40, 200, 60)                  # 채도 높은 초록뿐 → 트레이를 찾을 수 없음
    p.holes_for("CR", green)
    assert p.hole_fits["CR"]["used"] == "fixed" and not p.hole_fits["CR"]["ok"]
