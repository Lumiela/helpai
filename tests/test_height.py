import numpy as np
import pytest

from crop_monitor.modules.height import height as hm
from crop_monitor.nodes.preprocess import read_image
from crop_monitor.registry import height_config


@pytest.fixture(scope="module")
def cube():
    """스마트큐브 예시 쌍: 렌즈→체커보드 줄자 58.4cm, 원본 stereo_pair.py + stereo_calib.npz 결과 중앙 56.7cm."""
    L = read_image("data/samples/cube_CL.jpg")  # cube_L_C2 (스테레오 왼쪽)
    R = read_image("data/samples/cube_CR.jpg")  # cube_R_C1
    return hm.compute(L, R, height_config())


def test_center_distance_matches_original(cube):
    d = cube.depth_cm
    h, w = d.shape
    center = np.nanmedian(d[h // 2 - 10:h // 2 + 11, w // 2 - 10:w // 2 + 11])
    assert cube.info["align"]["mode"] == "calib"
    assert abs(center - 56.7) < 0.5


def test_flat_board_has_near_zero_height(cube):
    """평평한 체커보드만 찍힌 장면 → 기준면 대비 높이가 거의 0."""
    assert abs(np.nanmedian(cube.height_cm)) < 0.5
    assert cube.info["align"]["rect_dy_median_px"] < 2.0


def test_region_height_maps_original_coords(cube):
    W, H = cube.src_size
    mask = np.ones((200, 200), bool)
    r = hm.region_height(cube, mask, (W // 2 - 100, H // 2 - 100), height_config())
    assert r is not None and abs(r["median_cm"]) < 1.0
