"""여러 카메라에서 나온 한 식물의 등급을 하나로 합치는 규칙.

규칙 함수: (camera_grades: {카메라: 등급}, grades: 낮은→높은 등급 목록) -> 최종 등급
새 규칙은 @register("이름") 으로 추가하고 설정의 grading.fusion 을 바꾼다.
"""

FUSERS = {}


def register(name):
    def deco(fn):
        FUSERS[name] = fn
        return fn
    return deco


@register("max")
def fuse_max(camera_grades, grades):
    """카메라별 등급 중 가장 높은 것."""
    return max(camera_grades.values(), key=grades.index)


def get_fuser(name):
    if name not in FUSERS:
        raise ValueError(f"알 수 없는 grading.fusion: {name} (가능: {list(FUSERS)})")
    return FUSERS[name]
