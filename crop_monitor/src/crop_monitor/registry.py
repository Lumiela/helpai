"""설정 로드: config/crops.yaml (카메라, 높이 모듈, 작물 레지스트리)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from crop_monitor.modules.height.height import HeightConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "crops.yaml"
CONFIG_ENV = "CROP_MONITOR_CONFIG"  # 다른 설정 파일을 쓰려면 이 환경 변수로 경로 지정


def config_path() -> Path:
    return Path(os.environ.get(CONFIG_ENV, DEFAULT_CONFIG))


def resolve(p: str | Path) -> Path:
    path = Path(p)
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True)
class CropConfig:
    crop_id: str
    name: str
    growth: dict[str, Any] | None
    disease: dict[str, Any] | None
    rules: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)


@lru_cache(maxsize=None)
def _load(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _raw() -> dict[str, Any]:
    return _load(str(config_path()))


def cameras() -> list[str]:
    """입력 이미지 이름 목록."""
    return list((_raw().get("cameras") or {}).get("names", ["CL", "CR"]))


def stereo_pair() -> tuple[str, str]:
    """(스테레오 왼쪽, 오른쪽) 이미지 이름 — stereo_calib.npz 의 좌우 기준."""
    c = _raw().get("cameras") or {}
    return c.get("stereo_left", "CR"), c.get("stereo_right", "CL")


def load_crops() -> dict[str, CropConfig]:
    crops: dict[str, CropConfig] = {}
    for crop_id, c in (_raw().get("crops") or {}).items():
        rules = _load(str(resolve(c["rules"]))) if c.get("rules") and resolve(c["rules"]).exists() else {}
        crops[str(crop_id)] = CropConfig(str(crop_id), c.get("name", str(crop_id)),
                                         c.get("growth"), c.get("disease"), rules)
    return crops


def get_crop(crop_id: str) -> CropConfig | None:
    return load_crops().get(crop_id)


def height_config() -> HeightConfig:
    h = dict(_raw().get("height") or {})
    h.pop("autocalib", None)  # 보정 관리 설정은 calibration.manager.load_config() 가 읽음
    if h.get("calib"):
        h["calib"] = resolve(h["calib"])
    return HeightConfig(**h)


def clear_caches() -> None:
    _load.cache_clear()
