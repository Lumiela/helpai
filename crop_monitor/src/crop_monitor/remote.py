"""젯슨 추론 창구 호출 (표준 라이브러리만) — 2026-11-24.

    growth(...)  → crop-infer  /growth  봉오리 위치·마스크·등급 분류 (GPU)
    height(...)  → crop-infer  /height  스테레오 높이 지도 (CPU)
    disease(...) → leafdx-api  /infer   잎 탐지·상태 분류 (GPU, 2026-11-06 부터 쓰던 창구)

주소·키·시간 제한은 config/crops.yaml 의 remote 항목에서 정한다.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class RemoteError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemoteConfig:
    crop_url: str = "http://100.96.221.46:8002"
    leafdx_url: str = "http://100.96.221.46:8001"
    key_file: str = "~/.leafdx/api_key"
    growth_timeout_s: int = 360
    disease_timeout_s: int = 420
    height_timeout_s: int = 180
    on_bad_calib: str = "skip"          # 보정이 지금 카메라와 맞지 않으면: skip = 높이를 내지 않음 | warn = 경고만 하고 냄

    @property
    def key(self) -> str:
        p = Path(os.path.expanduser(self.key_file))
        if not p.is_file():
            raise RemoteError(f"젯슨 요청 키 파일이 없습니다: {p}")
        return p.read_text(encoding="utf-8").strip()


def _post(url: str, data: bytes, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            resp = json.loads(e.read().decode("utf-8"))
        except Exception:
            resp = {"error": f"http {e.code}"}
        raise RemoteError(f"젯슨 응답 {e.code}: {_short(resp)}") from None
    except Exception as e:
        raise RemoteError(f"젯슨에 연결하지 못했습니다 ({type(e).__name__}: {getattr(e, 'reason', e)})") from None
    if not resp.get("ok"):
        raise RemoteError(f"젯슨 처리 실패: {_short(resp)}")
    return resp


def _short(resp: dict) -> str:
    d = {k: v for k, v in resp.items() if k != "ok"}
    if d.get("log"):                    # 긴 로그는 마지막 줄(오류 이유)만
        d["log"] = [line for line in str(d["log"]).splitlines() if line.strip()][-1][-300:]
    return json.dumps(d, ensure_ascii=False)[:500]


def _b64(path: str | Path) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def growth(cfg: RemoteConfig, images: dict[str, str], rects: dict[str, list], detector: dict | None = None) -> dict:
    """images {카메라: 사진 경로}, rects {카메라: [[x1, y1, x2, y2], …]} → 카메라별 영역별 봉오리."""
    body = {"cameras": {cam: {"name": Path(p).name, "b64": _b64(p), "rects": [[int(v) for v in r] for r in rects[cam]]}
                        for cam, p in images.items()}, "detector": detector or {}}
    return _post(cfg.crop_url + "/growth", json.dumps(body).encode("utf-8"),
                 {"Content-Type": "application/json", "X-Api-Key": cfg.key}, cfg.growth_timeout_s)


def height(cfg: RemoteConfig, left: str, right: str, config: dict) -> tuple[dict, bytes]:
    body = {"left": {"name": Path(left).name, "b64": _b64(left)}, "right": {"name": Path(right).name, "b64": _b64(right)}, "config": config}
    resp = _post(cfg.crop_url + "/height", json.dumps(body).encode("utf-8"),
                 {"Content-Type": "application/json", "X-Api-Key": cfg.key}, cfg.height_timeout_s)
    return resp, base64.b64decode(resp.pop("npz_b64"))


def disease(cfg: RemoteConfig, photo: str) -> dict:
    p = Path(photo)
    return _post(cfg.leafdx_url + "/infer", p.read_bytes(),
                 {"Content-Type": "application/octet-stream", "X-Api-Key": cfg.key, "X-Filename": p.name.encode("ascii", "replace").decode()},
                 cfg.disease_timeout_s)
