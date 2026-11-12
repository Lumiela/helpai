"""트레이 배치: 구멍(행·열), 카메라 범위표, 카메라별 구멍 위치."""

import re
from dataclasses import dataclass

import numpy as np

HOLES_HEADER = (
    "# 카메라별 구멍 중심 좌표 (보정 도구가 생성)\n"
    "# 이미지 크기 대비 정규화 좌표 [x, y]. 사진 가장자리 밖 구멍은 0~1 범위를 벗어나도 됨.\n"
)


@dataclass
class Hole:
    cell_id: str
    row: int
    col: int
    center: np.ndarray  # (2,) float, 카메라 이미지 픽셀 좌표
    radius: float       # px
    spacing: float      # 가장 가까운 다른 구멍까지 거리(px)


def cell_id(row, col):
    return f"R{row}C{col}"


def parse_cell_id(cid):
    m = re.fullmatch(r"R(\d+)C(\d+)", cid)
    if not m:
        raise ValueError(f"칸 이름 형식이 잘못됐습니다: {cid} (예: R1C2)")
    return int(m[1]), int(m[2])


def all_cells(layout):
    return [cell_id(r, c) for r in range(1, layout["rows"] + 1) for c in range(1, layout["cols"] + 1)]


def coverage_map(layout):
    """{칸: [그 칸을 판단에 쓰는 카메라들]}"""
    cov = layout["coverage"]
    if len(cov) != layout["rows"] or any(len(r) != layout["cols"] for r in cov):
        raise ValueError(f"layout.coverage 는 {layout['rows']}행 × {layout['cols']}열이어야 합니다")
    return {cell_id(r + 1, c + 1): [s.strip() for s in str(v).split(",") if s.strip()]
            for r, row in enumerate(cov) for c, v in enumerate(row)}


def camera_holes(holes_cfg, image_shape, radius_ratio):
    """카메라 하나의 구멍 목록 {칸: Hole}. holes_cfg 는 {칸: [x, y] 정규화 좌표}."""
    h, w = image_shape[:2]
    centers = {cid: np.float32(xy) * np.float32([w, h]) for cid, xy in (holes_cfg or {}).items()}
    holes = {}
    for cid, c in centers.items():
        others = [np.linalg.norm(c - o) for k, o in centers.items() if k != cid]
        spacing = float(min(others)) if others else 0.25 * max(h, w)
        r, col = parse_cell_id(cid)
        holes[cid] = Hole(cid, r, col, c, radius_ratio * spacing, spacing)
    return holes


def circle_mask(shape, center, radius):
    yy, xx = np.ogrid[:shape[0], :shape[1]]
    return (xx - center[0]) ** 2 + (yy - center[1]) ** 2 <= radius ** 2
