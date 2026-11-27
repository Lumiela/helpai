"""사진에서 구멍(화분) 위치를 자동으로 찾아 확인 그림을 저장하고, 원하면 holes.yaml 을 갱신.

    uv run python scripts/rose_growth/find_holes.py --CL data/samples/CL1.jpg --CR data/samples/CR1.jpg
    uv run python scripts/rose_growth/find_holes.py --CL ... --CR ... --write          # holes.yaml 갱신 (이전 파일은 백업)
    uv run python scripts/rose_growth/find_holes.py --from-api --write                  # 카메라 API 의 최신 사진 쌍으로

확인 그림: out/holes/<시각>/holes_<카메라>.jpg (빨강 = 트레이 테두리, 청록 = 구멍)
설정: config/rose/growth.yaml 의 holes_auto
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

import cv2

from crop_monitor.modules.rose_growth.rose_pipeline.config import load_config, resolve, save_yaml
from crop_monitor.modules.rose_growth.rose_pipeline.hole_finder import HoleFinder, draw_fit
from crop_monitor.nodes.preprocess import read_image


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/rose/growth.yaml")
    ap.add_argument("--CL", help="CL 카메라 사진")
    ap.add_argument("--CR", help="CR 카메라 사진")
    ap.add_argument("--from-api", action="store_true", help="카메라 API 에서 최신 사진 쌍을 받아 사용 (config/crops.yaml 의 camera_api)")
    ap.add_argument("--write", action="store_true", help="찾은 위치로 holes.yaml 갱신 (모든 카메라가 성공했을 때만)")
    ap.add_argument("--out", default="out/holes")
    a = ap.parse_args()

    cfg = load_config(a.config)
    if a.from_api:
        from crop_monitor.camera_api import latest_pairs
        pairs = latest_pairs(1)
        if not pairs:
            sys.exit("API 에서 동시 촬영 사진 쌍을 찾지 못했습니다")
        images = pairs[0]["images"]
        print("API 사진:", pairs[0]["taken_at"])
    else:
        paths = {k: v for k, v in (("CL", a.CL), ("CR", a.CR)) if v}
        if not paths:
            sys.exit("--CL/--CR 사진 또는 --from-api 가 필요합니다")
        images = {k: read_image(v) for k, v in paths.items()}

    auto = dict(cfg.get("holes_auto") or {})
    finder = HoleFinder(rows=cfg["layout"]["rows"], cols=cfg["layout"]["cols"], **auto)
    out = resolve(a.out) / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)

    found, all_ok = {}, True
    for cam, img in images.items():
        fit = finder.find(img, prior=cfg["holes"].get(cam))
        found[cam] = fit
        all_ok &= fit.ok
        vis = draw_fit(img, fit, title=cam)
        s = 1280 / vis.shape[1]
        cv2.imwrite(str(out / f"holes_{cam}.jpg"), cv2.resize(vis, None, fx=s, fy=s, interpolation=cv2.INTER_AREA))
        shift = "-" if fit.shift_ratio is None else f"{fit.shift_ratio:.2f}"
        print(f"{cam}: {'성공' if fit.ok else '실패'}  점수 {fit.score:.2f}  간격 {fit.info['pitch_px']}px  "
              f"회전 {fit.info['rotation_deg']}°  holes.yaml 대비 이동 {shift} 간격  {fit.reason}")
    print("확인 그림:", out)

    if a.write:
        if not all_ok:
            sys.exit("실패한 카메라가 있어 holes.yaml 을 바꾸지 않았습니다")
        path = resolve(cfg["holes_file"])
        holes = dict(cfg["holes"])
        holes.update({cam: fit.holes for cam, fit in found.items()})
        if path.exists():
            backup = path.with_name(path.stem + datetime.now().strftime("_%Y%m%d_%H%M%S") + path.suffix + ".bak")
            shutil.copy2(path, backup)
            print("이전 파일 백업:", backup)
        save_yaml(holes, path, header="# 카메라별 구멍 중심 좌표 (scripts/rose_growth/find_holes.py 가 생성)\n"
                                       "# 이미지 크기 대비 정규화 좌표 [x, y]\n")
        print("holes.yaml 갱신:", path)


if __name__ == "__main__":
    main()
