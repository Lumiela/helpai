"""보정 관리 테스트: 기하, B(자동 미세 조정), 이력 저장소, 상태 판정, A(체커보드 — 합성 사진), 파이프라인 연결.

실제 보정 파일(config/stereo)은 건드리지 않고 모두 임시 폴더에 복사해서 시험한다.
B 시험은 큐브 샘플(cube_CL = 스테레오 왼쪽)에 일부러 회전을 넣어 틀어지게 한 뒤 되돌아오는지 본다.
"""

import dataclasses
import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest

from crop_monitor.calibration import checkerboard, geometry as geo, manager, refine as rf
from crop_monitor.calibration.store import CalibStore
from crop_monitor.nodes.preprocess import read_image
from crop_monitor.registry import height_config, resolve

CALIB = Path(__file__).parent / "data" / "stereo_calib_ref.npz"   # 처음 보정 (운영 중 바뀌는 실제 파일 대신)


@pytest.fixture(scope="module")
def calib():
    return geo.load(CALIB)


@pytest.fixture(scope="module")
def cube():
    return read_image("data/samples/cube_CL.jpg"), read_image("data/samples/cube_CR.jpg")


@pytest.fixture
def tmp_store(tmp_path):
    cur = tmp_path / "stereo_calib.npz"
    shutil.copy2(CALIB, cur)
    store = CalibStore(cur, tmp_path / "history")
    store.ensure_initial()
    return store


def test_pose_roundtrip(calib):
    R, T = geo.pose(calib)
    assert abs(np.linalg.norm(T) - 33.31) < 0.05            # 보고서의 간격
    assert abs(geo.rot_deg(R) - 3.643) < 0.01               # 보고서의 회전
    c2 = geo.rectify(calib, R, T)
    for k in ("R1", "R2", "P1", "P2", "Q"):
        assert np.allclose(c2[k], calib[k], atol=1e-6)


def test_refine_fixes_pitch_roll_without_changing_distance(calib, cube):
    """끄덕임·기울어짐(세로 어긋남으로 보이는 회전)을 넣으면 B 가 되돌리고, 거리는 그대로."""
    hc = height_config()
    R, T = rf.rotate_right(calib, wx=np.radians(0.6), wz=np.radians(-0.5))
    bad = geo.rectify(calib, R, T)
    r = rf.refine(bad, [cube])
    assert r.ok, r.reason
    assert r.before["dy_median"] > 3 and r.after["dy_median"] < 1.0
    R1, _ = geo.pose(r.calib)
    R0, _ = geo.pose(calib)
    assert geo.rot_deg(R1 @ R0.T) < 0.25
    d0, d1 = rf.tray_distance_cm(calib, cube, hc), rf.tray_distance_cm(r.calib, cube, hc)
    assert abs(d1 - d0) < 1.0


def test_refine_leaves_good_calibration_alone(calib, cube):
    r = rf.refine(calib, [cube])
    assert r.ok and r.rotation_change_deg < 0.3


@pytest.mark.slow
def test_refine_with_reference_fixes_yaw(calib, cube):
    """좌우 돌아감(yaw)까지 넣으면 거리 기준이 있어야 되돌릴 수 있음 (height.compute 를 몇 번 돌려 20초 안팎)."""
    hc = height_config()
    ref = rf.tray_distance_cm(calib, cube, hc)
    R, T = rf.rotate_right(calib, wx=np.radians(0.6), wy=np.radians(0.5), wz=np.radians(-0.4))
    bad = geo.rectify(calib, R, T)
    r = rf.refine(bad, [cube], reference_cm=ref, height_cfg=hc)
    assert r.ok, r.reason
    assert abs(r.distance["after_cm"] - ref) < 0.5
    R1, _ = geo.pose(r.calib)
    R0, _ = geo.pose(calib)
    assert geo.rot_deg(R1 @ R0.T) < 0.25


def test_store_save_activate_rollback(tmp_store, calib):
    first = tmp_store.current_id()
    assert first.endswith("_initial")
    R, T = rf.rotate_right(calib, wx=np.radians(0.3))
    new = geo.rectify(calib, R, T)
    cid = tmp_store.save(new, dict(source="B", note="시험"))
    assert tmp_store.current_id() == cid
    assert np.allclose(geo.load(tmp_store.current)["R2"], new["R2"])
    assert tmp_store.meta(cid)["parent"] == first
    assert tmp_store.rollback() == first
    assert np.allclose(geo.load(tmp_store.current)["R2"], calib["R2"])


def test_health_detects_swapped_cameras(calib):
    L, R = read_image("data/samples/rose_CR.jpg"), read_image("data/samples/rose_CL.jpg")   # 바른 순서 (CR = 왼쪽)
    cfg = manager.AutoCalibConfig()
    good = manager.health(calib, [(L, R)])
    swapped = manager.health(calib, [(R, L)])
    assert manager.classify(good, cfg) == "drift"            # 4.5px: B 로 고칠 수 있는 정도
    assert manager.classify(swapped, cfg) == "swapped"


def test_maintain_saves_new_version(tmp_store, calib, cube):
    """틀어진 보정을 넣고 maintain → B 성공 → 새 버전 저장·교체 → 이력에 parent 연결."""
    R, T = rf.rotate_right(calib, wx=np.radians(0.6), wz=np.radians(-0.5))
    bad_id = tmp_store.save(geo.rectify(calib, R, T), dict(source="test", note="일부러 틀어지게"))
    hc = dataclasses.replace(height_config(), calib=tmp_store.current)
    cfg = manager.AutoCalibConfig(history_dir=str(tmp_store.dir))
    r = manager.maintain([cube], hc, cfg)
    assert r.action == "refined", r.message
    assert tmp_store.current_id() == r.saved_id
    m = tmp_store.meta()
    assert m["source"] == "B" and m["parent"] == bad_id and m["rect_dy_median_px"] < 1.0


def test_pipeline_height_node_autocalibrates(tmp_store, calib, cube, monkeypatch):
    """높이 노드: 세로 어긋남 경고 → 같은 사진 쌍으로 B → 높이 다시 계산 (임시 보정 파일로)."""
    from crop_monitor.nodes import modules

    R, T = rf.rotate_right(calib, wx=np.radians(0.6), wz=np.radians(-0.5))
    tmp_store.save(geo.rectify(calib, R, T), dict(source="test"))
    hc = dataclasses.replace(height_config(), calib=tmp_store.current)
    monkeypatch.setattr(modules, "height_config", lambda: hc)
    monkeypatch.setattr(modules, "stereo_pair", lambda: ("L", "R"))
    monkeypatch.setattr(manager, "load_config",
                        lambda: manager.AutoCalibConfig(history_dir=str(tmp_store.dir), in_pipeline=True))
    out = modules.height({"frames": {"L": cube[0], "R": cube[1]}})
    assert out["height"] is not None
    assert any("자동 미세 조정(B) 적용" in w for w in out["warnings"]), out["warnings"]
    assert out["height"].info["align"]["rect_dy_median_px"] < 1.5
    assert tmp_store.meta()["source"] == "B"


# ── A: 체커보드 (합성 사진) ───────────────────────────────────
def _render_pairs(out: Path, n=14, square=24.2, board=(9, 6), size=(640, 480), seed=0):
    """핀홀 카메라 2대(간격 33.3mm)로 체커보드를 여러 자세에서 찍은 합성 사진 L_NN / R_NN."""
    rng = np.random.default_rng(seed)
    K = np.array([[600.0, 0, 320], [0, 600.0, 240], [0, 0, 1]])
    Rlr = cv2.Rodrigues(np.array([0.01, 0.03, 0.02]))[0]
    Tlr = np.array([-33.3, 0.4, 0.8])
    ppm = 4.0                                                    # 텍스처: mm 당 4px
    nx, ny = board[0] + 3, board[1] + 3                          # 흰 테두리 포함 칸 수
    tex = np.full((int(ny * square * ppm), int(nx * square * ppm)), 255, np.uint8)
    for j in range(-1, board[1]):
        for i in range(-1, board[0]):
            if (i + j) % 2 == 0:
                x0, y0 = int((i + 2) * square * ppm), int((j + 2) * square * ppm)
                tex[y0:y0 + int(square * ppm), x0:x0 + int(square * ppm)] = 0
    S = np.array([[1 / ppm, 0, -2 * square], [0, 1 / ppm, -2 * square], [0, 0, 1]])   # 텍스처 px → 보드 mm
    out.mkdir(parents=True, exist_ok=True)
    k = 0
    while k < n:
        rb = cv2.Rodrigues(np.radians(rng.uniform([-25, -25, -15], [25, 25, 15])))[0]
        tb = np.array([rng.uniform(-120, 0), rng.uniform(-90, 0), rng.uniform(330, 480)])
        imgs = []
        for Rc, Tc in ((np.eye(3), np.zeros(3)), (Rlr, Tlr)):
            Rw, tw = Rc @ rb, Rc @ tb + Tc
            H = K @ np.column_stack([Rw[:, 0], Rw[:, 1], tw]) @ S
            imgs.append(cv2.warpPerspective(tex, H, size, borderValue=128))
        if all(cv2.findChessboardCorners(im, board)[0] for im in imgs):
            k += 1
            cv2.imwrite(str(out / f"L_{k:02d}.jpg"), imgs[0], [cv2.IMWRITE_JPEG_QUALITY, 95])
            cv2.imwrite(str(out / f"R_{k:02d}.jpg"), imgs[1], [cv2.IMWRITE_JPEG_QUALITY, 95])


def test_checkerboard_synthetic(tmp_store, tmp_path):
    pairs = tmp_path / "pairs"
    _render_pairs(pairs)
    r = checkerboard.run(tmp_store, 24.2, "9x6", 640, pairs_dir=str(pairs), min_pairs=6)
    assert r.ok, r.reason + "\n" + r.log[-1500:]
    assert abs(r.report["baseline_mm"] - 33.3) < 0.7
    assert r.report["rect_dy_median_px"] < 0.5
    m = tmp_store.meta()
    assert m["source"] == "A" and tmp_store.current_id() == r.saved_id


def test_checkerboard_rejects_too_few_pairs(tmp_store, tmp_path):
    pairs = tmp_path / "few"
    _render_pairs(pairs, n=3)
    r = checkerboard.run(tmp_store, 24.2, pairs_dir=str(pairs), min_pairs=6)
    assert not r.ok and "3개" in r.reason
    assert tmp_store.current_id().endswith("_initial")
