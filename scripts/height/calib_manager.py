"""스테레오 보정 관리 도구 — A(체커보드 재보정) + B(자동 미세 조정) + 이력.

사진 쌍 지정 (check / auto / set-reference):
    --pair CL.jpg CR.jpg        파이프라인 카메라 이름 순서(CL, CR)로. 여러 번 줄 수 있음
    --from-api [--n 3]          카메라 API 의 최근 동시 촬영 쌍 n 개
    (스테레오 왼쪽/오른쪽은 config/crops.yaml 의 cameras.stereo_left/right 로 자동 배치)

명령:
    status                       지금 보정 정보 + 이력
    check   --pair ... | --from-api       상태만 확인 (정상 / 어긋남 / 좌우 바뀜)
    auto    --pair ... | --from-api [--dry-run] [--force]
                                 상태가 나쁘면 B 시도 → 성공하면 새 버전 저장·교체, 실패하면 A 안내
    set-reference --pair ... [--cm 42.0]
                                 지금 보정이 맞을 때 트레이 거리를 기준으로 기록 (B 가 좌우 돌아감까지 맞추게 함)
                                 --cm 을 주면 줄자로 잰 카메라→트레이 거리를 기록
    checkerboard --square 24.2 (--since 2026-11-27T10:00 | --dir calib) [--board 9x6] [--width 640] [--force]
                                 A: 체커보드 사진으로 전체 재보정 → 검증 → 저장·교체
    rollback [--to ID]           이전 버전(또는 지정 버전)으로 되돌리기

예:
    uv run python scripts/height/calib_manager.py check --pair data/samples/rose_CL.jpg data/samples/rose_CR.jpg
    uv run python scripts/height/calib_manager.py auto --from-api --n 3
결과 확인 그림: out/calib/<시각>/ (평행화 전후 나란히 + 가로선)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import cv2

from crop_monitor.calibration import checkerboard
from crop_monitor.calibration.manager import classify, get_store, health, load_config, maintain, set_reference
from crop_monitor.modules.height import stereo_pair as sp
from crop_monitor.nodes.preprocess import read_image
from crop_monitor.registry import height_config, resolve, stereo_pair

STATUS_KR = {"ok": "정상", "drift": "어긋남 (B 로 고칠 수 있음)", "bad": "크게 어긋남 (A 필요할 수 있음)",
             "swapped": "좌우 바뀜"}


def load_pairs(a) -> list[tuple]:
    left, right = stereo_pair()
    pairs = []
    if getattr(a, "from_api", False):
        from crop_monitor.camera_api import latest_pairs
        for p in latest_pairs(a.n):
            print(f"API 사진 쌍: {p['taken_at']} ({p['pair_id']})")
            pairs.append((p["images"][left], p["images"][right]))
    for cl, cr in getattr(a, "pair", None) or []:
        imgs = {"CL": read_image(cl), "CR": read_image(cr)}
        pairs.append((imgs[left], imgs[right]))
    if not pairs:
        sys.exit("사진 쌍이 없습니다: --pair CL.jpg CR.jpg 또는 --from-api")
    return pairs


def save_check_images(pairs, before: dict, after: dict | None):
    out = resolve("out/calib") / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    for i, (L, R) in enumerate(pairs):
        for tag, c in (("before", before), ("after", after)):
            if c is None:
                continue
            size = tuple(int(v) for v in c["size"])
            Ls, Rs = (cv2.resize(x, size, interpolation=cv2.INTER_AREA) for x in (L, R))
            m1 = cv2.initUndistortRectifyMap(c["K1"], c["D1"], c["R1"], c["P1"], size, cv2.CV_32FC1)
            m2 = cv2.initUndistortRectifyMap(c["K2"], c["D2"], c["R2"], c["P2"], size, cv2.CV_32FC1)
            cv2.imwrite(str(out / f"pair{i + 1}_{tag}.jpg"),
                        sp.align_check(cv2.remap(Ls, *m1, cv2.INTER_LINEAR), cv2.remap(Rs, *m2, cv2.INTER_LINEAR)))
    return out


def cmd_status(a):
    hc = height_config()
    store = get_store(hc)
    cur = store.current_id()
    print("지금 보정:", hc.calib)
    for m in store.list():
        mark = "▶" if m["id"] == cur else " "
        ref = f", 거리 기준 {m['reference_cm']}cm" if m.get("reference_cm") else ""
        dy = m.get("rect_dy_median_px")
        print(f" {mark} {m['id']:<22} {m.get('source', ''):<8} 간격 {m.get('baseline_mm')}mm  "
              f"세로 어긋남 {dy if dy is not None else '-'}px{ref}  {m.get('note', '')}")


def cmd_check(a):
    hc = height_config()
    cfg = load_config()
    store = get_store(hc, cfg)
    pairs = load_pairs(a)
    h = health(store.load(), pairs)
    st = classify(h, cfg)
    for i, p in enumerate(h["pairs"], 1):
        print(f"쌍 {i}: 매칭 {p['n']}개, 세로 어긋남 {p['dy_median']}px, 시차 {p['disp_median']}px "
              f"(좌우 바꾸면 {p['swapped_dy_median']}px / {p['swapped_disp_median']}px)")
    print(f"상태: {STATUS_KR[st]} — 세로 어긋남 중앙값 {h['dy_median']}px (정상 ≤ {cfg.ok_dy_px}px)")
    print("평행화 확인 그림:", save_check_images(pairs, store.load(), None))


def cmd_auto(a):
    hc = height_config()
    pairs = load_pairs(a)
    store = get_store(hc)
    before = store.load()
    r = maintain(pairs, hc, dry_run=a.dry_run, force=a.force)
    print(f"[{r.action}] {r.message}")
    if r.refine is not None and r.refine.calib is not None:
        print("평행화 전후 확인 그림:", save_check_images(pairs, before, r.refine.calib))
    sys.exit(0 if r.action in ("none", "refined") else 2)


def cmd_set_reference(a):
    hc = height_config()
    pairs = load_pairs(a) if (a.pair or a.from_api) else [None]
    if a.cm is None and pairs[0] is None:
        sys.exit("--cm(줄자 실측) 또는 --pair/--from-api(측정할 사진)가 필요합니다")
    cid, cm = set_reference(pairs[0], hc, value_cm=a.cm)
    print(f"거리 기준 {cm}cm 를 보정 {cid} 에 기록했습니다. 이후 B 가 좌우 돌아감까지 이 거리에 맞춥니다")


def cmd_checkerboard(a):
    hc = height_config()
    store = get_store(hc)
    r = checkerboard.run(store, a.square, a.board, a.width, pairs_dir=a.dir, since=a.since,
                         min_pairs=a.min_pairs, max_dy_px=a.max_dy,
                         max_baseline_change=1e9 if a.force else 0.10)
    if a.verbose and r.log:
        print(r.log)
    if r.ok:
        rep = r.report
        print(f"[A 성공] 보정 {r.saved_id} 저장·교체 — 세로 어긋남 {rep.get('rect_dy_median_px')}px, "
              f"간격 {rep.get('baseline_mm')}mm, 쌍 {len(rep.get('pairs_used') or [])}개 ({rep.get('grade')})")
        print("다음: 보정이 맞는 지금 트레이 거리를 기준으로 기록하세요 → calib_manager.py set-reference --from-api")
    else:
        print(f"[A 실패] {r.reason}")
        sys.exit(2)


def cmd_rollback(a):
    store = get_store(height_config())
    print("되돌림 →", store.rollback(a.to))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def pairs_args(p):
        p.add_argument("--pair", nargs=2, action="append", metavar=("CL", "CR"))
        p.add_argument("--from-api", action="store_true")
        p.add_argument("--n", type=int, default=3, help="--from-api 로 받을 쌍 수")

    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("check")
    pairs_args(p)
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("auto")
    pairs_args(p)
    p.add_argument("--dry-run", action="store_true", help="계산만 하고 저장·교체하지 않음")
    p.add_argument("--force", action="store_true", help="상태가 정상이어도 B 실행")
    p.set_defaults(fn=cmd_auto)
    p = sub.add_parser("set-reference")
    pairs_args(p)
    p.add_argument("--cm", type=float, help="줄자로 잰 카메라 → 트레이 거리(cm)")
    p.set_defaults(fn=cmd_set_reference)
    p = sub.add_parser("checkerboard")
    p.add_argument("--square", type=float, required=True, help="체커보드 칸 크기 mm")
    p.add_argument("--board", default="9x6")
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--since", help="API 에서 이 시각 이후 동시 촬영 사진을 받음 (예 2026-11-27T10:00)")
    p.add_argument("--dir", help="이미 모아 둔 L_NN/R_NN 사진 폴더")
    p.add_argument("--min-pairs", type=int, default=6)
    p.add_argument("--max-dy", type=float, default=1.5, help="통과 기준: 평행화 세로 어긋남 중앙값 px")
    p.add_argument("--force", action="store_true", help="카메라 간격이 10%% 넘게 바뀌어도 저장")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(fn=cmd_checkerboard)
    p = sub.add_parser("rollback")
    p.add_argument("--to")
    p.set_defaults(fn=cmd_rollback)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
