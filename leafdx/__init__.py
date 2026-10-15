"""leafdx: mini-rose leaf finder + per-leaf state classifier (v8, 2026-10-15).

    from leafdx import LeafPipeline
    pl = LeafPipeline("models")              # folder with leaf_seg_v8r2.pt, leaf_cls_v8.pt, states.json (+ config.json)
    result = pl.run(rgb)                      # rgb: HxWx3 uint8 (RGB)
"""
from . import _compat  # noqa: F401  (must run before cv2 / ultralytics)
from .pipeline import LeafPipeline, load_rgb  # noqa: E402,F401
from .draw import draw_result  # noqa: E402,F401

__version__ = "v8.2026-10-15"
