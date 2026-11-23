"""미니 장미 생장 단계 모듈만 단독 실행 (원본 run_pipeline.py).

    uv run python scripts/rose_growth/run_pipeline.py                                  # 설정의 input/CL.jpg, input/CR.jpg
    uv run python scripts/rose_growth/run_pipeline.py --CL a.jpg --CR b.jpg            # 이미지 직접 지정
    uv run python scripts/rose_growth/run_pipeline.py --no-detector                    # 검출 모델 없이 1단계만 확인
"""

import argparse

from crop_monitor.modules.rose_growth.rose_pipeline import RosePipeline
from crop_monitor.modules.rose_growth.rose_pipeline.config import load_config, resolve
from crop_monitor.modules.rose_growth.rose_pipeline.visualize import save_outputs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config/rose/growth.yaml")
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

    pipeline = RosePipeline(cfg)
    result = pipeline.run()

    out = cfg["output"]
    run_dir = save_outputs(result, resolve(out["dir"]), out.get("save_visualization", True),
                           out.get("save_masks", True))
    print(result.table())
    print("\n" + result.bud_table())
    if result.vlm_table():
        print("\n" + result.vlm_table())
    usage = getattr(pipeline.detector, "usage", None)  # hybrid: 후보·검수 개수, vlm_plant/검수: Claude 요청·토큰 수
    if usage:
        print(f"\n사용량 (후보·분류·Claude 검수 개수, Claude 요청·토큰): {usage}")
    print(f"\n결과 저장: {run_dir}")


if __name__ == "__main__":
    main()
