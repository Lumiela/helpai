"""현재 holes.yaml 의 구멍 위치와 빈 구멍 점수를 이미지에 그려 output/holes_preview_<카메라>.jpg 로 저장.

    python tools/preview_holes.py
"""

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rose_pipeline.config import load_config, resolve  # noqa: E402
from rose_pipeline.layout import camera_holes  # noqa: E402
from rose_pipeline.presence import build_presence  # noqa: E402
from rose_pipeline.sources import build_source  # noqa: E402
from rose_pipeline.visualize import _label, draw_holes, imwrite  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/pipeline.yaml")
    args = ap.parse_args()

    cfg = load_config(args.config)
    presence = build_presence(cfg["presence"], cfg["layout"])
    out_dir = resolve(cfg["output"]["dir"])
    for name, cam in cfg["cameras"].items():
        image = build_source(cam["source"]).read()
        holes = camera_holes(cfg["holes"].get(name), image.shape, cfg.get("radius_ratio", 0.3))
        scores = {cid: presence.score(image, h) for cid, h in holes.items()}
        # presence.method 가 fixed 여도 여기서는 사진 판단 결과를 보여줌 (기준값 조정용)
        judged = {cid: None if np.isnan(s) else bool(s < presence.empty_ratio) for cid, s in scores.items()}
        vis = draw_holes(image.copy(), holes, judged)
        scale = max(image.shape[:2]) / 1600
        for cid, h in holes.items():
            _label(vis, f"empty {scores[cid]:.2f}", (h.center[0] - 70 * scale, h.center[1] + 10 * scale),
                   (255, 255, 255), scale * 0.7)
        path = out_dir / f"holes_preview_{name}.jpg"
        imwrite(path, vis)
        print(f"[{name}] {path}")
        for cid, s in scores.items():
            verdict = {True: "식물", False: "빈 구멍", None: "사진 밖"}[judged[cid]]
            print(f"  {cid}: 빈 구멍 점수 {s:.3f} → 사진 판단: {verdict}")


if __name__ == "__main__":
    main()
