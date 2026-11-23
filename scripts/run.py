"""파이프라인 실행.

사용 예:
  uv run python scripts/run.py --crop A --CL data/samples/rose_CL.jpg --CR data/samples/rose_CR.jpg
  uv run python scripts/run.py --crop A --CL ... --CR ... --out out/run1   # 시각화·JSON·보고서 저장

카메라 이름은 생장 단계 모듈(config/rose/holes.yaml)과 같음. 스테레오 좌우는 config/crops.yaml 의 cameras 에서 정함
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from crop_monitor import build_graph


def _jsonable(o):
    import numpy as np

    if isinstance(o, np.ndarray):
        return None  # 마스크 등 배열은 JSON 에서 뺌
    if isinstance(o, (np.integer, np.floating)):
        return o.item()
    if isinstance(o, tuple):
        return list(o)
    raise TypeError(type(o))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--crop", required=True, help="작물 ID (config/crops.yaml)")
    ap.add_argument("--CL", required=True, help="CL 카메라 이미지")
    ap.add_argument("--CR", required=True, help="CR 카메라 이미지")
    ap.add_argument("--out", type=Path, help="결과 폴더 (생략하면 저장 안 함)")
    args = ap.parse_args()

    out = None
    if args.out:
        out = args.out / datetime.now().strftime("%Y%m%d_%H%M%S")
        out.mkdir(parents=True, exist_ok=True)

    result = build_graph().invoke({
        "crop_id": args.crop,
        "images": {"CL": args.CL, "CR": args.CR},
        "out_dir": str(out) if out else None,
    })
    print(result["report"])

    if out:
        (out / "report.md").write_text(result["report"], encoding="utf-8")
        if "crop_data" in result:
            (out / "crop_data.json").write_text(
                json.dumps(result["crop_data"], ensure_ascii=False, indent=1, default=_jsonable), encoding="utf-8")
        print(f"\n저장: {out}")


if __name__ == "__main__":
    main()
