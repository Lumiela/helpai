"""leafdx: mini-rose leaf finder + per-leaf state classifier (YOLOv8m-cls, 2026-11-02).

    from leafdx import LeafPipeline
    pl = LeafPipeline("models")              # folder with leaf_seg_v8r2.pt, leaf_cls_yolov8m.pt, states.json (+ config.json)
    result = pl.run(rgb)                      # rgb: HxWx3 uint8 (RGB)
"""
from . import _compat  # noqa: F401  (must run before cv2 / ultralytics)
from .pipeline import LeafPipeline, load_rgb  # noqa: E402,F401
from .draw import draw_result  # noqa: E402,F401

__version__ = "v8-yolocls.2026-11-04"
