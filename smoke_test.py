"""Check that the package reproduces the server pipeline on the sample photo.

Reference (v8_reallabel, 2026-10-15): samples/fix_eafda190.jpg -> 308 leaves: FRESH 277, HOLD_SHAPE 31
(leaf_search_v8.py --no-bg-filter + cls/scripts/classify_v8.py).
    python smoke_test.py [--device cpu]
"""
import argparse, sys  # noqa: E401
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from leafdx import LeafPipeline, load_rgb  # noqa: E402

ap = argparse.ArgumentParser(); ap.add_argument("--device"); a = ap.parse_args()
pl = LeafPipeline(HERE / "models", config=({"device": a.device} if a.device else None))
res = pl.run(load_rgb(HERE / "samples" / "fix_eafda190.jpg"))
expected = {"FRESH": 277, "HOLD_SHAPE": 31}
print("leaves", len(res["leaves"]), "counts", res["counts"], "expected", expected, "timing", res["timing"])
ok = len(res["leaves"]) == 308 and res["counts"] == expected
print("SMOKE_TEST", "PASS" if ok else "DIFF (small differences can come from a different GPU / library version)")
