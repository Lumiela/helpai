"""Stage 2: per-leaf state with the YOLOv8m-cls classifier (rose_leaf_cls, trained on Colab 2026-11-02).

Classifier input (same layout as its training set rose_leaf_canvas_cls, measured from the val_batch images): the leaf cut
out by its stage-1 mask, long side scaled to canvas_ratio x 224, centred on a white 224 x 224 canvas.
Classes come from the weights: black_spot, downy_mildew, healthy, insects_infected.
States (thresholds from states.json):
  HOLD_SHAPE  solidity < min_solidity or the leaf touches the photo border (several leaves merged / cut)
  HOLD        quality gate: sharpness < s_min or dark share > d_max or leaf long side < L_min
  ANOMALY     max(p of the non-healthy classes) >= t_anom  (class = the largest of them)
  FRESH       p_healthy >= t_fresh
  UNKNOWN     otherwise (abnormal-looking but unsure -> watch, not an alarm)
"""
from . import _compat  # noqa: F401
import json
import numpy as np
from PIL import Image
from ultralytics import YOLO

HEALTHY = "healthy"
WHITE = (255, 255, 255)


def gray_world(img):
    """White balance from low-saturation mid-brightness pixels (identical to v7 cls_common.gray_world)."""
    hsv_s = (img.max(-1).astype(np.int16) - img.min(-1)) / np.maximum(img.max(-1), 1)
    v = img.max(-1)
    ref = (hsv_s < 60 / 255) & (v > 60) & (v < 240)
    if ref.sum() < 1000:
        ref = np.ones(ref.shape, bool)
    mean = img[ref].reshape(-1, 3).mean(0)
    return np.clip(img.astype(np.float32) * (mean.mean() / mean), 0, 255).astype(np.uint8)


def to_canvas(img, leaf, size=224, ratio=0.6):
    """Leaf cut out by its mask, centred on a white square canvas."""
    x0, y0, x1, y1 = leaf["box"]
    cut = Image.fromarray(np.dstack([img[y0:y1, x0:x1], leaf["_mask"].astype(np.uint8) * 255]))
    s = size * ratio / max(x1 - x0, y1 - y0)
    w, h = max(1, round((x1 - x0) * s)), max(1, round((y1 - y0) * s))
    cut = cut.resize((w, h), Image.LANCZOS if s < 1 else Image.BILINEAR)
    out = Image.new("RGB", (size, size), WHITE)
    out.paste(cut, ((size - w) // 2, (size - h) // 2), cut)
    return out


class LeafClassifier:
    def __init__(self, weights, states_json, canvas_ratio=0.6, min_solidity=0.8, device=None, batch=64, imgsz=224):
        self.model = YOLO(str(weights))
        self.classes = [self.model.names[i] for i in sorted(self.model.names)]
        self.healthy = self.classes.index(HEALTHY)
        self.anom = [i for i in range(len(self.classes)) if i != self.healthy]
        self.th = json.loads(open(states_json).read())["thresholds"]
        self.canvas_ratio, self.min_solidity, self.device, self.batch, self.imgsz = canvas_ratio, min_solidity, device, batch, imgsz

    def probs(self, img, leaves):
        crops = [to_canvas(img, l, self.imgsz, self.canvas_ratio) for l in leaves]
        kw = dict(imgsz=self.imgsz, verbose=False)
        if self.device is not None:
            kw["device"] = self.device
        out = []
        for i in range(0, len(crops), self.batch):
            bgr = [np.ascontiguousarray(np.asarray(c)[:, :, ::-1]) for c in crops[i:i + self.batch]]
            out += [r.probs.data.float().cpu().numpy() for r in self.model.predict(bgr, **kw)]
        return (np.stack(out) if out else np.zeros((0, len(self.classes)), np.float32)), crops

    def state(self, leaf, p):
        th = self.th
        if leaf["solidity"] < self.min_solidity or leaf["touches_border"]:
            return "HOLD_SHAPE", None
        if leaf["sharpness"] < th["s_min"] or leaf["dark"] > th["d_max"] or leaf["L"] < th["L_min"]:
            return "HOLD", None
        k = max(self.anom, key=lambda j: p[j])
        if p[k] >= th["t_anom"]:
            return "ANOMALY", self.classes[k]
        if p[self.healthy] >= th["t_fresh"]:
            return "FRESH", None
        return "UNKNOWN", None
