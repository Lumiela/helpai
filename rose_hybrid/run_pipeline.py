"""미니 장미 생장 단계 판단 실행.

    python run_pipeline.py                                  # 설정의 input/CL.jpg, input/CR.jpg
    python run_pipeline.py --CL a.jpg --CR b.jpg            # 이미지 직접 지정
    python run_pipeline.py --no-detector                    # 검출 모델 없이 1단계만 확인
"""

import argparse

from rose_pipeline import RosePipeline
from rose_pipeline.config import load_config, resolve
from rose_pipeline.visualize import save_outputs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="configs/pipeline.yaml")
    ap.add_argument("--CL", help="CL 카메라 이미지 경로 (설정보다 우선)")
    ap.add_argument("--CR", help="CR 카메라 이미지 경로 (설정보다 우선)")
    ap.add_argument("--no-detector", action="store_true", help="봉오리 검출 생략 (모든 식물 = 기본 클래스)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    for name in ("CL", "CR"):
        path = getattr(args, name)
        if path:
            cfg["cameras"][name]["source"] = {"type": "file", "path": path}
    if args.no_detector:
        cfg["grading"]["detector"]["type"] = "none"

    result = RosePipeline(cfg).run()

    out = cfg["output"]
    run_dir = save_outputs(result, resolve(out["dir"]), out.get("save_visualization", True),
                           out.get("save_masks", True))
    print(result.table())
    print("\n" + result.bud_table())
    print(f"\n결과 저장: {run_dir}")


if __name__ == "__main__":
    main()
