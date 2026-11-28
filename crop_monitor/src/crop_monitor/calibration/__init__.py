"""스테레오 보정 관리: A(체커보드 재보정) + B(평소 사진으로 자동 미세 조정) + 보정 이력.

    geometry   : 보정 파일 ↔ 두 카메라 상대 자세(R, T), 평행화, 특징점 매칭, 세로 어긋남
    refine     : B — 평소 사진 쌍에서 두 카메라 상대 자세만 다시 맞춤 (렌즈 특성 K·D 와 간격은 A 값 유지)
    checkerboard: A — 체커보드 사진(API 또는 폴더)으로 전체 재보정 (scripts/height 의 원본 도구 사용)
    store      : 보정 이력 (config/stereo/history) · 현재 보정 교체 · 되돌리기
    manager    : 상태 확인 → 필요하면 B → 실패하면 A 요청
"""
