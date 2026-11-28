"""테스트 공통 설정.

1) 보정 파일: 실제 config/stereo/stereo_calib.npz 는 운영 중 자동 미세 조정(B)으로 바뀔 수 있으므로,
   테스트는 처음 보정(2026-11-11 체커보드 33쌍)을 복사해 둔 tests/data/stereo_calib_ref.npz 를 쓴다.
   (큐브 샘플 56.7cm 등 기대값이 이 보정 기준)
2) 파이프라인 실행 중 자동 보정(height.autocalib.in_pipeline)은 보정 파일을 바꾸므로 기본으로 끈다.
   자동 보정을 시험하는 테스트는 임시 폴더의 보정 파일로 따로 켠다.
"""

import copy
import dataclasses
from pathlib import Path

import pytest

REF_CALIB = Path(__file__).parent / "data" / "stereo_calib_ref.npz"


@pytest.fixture(autouse=True, scope="session")
def _reference_calibration():
    """세션 전체(모듈 단위 fixture 포함)에서 기준 보정 사용."""
    from crop_monitor import registry

    real = registry._raw

    def raw():
        d = copy.deepcopy(real())
        d.setdefault("height", {})["calib"] = str(REF_CALIB)
        return d

    mp = pytest.MonkeyPatch()
    mp.setattr(registry, "_raw", raw)
    yield
    mp.undo()


@pytest.fixture(autouse=True)
def _no_autocalib_in_pipeline(monkeypatch):
    from crop_monitor.calibration import manager

    real = manager.load_config

    def off():
        return dataclasses.replace(real(), in_pipeline=False)

    monkeypatch.setattr(manager, "load_config", off)
    yield
