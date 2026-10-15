"""Stage 1 + stage 2 for one photo.

result = LeafPipeline(models_dir).run(rgb) ->
  dict(L=<estimated leaf length px>, size=[W, H], counts={state: n},
       leaves=[dict(id, box=[x0, y0, x1, y1], polygon=[[x, y], ...], area, conf, solidity, touches_border,
                    sharpness, dark, L, state, anomaly_class, probs={class: p})])
States: FRESH | ANOMALY (see anomaly_class) | UNKNOWN | HOLD (quality) | HOLD_SHAPE (merged / cut by the frame)
"""
from . import _compat  # noqa: F401
import json
import time
from pathlib import Path
import numpy as np
import cv2
from PIL import Image, ImageOps
from .stage1 import LeafSegmenter
from .stage2 import LeafClassifier, gray_world, CLASSES

DEFAULT_CONFIG = dict(seg_weights="leaf_seg_v8r2.pt", cls_weights="leaf_cls_v8.pt", states="states.json",
                      conf=0.35, tile_mult=8.0, imgsz=640, est_imgsz=1280, min_area=400, dedup_iou=0.5,
                      box_scale=1.25, min_solidity=0.8, device=None)


def load_rgb(path):
    return np.asarray(ImageOps.exif_transpose(Image.open(path)).convert("RGB"))


def _simple_gray_world(img):
    f = img.astype(np.float32); m = f.reshape(-1, 3).mean(0); f *= m.mean() / np.maximum(m, 1)
    return np.clip(f, 0, 255).astype(np.uint8)


def _dark_share(sub, m):
    rgb = sub[m].astype(np.float32) / 255.0
    return float((rgb.max(1) < 0.2).mean())


class LeafPipeline:
    def __init__(self, models_dir="models", config=None):
        d = Path(models_dir)
        cfg = dict(DEFAULT_CONFIG)
        if (d / "config.json").exists():
            cfg.update(json.loads((d / "config.json").read_text()))
        if (d.parent / "config.json").exists():
            cfg.update(json.loads((d.parent / "config.json").read_text()))
        cfg.update(config or {})
        self.cfg = cfg
        self.seg = LeafSegmenter(d / cfg["seg_weights"], conf=cfg["conf"], tile_mult=cfg["tile_mult"], imgsz=cfg["imgsz"],
                                 est_imgsz=cfg["est_imgsz"], min_area=cfg["min_area"], dedup_iou=cfg["dedup_iou"], device=cfg["device"])
        self.cls = LeafClassifier(d / cfg["cls_weights"], d / cfg["states"], box_scale=cfg["box_scale"],
                                  min_solidity=cfg["min_solidity"], device=cfg["device"])

    def run(self, rgb, L=None):
        t0 = time.time()
        H, W = rgb.shape[:2]
        found, L = self.seg(rgb, L)
        t1 = time.time()
        found.sort(key=lambda x: ((x["box"][1] + x["box"][3]) // 2 // 150, (x["box"][0] + x["box"][2]) // 2))  # reading order
        im = _simple_gray_world(rgb)
        lap = cv2.Laplacian(cv2.cvtColor(im, cv2.COLOR_RGB2GRAY), cv2.CV_32F)
        leaves = []
        for i, f in enumerate(found, 1):
            x0, y0, x1, y1 = f["box"]; m = f["mask"]
            area = int(m.sum())
            cs, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            c = max(cs, key=cv2.contourArea)
            poly = (cv2.approxPolyDP(c, 1.0, True).reshape(-1, 2) + [x0, y0]).tolist()
            leaves.append(dict(id=f"L{i:04d}", box=[int(x0), int(y0), int(x1), int(y1)], polygon=poly, area=area, conf=round(f["conf"], 4),
                               solidity=round(float(area / max(1.0, cv2.contourArea(cv2.convexHull(c)))), 3),
                               touches_border=bool(x0 <= 2 or y0 <= 2 or x1 >= W - 2 or y1 >= H - 2),
                               sharpness=round(float(lap[y0:y1, x0:x1][m].var()), 1), dark=round(_dark_share(im[y0:y1, x0:x1], m), 3),
                               L=int(max(x1 - x0, y1 - y0)), _mask=m))
        P, _ = self.cls.probs(gray_world(rgb), leaves)
        counts = {}
        for l, p in zip(leaves, P):
            st, ac = self.cls.state(l, p)
            l.update(state=st, anomaly_class=ac, probs={c: round(float(v), 4) for c, v in zip(CLASSES, p)})
            key = st if st != "ANOMALY" else f"ANOMALY:{ac}"
            counts[key] = counts.get(key, 0) + 1
        return dict(L=round(L, 1), size=[W, H], counts=counts, leaves=leaves,
                    timing=dict(stage1_s=round(t1 - t0, 2), stage2_s=round(time.time() - t1, 2)))

    @staticmethod
    def to_json(result):
        """JSON-serialisable copy (drops the per-leaf mask arrays)."""
        return dict({k: v for k, v in result.items() if k != "leaves"},
                    leaves=[{k: v for k, v in l.items() if not k.startswith("_")} for l in result["leaves"]])
