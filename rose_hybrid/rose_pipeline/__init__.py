"""미니 장미 생장 단계 판단 파이프라인.

1단계 (각각의 미니 장미 구분): 카메라 이미지 → 칸(R행C열)별 식물 세그멘테이션
2단계 (생장 단계 판단): 꽃봉오리 검출 → 식물별 최고 등급
"""

from .pipeline import RosePipeline, CellResult, PipelineResult

__all__ = ["RosePipeline", "CellResult", "PipelineResult"]
