import json
from pathlib import Path

import cv2
import numpy as np

from .detection import square_box

# BGR. 등급이 높을수록 붉은색
GRADE_COLORS = {
    "grade_zero": (220, 220, 220),
    "grade_one": (0, 220, 255),
    "grade_two": (0, 160, 255),
    "grade_three": (0, 80, 255),
    "grade_four": (60, 0, 220),
}
# 봉오리 박스 라벨용 짧은 이름
GRADE_SHORT = {"grade_zero": "G0", "grade_one": "G1", "grade_two": "G2", "grade_three": "G3", "grade_four": "G4"}
# 식물 마스크 색 (칸마다 다르게)
PLANT_COLORS = [(255, 80, 80), (80, 200, 255), (255, 0, 255), (0, 255, 128), (255, 255, 0),
                (128, 128, 255), (0, 128, 255), (200, 255, 120), (255, 160, 200), (120, 60, 255),
                (60, 255, 255), (180, 120, 60)]


def imwrite(path, image):
    """한글 경로도 저장되도록 imencode 사용."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(path.suffix or ".jpg", image)
    if not ok:
        raise ValueError(f"이미지 인코딩 실패: {path}")
    buf.tofile(str(path))


def _thickness(image):
    return max(2, round(max(image.shape[:2]) / 600))


def _label(image, text, org, color, scale):
    t = _thickness(image)
    org = (int(org[0]), int(org[1]))
    cv2.putText(image, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), t + 3, cv2.LINE_AA)
    cv2.putText(image, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, t, cv2.LINE_AA)


def _plant_color(cell_id):
    r, c = (int(v) for v in cell_id[1:].split("C"))
    return PLANT_COLORS[((r - 1) * 4 + (c - 1)) % len(PLANT_COLORS)]


def draw_holes(image, holes, presence=None):
    """구멍 원 + 칸 이름. presence: {칸: True/False/None} (빨강=식물, 파랑=빈 구멍, 회색=판단 안 함)"""
    t = _thickness(image)
    scale = max(image.shape[:2]) / 1600
    for cid, h in holes.items():
        p = None if presence is None else presence.get(cid)
        color = (0, 0, 255) if p else (255, 140, 0) if p is False else (160, 160, 160)
        c = (int(h.center[0]), int(h.center[1]))
        cv2.circle(image, c, int(h.radius), color, t)
        _label(image, cid, (c[0] - 45 * scale, c[1] - h.radius - 10 * scale), color, scale)
    return image


def draw_frame(frame, cell_results):
    """카메라 하나의 결과: 식물 마스크, 구멍(유무), 봉오리, 칸별 등급."""
    image = frame.image.copy()
    overlay = image.copy()
    for p in frame.plants:
        if p.mask.size:
            x, y = p.offset
            h, w = p.mask.shape
            overlay[y:y + h, x:x + w][p.mask] = _plant_color(p.cell_id)
    image = cv2.addWeighted(overlay, 0.4, image, 0.6, 0)
    for b in frame.buds:  # 봉오리는 식물 색을 입히지 않고 원래 색으로 보여 줌
        x1, y1, x2, y2 = np.maximum(b.xyxy.astype(int), 0)
        image[y1:y2, x1:x2] = frame.image[y1:y2, x1:x2]

    by_id = {r.cell_id: r for r in cell_results}
    covered = {cid for cid, r in by_id.items() if frame.name in r.cameras}
    draw_holes(image, frame.holes, {cid: by_id[cid].present if cid in covered else None
                                    for cid in frame.holes if cid in by_id})

    t = _thickness(image)
    scale = max(image.shape[:2]) / 1600
    for cid, h in frame.holes.items():
        r = by_id.get(cid)
        if r is None or cid not in covered:
            continue
        text = r.camera_grades.get(frame.name) if r.present else "empty"
        _label(image, text or "?", (h.center[0] - 60 * scale, h.center[1] + 10 * scale),
               GRADE_COLORS.get(text, (255, 200, 120)), scale * 0.8)

    for b in frame.buds:  # 칸 등급 글자에 가리지 않도록 마지막에 그림
        color = GRADE_COLORS.get(b.class_name, (255, 255, 255))
        x1, y1, x2, y2 = b.xyxy.astype(int)
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 0, 0), t + 3)
        cv2.rectangle(image, (x1, y1), (x2, y2), color, t + 1)
        if b.mask is not None:  # 혼합형: FastSAM 봉오리 마스크 윤곽
            contours, _ = cv2.findContours(b.mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(image, contours, -1, (255, 255, 255), max(1, t // 2), offset=b.mask_xy)
        tag = f"{GRADE_SHORT.get(b.class_name, b.class_name)} {b.conf:.2f} -> {b.cell_id or 'none'}"
        _label(image, tag, (x1, max(y1 - 5, 15)), color, scale * 0.6)
    return image


def draw_board(result, thumb=120, per_row=4, max_rows=2):
    """최종 결과판: 3×4 칸마다 최종 등급과 그 칸에 배정된 봉오리 사진 (높은 등급 먼저)."""
    gap, head, cap = 8, 44, 24
    cell_w = per_row * (thumb + gap) + gap
    cell_h = head + max_rows * (thumb + cap + gap) + gap
    order = list(GRADE_COLORS)
    board = np.full((result.rows * cell_h, result.cols * cell_w, 3), 40, np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX
    for c in result.cells:
        ox, oy = (c.col - 1) * cell_w, (c.row - 1) * cell_h
        cv2.rectangle(board, (ox, oy), (ox + cell_w - 1, oy + cell_h - 1), (120, 120, 120), 1)
        title = f"{c.cell_id}  {c.symbol if c.present else 'empty' if c.present is False else '?'}"
        cv2.putText(board, title, (ox + gap, oy + 30), font, 0.8, GRADE_COLORS.get(c.grade, (170, 170, 170)), 2,
                    cv2.LINE_AA)
        if not c.present:
            continue
        buds = sorted(c.buds, key=lambda cb: (-order.index(cb[1].class_name) if cb[1].class_name in order else 0,
                                              -cb[1].conf))
        if not buds:
            cv2.putText(board, "no bud", (ox + gap, oy + head + 30), font, 0.6, (170, 170, 170), 1, cv2.LINE_AA)
        shown = per_row * max_rows
        for i, (cam, b) in enumerate(buds[:shown]):
            image = result.frames[cam].image
            x1, y1, x2, y2 = square_box(b.xyxy, 0.3, image.shape[1], image.shape[0])
            x = ox + gap + (i % per_row) * (thumb + gap)
            y = oy + head + (i // per_row) * (thumb + cap + gap)
            color = GRADE_COLORS.get(b.class_name, (255, 255, 255))
            board[y:y + thumb, x:x + thumb] = cv2.resize(image[y1:y2, x1:x2], (thumb, thumb),
                                                         interpolation=cv2.INTER_AREA)
            cv2.rectangle(board, (x, y), (x + thumb - 1, y + thumb - 1), color, 3)
            cv2.putText(board, f"{GRADE_SHORT.get(b.class_name, b.class_name)} {b.conf:.2f} {cam}",
                        (x, y + thumb + 18), font, 0.5, color, 1, cv2.LINE_AA)
        if len(buds) > shown:
            cv2.putText(board, f"+{len(buds) - shown} more", (ox + cell_w - 110, oy + 30), font, 0.55,
                        (220, 220, 220), 1, cv2.LINE_AA)
    return board


def save_outputs(result, out_dir, save_visualization=True, save_masks=True):
    run_dir = Path(out_dir) / result.timestamp.replace(":", "").replace("-", "")
    run_dir.mkdir(parents=True, exist_ok=True)

    with open(run_dir / "result.json", "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
    with open(run_dir / "table.txt", "w", encoding="utf-8") as f:
        f.write(result.table() + "\n\n" + result.bud_table() + "\n")

    if save_visualization:
        imwrite(run_dir / "result_buds.jpg", draw_board(result))

    for name, frame in result.frames.items():
        if save_visualization:
            imwrite(run_dir / f"vis_{name}.jpg", draw_frame(frame, result.cells))
        if save_masks:
            for p in frame.plants:
                if p.mask.size:
                    full = np.zeros(frame.image.shape[:2], np.uint8)
                    x, y = p.offset
                    h, w = p.mask.shape
                    full[y:y + h, x:x + w][p.mask] = 255
                    imwrite(run_dir / "masks" / f"{name}_{p.cell_id}.png", full)
    return run_dir
