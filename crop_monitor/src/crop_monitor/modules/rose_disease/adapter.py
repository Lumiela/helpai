"""장미 잎 이상 상태 모듈 어댑터 — 잎 탐지·분류는 젯슨(leafdx-api), VLM 재확인은 Gemini (원본: helpai-Rose_Disease / leafdx).

    1. 젯슨 leafdx-api /infer : 잎 탐지(leaf_seg_v8r2.pt) → 잎별 상태 분류(leaf_cls_yolov8m.pt)
    2. (선택) Gemini 재확인   : 이상·의심 잎 + 정상 잎 일부를 다시 물음

입력 : {카메라 이름: BGR 이미지}, paths {카메라: 원본 사진 경로}
출력 : {
    "cls_model": 분류 모델 파일명,
    "cameras": {카메라: {"leaves": [잎 ...], "counts": {상태: 개수}, "L": 추정 잎 길이 px, "timing", "elapsed_s"}},
    "vlm": {"backend", "model", "tokens", "cost_usd"} | None,
}
잎: id, box, polygon, area, conf, state(FRESH|ANOMALY|UNKNOWN|HOLD|HOLD_SHAPE), anomaly_class, probs, (vlm)
"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2

from crop_monitor import remote
from crop_monitor.keys import read_key
from crop_monitor.registry import remote_config

from .leafdx import vlm as leaf_vlm

JETSON_CLS_MODEL = "leaf_cls_yolov8m.pt"   # 젯슨 leafdx-api 가 쓰는 분류 모델 (package/config.json)


def _review(ver, rgb, leaves):
    """verify.py 의 review() 와 같은 판단. 잎마다 vlm 결과를 붙임."""
    def one(leaf):
        close, ctx = leaf_vlm.views(rgb, leaf)
        try:
            ans = ver([close, ctx])
        except ver.fatal:
            raise
        except Exception as e:  # 잎 하나 실패로 전체를 멈추지 않음
            ans = dict(label="unclear", confidence="low", symptoms="", reason="", error=f"{type(e).__name__}: {e}"[:300])
        leaf["vlm"] = dict(label=ans["label"], confidence=ans["confidence"], verdict=leaf_vlm.verdict(leaf, ans["label"]),
                           symptoms=ans["symptoms"], reason=ans["reason"], error=ans.get("error", ""))

    with ThreadPoolExecutor(ver.workers) as ex:
        list(ex.map(one, leaves))


def run(images: dict, spec: dict, out_dir: Path | None = None, paths: dict | None = None) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    rcfg = remote_config()
    want = spec.get("cls_weights", JETSON_CLS_MODEL)
    if want != JETSON_CLS_MODEL:
        warnings.append(f"잎 이상: 젯슨 창구는 {JETSON_CLS_MODEL} 로 판정합니다 (설정의 {want} 는 쓰지 않음)")

    v = spec.get("vlm") or {}
    ver = None
    if v.get("enabled") and spec.get("use_vlm", True):
        key = read_key()
        if key:
            ver = leaf_vlm.GeminiVerifier(key, v.get("model", "gemini-4-hydrogen"), thinking=v.get("thinking", "low"))
        else:
            warnings.append("잎 이상: API 키가 없어 Gemini 재확인을 생략")

    cams = {}
    for cam in spec.get("cameras") or list(images):
        if cam not in images:
            warnings.append(f"잎 이상: 카메라 {cam} 이미지 없음")
            continue
        resp = remote.disease(rcfg, paths[cam])
        res = resp["result"]
        if list(res["size"]) != [images[cam].shape[1], images[cam].shape[0]]:
            raise remote.RemoteError(f"{cam}: 젯슨 결과의 사진 크기 {res['size']} 가 원본과 다릅니다")
        if out_dir is not None and resp.get("vis_jpg_b64"):
            d = Path(out_dir) / "disease"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{cam}_vis.jpg").write_bytes(base64.b64decode(resp["vis_jpg_b64"]))
        if ver is not None:
            todo = leaf_vlm.select(res["leaves"], tuple(v.get("states", ["ANOMALY", "UNKNOWN"])), v.get("fresh", 5), v.get("max", 40))
            try:
                _review(ver, cv2.cvtColor(images[cam], cv2.COLOR_BGR2RGB), todo)
            except ver.fatal as e:
                warnings.append(f"잎 이상: Gemini 재확인 중단 ({e})")
                ver = None
        cams[cam] = dict(leaves=res["leaves"], counts=res["counts"], L=res["L"], timing=res["timing"], elapsed_s=resp.get("elapsed_s"))

    vlm_info = None
    if ver is not None:
        cost = ver.cost()
        vlm_info = dict(backend="gemini", model=ver.model, tokens=list(ver.tokens), cost_usd=round(cost, 4) if cost is not None else None)
    return {"cls_model": JETSON_CLS_MODEL, "cameras": cams, "vlm": vlm_info}, warnings
