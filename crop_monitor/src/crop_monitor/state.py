"""LangGraph 상태 정의."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

import numpy as np

from crop_monitor.modules.height.height import HeightResult


class PipelineState(TypedDict, total=False):
    # ── 입력 ──
    crop_id: str
    images: dict[str, str]          # {카메라: 이미지 경로}  예: {"CL": ".../CL.jpg", "CR": ".../CR.jpg"}
    out_dir: str | None             # 지정하면 모듈별 시각화 결과 저장

    # ── 전처리 ──
    frames: dict[str, np.ndarray]   # {카메라: BGR 원본 이미지}

    # ── 병렬 모듈 결과 (실패하면 None + warnings) ──
    growth: dict[str, Any] | None   # modules/<growth module>/adapter.py
    disease: dict[str, Any] | None  # modules/<disease module>/adapter.py
    height: HeightResult | None     # modules/height/height.py

    # ── 병합 결과 ──
    crop_data: dict[str, Any]
    report: str

    # 여러 노드가 동시에 경고를 추가할 수 있도록 리스트를 합침
    warnings: Annotated[list[str], operator.add]
