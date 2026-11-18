"""Check run.py results with a vision-language model (VLM) as a second opinion.

    python verify.py --photos <photo folder> --results <run.py output folder> [--backend auto|claude|qwen]
                     [--model ...] [--effort low] [--states ANOMALY,UNKNOWN] [--fresh 5] [--max 40] [--dry-run]

Per photo it reviews the flagged leaves (states) plus a random sample of FRESH leaves, and writes into the results folder:
<stem>_vlm.json (one row per reviewed leaf), <stem>_vlm.jpg (close-ups with model / VLM labels) and vlm_review.csv (all photos).
backend auto = claude when ANTHROPIC_API_KEY is set, else qwen (open model, needs a GPU).
"""
import argparse, csv, json, os, sys  # noqa: E401
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from leafdx import load_rgb  # noqa: E402
from leafdx import vlm  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--photos", required=True); ap.add_argument("--results", required=True)
ap.add_argument("--backend", default="auto", choices=["auto", "claude", "qwen"])
ap.add_argument("--model", help="default: claude-opus-5-5 (claude) / Qwen/Qwen2.5-VL-3B-Instruct (qwen)")
ap.add_argument("--effort", default="low", help="claude only: low | medium | high")
ap.add_argument("--states", default="ANOMALY,UNKNOWN", help="leaf states to review")
ap.add_argument("--fresh", type=int, default=5, help="random FRESH leaves per photo as a spot check")
ap.add_argument("--max", type=int, default=40, help="max reviewed leaves per photo")
ap.add_argument("--seed", type=int, default=0)
ap.add_argument("--dry-run", action="store_true", help="only count the leaves that would be reviewed")
a = ap.parse_args()

results = Path(a.results)
todo = []
for f in sorted(results.glob("*.json")):
    if f.stem.endswith("_vlm"):
        continue
    res = json.loads(f.read_text(encoding="utf-8"))
    leaves = vlm.select(res["leaves"], tuple(a.states.split(",")), a.fresh, a.max, a.seed)
    todo.append((Path(a.photos) / res["photo"], f.stem, leaves, res.get("cls_model", "")))
    print(f"{res['photo']}: {len(leaves)} leaves to review", flush=True)
n = sum(len(t[2]) for t in todo)
print("total", n, "leaves", flush=True)
if a.dry_run or not n:
    sys.exit(0)

backend = a.backend if a.backend != "auto" else ("claude" if os.environ.get("ANTHROPIC_API_KEY") else "qwen")
model = a.model or ("claude-opus-5-5" if backend == "claude" else "Qwen/Qwen2.5-VL-3B-Instruct")
print("backend", backend, model, flush=True)
ver = vlm.ClaudeVerifier(model, effort=a.effort) if backend == "claude" else vlm.QwenVerifier(model)


def review(item):
    leaf, close, ctx = item
    try:
        ans = ver([close, ctx])
    except ver.fatal:
        raise
    except Exception as e:  # one failed leaf should not stop the run
        ans = dict(label="unclear", confidence="low", symptoms="", reason="", error=f"{type(e).__name__}: {e}"[:300])
    return dict(id=leaf["id"], state=leaf["state"], model=vlm.model_label(leaf) or leaf["state"],
                model_p=round(max(leaf["probs"].values()), 4), vlm_label=ans["label"], confidence=ans["confidence"],
                verdict=vlm.verdict(leaf, ans["label"]), symptoms=ans["symptoms"], reason=ans["reason"],
                error=ans.get("error", ""), box=leaf["box"])


all_rows = []
for photo, stem, leaves, cls_model in todo:
    if not leaves:
        continue
    rgb = load_rgb(photo)
    items = [(l, *vlm.views(rgb, l)) for l in leaves]
    with ThreadPoolExecutor(ver.workers) as ex:
        rows = list(ex.map(review, items))
    (results / f"{stem}_vlm.json").write_text(json.dumps(dict(photo=photo.name, backend=backend, rows=rows), ensure_ascii=False, indent=1), encoding="utf-8")
    vlm.contact_sheet([(it[1], r) for it, r in zip(items, rows)]).save(results / f"{stem}_vlm.jpg", quality=90)
    all_rows += [dict(photo=photo.name, cls_model=cls_model, **r) for r in rows]
    print(photo.name, dict(Counter(r["verdict"] for r in rows)), flush=True)

cols = ["photo", "cls_model", "id", "state", "model", "model_p", "vlm_label", "confidence", "verdict", "symptoms", "reason", "error", "box"]
with open(results / "vlm_review.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(all_rows)
print("verdicts", dict(Counter(r["verdict"] for r in all_rows)))
errors = sum(1 for r in all_rows if r["error"])
if errors:
    print("errors", errors, "(see the error column)")
cost = ver.cost()
print("tokens in/out", ver.tokens, "" if cost is None else f"approx ${cost:.3f}")
