"""봉오리 → 식물 할당 규칙.

규칙을 바꾸거나 추가하려면 아래처럼 함수를 만들고 @register("이름") 을 붙인 뒤
설정의 grading.assignment 를 그 이름으로 바꾸면 된다.

규칙 함수: (detection, plants) -> 할당할 PlantSegment 또는 None(어느 식물에도 속하지 않음)
plants 에는 식물이 있는 칸(present=True)만 들어온다.
"""

ASSIGNERS = {}


def register(name):
    def deco(fn):
        ASSIGNERS[name] = fn
        return fn
    return deco


@register("center_in_mask")
def center_in_mask(det, plants):
    """봉오리 박스 중심점이 식물 마스크 안에 있으면 그 식물."""
    cx, cy = det.center
    for p in plants:
        if p.contains(cx, cy):
            return p
    return None


@register("max_overlap")
def max_overlap(det, plants):
    """봉오리 박스 안에 식물 마스크 픽셀이 가장 많이 들어오는 식물."""
    best, best_px = None, 0
    for p in plants:
        px = p.overlap(det.xyxy)
        if px > best_px:
            best, best_px = p, px
    return best


@register("mask_overlap")
def mask_overlap(det, plants):
    """봉오리 마스크(혼합형 검출기)와 픽셀이 가장 많이 겹치는 식물.
    마스크가 없거나 어느 식물과도 겹치지 않으면 max_overlap(박스 기준)으로 판단."""
    if det.mask is None:
        return max_overlap(det, plants)
    best, best_px = None, 0
    for p in plants:
        px = p.overlap_mask(det.mask, det.mask_xy)
        if px > best_px:
            best, best_px = p, px
    return best if best is not None else max_overlap(det, plants)


def get_assigner(name):
    if name not in ASSIGNERS:
        raise ValueError(f"알 수 없는 grading.assignment: {name} (가능: {list(ASSIGNERS)})")
    return ASSIGNERS[name]
