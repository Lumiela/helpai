"""Visualisation: leaf outlines coloured by state + legend with counts."""
from . import _compat  # noqa: F401
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

COLORS = {"FRESH": (60, 230, 60), "Black Spot": (255, 40, 40), "Hole": (40, 140, 255), "Yellow": (255, 220, 0),
          "UNKNOWN": (255, 0, 255), "HOLD": (150, 150, 150), "HOLD_SHAPE": (90, 90, 90)}


def _font(s):
    try:
        return ImageFont.load_default(size=s)
    except TypeError:
        return ImageFont.load_default()


def draw_result(rgb, result, title=""):
    vis = (rgb.astype(np.float32) * 0.8).astype(np.uint8)
    for l in result["leaves"]:
        key = l["anomaly_class"] if l["state"] == "ANOMALY" else l["state"]
        pts = np.array(l["polygon"], np.int32).reshape(-1, 1, 2)
        cv2.polylines(vis, [pts], True, COLORS[key], 5 if l["state"] in ("ANOMALY", "UNKNOWN") else 2)
    pil = Image.fromarray(vis); d = ImageDraw.Draw(pil)
    F, FB = _font(22), _font(30)
    for l in result["leaves"]:
        if l["state"] in ("ANOMALY", "UNKNOWN"):
            d.text((l["box"][0], max(0, l["box"][1] - 26)), f"{l['id']} {l['anomaly_class'] or 'UNKNOWN'}", font=F,
                   fill=COLORS[l["anomaly_class"] or "UNKNOWN"], stroke_width=3, stroke_fill=(0, 0, 0))
    items = sorted(result["counts"].items())
    d.rectangle([8, 8, 640, 56 + 32 * len(items)], fill=(0, 0, 0))
    d.text((18, 14), f"{title} leaves {len(result['leaves'])} | L {result['L']:.0f}px", font=FB, fill=(255, 255, 255))
    for i, (k, v) in enumerate(items):
        c = COLORS[k.split(":")[-1]]
        d.rectangle([18, 58 + 32 * i, 48, 78 + 32 * i], outline=c, width=4); d.text((60, 54 + 32 * i), f"{k} {v}", font=F, fill=(255, 255, 255))
    return pil
