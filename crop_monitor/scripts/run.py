#!/usr/bin/env python3
"""작물 모니터링 파이프라인 실행 (네이버 서버). LangGraph·Gemini 는 여기서, 모델 추론은 젯슨에서.

    .venv/bin/python scripts/run.py --latest                                # 가장 최근 동시 촬영 쌍
    .venv/bin/python scripts/run.py --pair 17b423748066                      # 특정 쌍(요청번호에서 a,b 를 뺀 앞부분)
    .venv/bin/python scripts/run.py --CL a.jpg --CR b.jpg                   # 사진 직접 지정
    옵션: --crop A, --out <폴더>(기본 out), --no-vlm(Gemini 를 쓰지 않음)

젯슨은 한 번에 한 건만 처리하므로 그래프의 병렬 노드를 차례로 실행한다(max_concurrency=1).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SNAP_ROOT = Path(os.environ.get("SNAPSHOT_DIR", "/data/snapshots"))


def _jsonable(o):
    import numpy as np

    if isinstance(o, np.ndarray):
        return None  # 마스크 등 배열은 JSON 에서 뺌
    if isinstance(o, (np.integer, np.floating)):
        return o.item()
    if isinstance(o, tuple):
        return list(o)
    raise TypeError(type(o))


def find_pair(devices: dict[str, str], pair_id: str | None) -> tuple[str, dict[str, str]]:
    """동시 촬영 쌍: 사이드카 request_id 가 <쌍번호>a / <쌍번호>b 로 끝나는 두 장. 반환 (쌍번호, {카메라: 경로})."""
    groups: dict[str, dict[str, tuple[str, Path]]] = {}
    for cam, sn in devices.items():
        for p in SNAP_ROOT.glob(f"*/*/{sn}/*/*_pair.jpg"):
            try:
                side = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rid = str(side.get("request_id") or "")
            if rid[-1:] in ("a", "b"):
                groups.setdefault(rid[:-1], {})[cam] = (str(side.get("taken_at") or ""), p)
    full = {k: v for k, v in groups.items() if set(v) == set(devices)}
    if pair_id:
        if pair_id not in full:
            raise SystemExit(f"쌍 {pair_id} 을 찾지 못했습니다 (두 장이 모두 있는 쌍 {len(full)}개)")
        pid = pair_id
    elif full:
        pid = max(full, key=lambda k: min(t for t, _ in full[k].values()))
    else:
        raise SystemExit("두 장이 모두 있는 동시 촬영 쌍이 없습니다")
    return pid, {cam: str(p) for cam, (_, p) in full[pid].items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--crop", default="A", help="작물 ID (config/crops.yaml)")
    ap.add_argument("--CL", help="CL 카메라 사진")
    ap.add_argument("--CR", help="CR 카메라 사진")
    ap.add_argument("--latest", action="store_true", help="가장 최근 동시 촬영 쌍")
    ap.add_argument("--pair", help="동시 촬영 쌍번호")
    ap.add_argument("--out", type=Path, default=ROOT / "out", help="결과 폴더")
    ap.add_argument("--no-vlm", action="store_true", help="Gemini 검수·재확인을 하지 않음")
    args = ap.parse_args()

    from crop_monitor import build_graph
    from crop_monitor.registry import _raw

    if args.CL and args.CR:
        pid, images = "manual", {"CL": args.CL, "CR": args.CR}
    elif args.latest or args.pair:
        pid, images = find_pair(_raw()["cameras"]["devices"], args.pair)
    else:
        ap.error("--latest, --pair 또는 --CL/--CR 가 필요합니다")
    print("쌍 %s\n  CL %s\n  CR %s" % (pid, images["CL"], images["CR"]), flush=True)

    out = args.out / f"{datetime.now():%Y%m%d_%H%M%S}_{pid}"
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    result = build_graph().invoke({"crop_id": args.crop, "images": images, "out_dir": str(out), "use_vlm": not args.no_vlm},
                                  config={"max_concurrency": 1})
    print(result["report"])
    (out / "report.md").write_text(result["report"], encoding="utf-8")
    if "crop_data" in result:
        data = {"pair_id": pid, "images": images, "elapsed_s": round(time.time() - t0, 1), **result["crop_data"],
                "warnings": result.get("warnings", [])}
        (out / "crop_data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1, default=_jsonable), encoding="utf-8")
    print("\n저장: %s (%.0f초)" % (out, time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
