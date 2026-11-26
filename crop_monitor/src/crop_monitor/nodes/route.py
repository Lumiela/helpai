"""작물별 모델 라우팅.

등록된 작물이면 생장 단계 / 이상 상태 / 높이를 병렬로 실행하고,
등록되지 않은 작물이면 unknown_crop 으로 보냅니다.
"""

from __future__ import annotations

from crop_monitor.registry import get_crop
from crop_monitor.state import PipelineState

PARALLEL_NODES = ["growth_stage", "disease", "height"]


def route_by_crop(state: PipelineState) -> list[str] | str:
    if get_crop(state.get("crop_id", "")) is None:
        return "unknown_crop"
    return PARALLEL_NODES
