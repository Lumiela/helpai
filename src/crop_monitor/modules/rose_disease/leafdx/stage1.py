"""Stage 1: find every leaf (1-class YOLO-seg, tiled inference at a leaf-size-dependent scale).

Same algorithm as v8_reallabel/scripts/leaf_search_v8.py (run with --no-bg-filter):
  1. leaf length L = median box long side of a first pass on the whole photo (imgsz 1280), fallback 90 px
  2. square tiles of side tile_mult x L, 25 % overlap, each predicted at imgsz 640
  3. masks touching an inner tile edge are dropped (another tile sees them whole); masks < 20 px dropped
  4. duplicates across tiles merged: higher confidence wins when mask IoU > dedup_iou
  5. masks smaller than min_area dropped
"""
from . import _compat  # noqa: F401
import numpy as np
import cv2
from ultralytics import YOLO


class LeafSegmenter:
    def __init__(self, weights, conf=0.35, tile_mult=8.0, imgsz=640, est_imgsz=1280, min_area=400, dedup_iou=0.5, device=None):
        self.model = YOLO(str(weights))
        self.conf, self.tile_mult, self.imgsz, self.est_imgsz = conf, tile_mult, imgsz, est_imgsz
        self.min_area, self.dedup_iou, self.device = min_area, dedup_iou, device

    def _predict(self, bgr, imgsz, retina=False):
        kw = dict(imgsz=imgsz, conf=self.conf, verbose=False, retina_masks=retina)
        if self.device is not None:
            kw["device"] = self.device
        return self.model.predict(np.ascontiguousarray(bgr), **kw)[0]

    def estimate_leaf_length(self, rgb):
        r = self._predict(rgb[:, :, ::-1], self.est_imgsz)
        if r.boxes is None or len(r.boxes) < 5:
            return 90.0
        b = r.boxes.xyxy.cpu().numpy()
        return float(np.median(np.maximum(b[:, 2] - b[:, 0], b[:, 3] - b[:, 1])))

    def __call__(self, rgb, L=None):
        """rgb HxWx3 uint8 -> (leaves, L). leaf = dict(conf, box=(x0, y0, x1, y1) exclusive, mask=bool array of the box)."""
        H, W = rgb.shape[:2]
        L = float(L) if L else self.estimate_leaf_length(rgb)
        R = int(min(max(H, W), max(320, self.tile_mult * L))); st = max(1, int(R * 0.75))
        xs = sorted(set(list(range(0, max(1, W - R), st)) + [max(0, W - R)]))
        ys = sorted(set(list(range(0, max(1, H - R), st)) + [max(0, H - R)]))
        preds = []
        for ty in ys:
            for tx in xs:
                x1, y1 = min(W, tx + R), min(H, ty + R)
                r = self._predict(rgb[ty:y1, tx:x1, ::-1], self.imgsz, retina=True)
                if r.masks is None:
                    continue
                for m, c in zip(r.masks.data.cpu().numpy() > 0.5, r.boxes.conf.cpu().numpy()):
                    if m.shape != (y1 - ty, x1 - tx):
                        m = cv2.resize(m.astype(np.uint8), (x1 - tx, y1 - ty), interpolation=cv2.INTER_NEAREST).astype(bool)
                    yy, xx = np.nonzero(m)
                    if len(xx) < 20:
                        continue
                    e = 2
                    if (xx.min() <= e and tx > 0) or (yy.min() <= e and ty > 0) or (xx.max() >= x1 - tx - 1 - e and x1 < W) or (yy.max() >= y1 - ty - 1 - e and y1 < H):
                        continue
                    b = (tx + int(xx.min()), ty + int(yy.min()), tx + int(xx.max()) + 1, ty + int(yy.max()) + 1)
                    preds.append((float(c), b, m[b[1] - ty:b[3] - ty, b[0] - tx:b[2] - tx].copy()))
        preds.sort(key=lambda t: -t[0])
        keep = []
        for c, b, m in preds:
            dup = False
            for _, b2, m2 in keep:
                ix0, iy0, ix1, iy1 = max(b[0], b2[0]), max(b[1], b2[1]), min(b[2], b2[2]), min(b[3], b2[3])
                if ix0 >= ix1 or iy0 >= iy1:
                    continue
                inter = (m[iy0 - b[1]:iy1 - b[1], ix0 - b[0]:ix1 - b[0]] & m2[iy0 - b2[1]:iy1 - b2[1], ix0 - b2[0]:ix1 - b2[0]]).sum()
                if inter and inter / (m.sum() + m2.sum() - inter) > self.dedup_iou:
                    dup = True; break
            if not dup:
                keep.append((c, b, m))
        leaves = [dict(conf=c, box=b, mask=m) for c, b, m in keep if m.sum() >= self.min_area]
        return leaves, L
