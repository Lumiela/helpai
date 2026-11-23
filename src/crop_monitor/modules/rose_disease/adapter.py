"""장미 잎 이상 상태 모듈 어댑터 (helpai-Rose_Disease / leafdx, 원본 rose_leaf_colab_keyfile.ipynb).

노트북 흐름을 그대로 따름:
    4. run.py    → LeafPipeline: 잎 탐지(leaf_seg_v8r2.pt) → 잎별 상태 분류(CLS_MODEL)
    6. verify.py → (선택) VLM 재확인: 이상·의심 잎 + 정상 잎 일부를 Claude/Qwen 에게 다시 물음

입력 : {카메라 이름: BGR 이미지}
출력 : {
    "cls_model": 분류 모델 파일명,
    "cameras": {카메라: {"leaves": [잎 ...], "counts": {상태: 개수}, "L": 추정 잎 길이 px}},
    "vlm": {"backend", "model", "tokens", "cost_usd"} | None,
}
잎: id, box, polygon, area, conf, state(FRESH|ANOMALY|UNKNOWN|HOLD|HOLD_SHAPE), anomaly_class, probs, (vlm)
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import cv2

from crop_monitor.keys import read_key
from crop_monitor.registry import resolve

from .leafdx import LeafPipeline, draw_result
from .leafdx import vlm as leaf_vlm


@lru_cache(maxsize=4)
def _pipeline(models_dir: str, config_path: str | None, cls_weights: str | None) -> LeafPipeline:
    cfg = json.loads(Path(config_path).read_text(encoding="utf-8")) if config_path and Path(config_path).exists() else {}
    if cls_weights:
        cfg["cls_weights"] = cls_weights
    return LeafPipeline(models_dir, config=cfg)


def _verifier(v: dict):
    backend = v.get("backend", "claude")
    if backend == "claude":
        key = read_key()
        if not key:
            raise FileNotFoundError("잎 이상 VLM 재확인: Claude API 키가 없습니다 → secrets/ANTHROPIC_API_KEY")
        return leaf_vlm.ClaudeVerifier(v.get("model", "claude-opus-5-5"), effort=v.get("effort", "low"), api_key=key)
    return leaf_vlm.QwenVerifier(v.get("model", "Qwen/Qwen2.5-VL-3B-Instruct"))


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


def run(images: dict, spec: dict, out_dir: Path | None = None) -> tuple[dict, list[str]]:
    pl = _pipeline(str(resolve(spec.get("models_dir", "weights/rose/disease"))),
                   str(resolve(spec["config"])) if spec.get("config") else None,
                   spec.get("cls_weights"))
    cls_model = pl.cfg["cls_weights"]
    warnings: list[str] = []

    v = spec.get("vlm") or {}
    ver = None
    if v.get("enabled"):
        try:
            ver = _verifier(v)
        except Exception as e:
            warnings.append(f"잎 이상: VLM 재확인 생략 ({e})")

    cams = {}
    for cam in spec.get("cameras") or list(images):
        if cam not in images:
            warnings.append(f"잎 이상: 카메라 {cam} 이미지 없음")
            continue
        rgb = cv2.cvtColor(images[cam], cv2.COLOR_BGR2RGB)
        res = pl.run(rgb)
        if out_dir is not None:
            d = Path(out_dir) / "disease"
            d.mkdir(parents=True, exist_ok=True)
            draw_result(rgb, res, title=f"{cam} [{Path(cls_model).stem}]").save(d / f"{cam}_vis.jpg", quality=90)
        res = LeafPipeline.to_json(res)  # 마스크 배열 제거
        if ver is not None:
            todo = leaf_vlm.select(res["leaves"], tuple(v.get("states", ["ANOMALY", "UNKNOWN"])),
                                   v.get("fresh", 5), v.get("max", 40))
            _review(ver, rgb, todo)
        cams[cam] = dict(leaves=res["leaves"], counts=res["counts"], L=res["L"], timing=res["timing"])

    vlm_info = None
    if ver is not None:
        cost = ver.cost()
        vlm_info = dict(backend=v.get("backend", "claude"), model=v.get("model"), tokens=list(ver.tokens),
                        cost_usd=round(cost, 4) if cost is not None else None)
    return {"cls_model": cls_model, "cameras": cams, "vlm": vlm_info}, warnings
