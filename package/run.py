"""Run the leaf pipeline on a photo or a folder.

    python run.py <image or folder> --out <dir> [--models models] [--device cuda:0|cpu] [--L px]

Writes per photo: <stem>.json (all leaves, states, probabilities, polygons), <stem>_vis.jpg;
and summary.csv (one row per photo with counts per state).
"""
import argparse, csv, json, sys  # noqa: E401
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from leafdx import LeafPipeline, load_rgb, draw_result  # noqa: E402

EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".jfif"}
ap = argparse.ArgumentParser(); ap.add_argument("src"); ap.add_argument("--out", required=True)
ap.add_argument("--models", default=str(Path(__file__).resolve().parent / "models")); ap.add_argument("--device")
ap.add_argument("--L", type=float, help="known leaf length in px (skips the estimation pass)")
a = ap.parse_args()
src = Path(a.src); out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
photos = [src] if src.is_file() else sorted(p for p in src.iterdir() if p.suffix.lower() in EXTS)
pl = LeafPipeline(a.models, config=({"device": a.device} if a.device else None))
rows, states = [], set()
for p in photos:
    rgb = load_rgb(p)
    res = pl.run(rgb, L=a.L)
    (out / f"{p.stem}.json").write_text(json.dumps(dict(photo=p.name, **LeafPipeline.to_json(res)), ensure_ascii=False))
    draw_result(rgb, res, title=p.stem).save(out / f"{p.stem}_vis.jpg", quality=90)
    states |= set(res["counts"]); rows.append(dict(photo=p.name, leaves=len(res["leaves"]), L=res["L"], **res["counts"], **res["timing"]))
    print(p.name, len(res["leaves"]), res["counts"], res["timing"], flush=True)
cols = ["photo", "leaves", "L"] + sorted(states) + ["stage1_s", "stage2_s"]
with open(out / "summary.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols, restval=0); w.writeheader(); w.writerows(rows)
