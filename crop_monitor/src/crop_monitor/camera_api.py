"""스마트큐브 카메라 API 클라이언트 (ESP32-CAM 2대의 동시 촬영 사진 받기).

scripts/height/collect_pairs.py 와 같은 API·짝 맞추기 규칙을 씀:
    목록   GET {base}/cubes/{cube}/snapshots?cam=<SN>&date=YYYY-MM-DD&limit=N
    사이드카 GET {base}/cubes/{cube}/snapshots/<SN>/<date>/<file>.json  → request_id = <pair_id>a | <pair_id>b
    사진   GET {base}/cubes/{cube}/snapshots/<SN>/<date>/<file>
    trigger == "pair" 인 사진을 request_id 앞부분(pair_id)으로 묶으면 두 카메라 동시 촬영 쌍.

설정 (config/crops.yaml 의 camera_api):
    base, cube, serials: {CL: CUBE001-C1, CR: CUBE001-C2}   ← 파이프라인 카메라 이름 ↔ 카메라 SN
토큰: 환경 변수 LEAFDX_API_TOKEN, 없으면 secrets/SMARTCUBE_API_TOKEN(.txt) (git 에 올라가지 않음)
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import date as date_cls, timedelta

import cv2
import numpy as np

from crop_monitor.registry import PROJECT_ROOT, _raw


def _cfg() -> dict:
    c = dict(_raw().get("camera_api") or {})
    c.setdefault("base", os.environ.get("SMARTCUBE_API", "https://smartcubens.duckdns.org/i/jetson-01/api"))
    c.setdefault("cube", os.environ.get("SMARTCUBE_CUBE", "CUBE001"))
    c.setdefault("serials", {"CL": "CUBE001-C1", "CR": "CUBE001-C2"})
    c.setdefault("timeout", 60)
    return c


def _token() -> str | None:
    tok = os.environ.get("LEAFDX_API_TOKEN")
    if tok:
        return tok.strip()
    for name in ("SMARTCUBE_API_TOKEN", "SMARTCUBE_API_TOKEN.txt"):
        p = PROJECT_ROOT / "secrets" / name
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    return line.split("=", 1)[-1].strip().strip("'\"")
    return None


def get(path: str, binary: bool = False):
    c = _cfg()
    req = urllib.request.Request(c["base"] + path, headers={"Accept": "application/json"})
    tok = _token()
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    with urllib.request.urlopen(req, timeout=c["timeout"]) as r:
        data = r.read()
    return data if binary else json.loads(data.decode("utf-8"))


def list_pairs(day: str, since: str = "") -> list[dict]:
    """그날 두 카메라 동시 촬영 쌍. 반환: [{pair_id, taken_at, rows: {카메라 이름: row}}] (오래된 순)."""
    c = _cfg()
    name_of = {sn: name for name, sn in c["serials"].items()}
    groups: dict[str, dict] = {}
    for sn in c["serials"].values():
        rows = get(f"/cubes/{c['cube']}/snapshots?cam={sn}&date={day}&limit=500")["rows"]
        for row in rows:
            if row.get("trigger") != "pair" or (row.get("taken_at") or "") < since:
                continue
            side = get(f"/cubes/{c['cube']}/snapshots/{sn}/{row['date']}/{row['file'][:-4]}.json")
            rid = side.get("request_id") or ""
            if rid[-1:] not in ("a", "b"):
                continue
            groups.setdefault(rid[:-1], {})[name_of[sn]] = dict(row, cam=sn, cap_at=side.get("cap_at"))
    out = []
    for pid, rows in groups.items():
        if len(rows) == len(c["serials"]):
            out.append(dict(pair_id=pid, taken_at=min(r.get("taken_at") or "" for r in rows.values()), rows=rows))
    return sorted(out, key=lambda p: p["taken_at"])


def fetch_image(row: dict) -> np.ndarray:
    c = _cfg()
    data = get(f"/cubes/{c['cube']}/snapshots/{row['cam']}/{row['date']}/{row['file']}", binary=True)
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"사진을 읽을 수 없습니다: {row['cam']}/{row['file']}")
    return img


def latest_pairs(n: int = 1, since: str = "", days: int = 3) -> list[dict]:
    """최근 동시 촬영 쌍 n 개 (새로운 순). 반환: [{pair_id, taken_at, images: {카메라 이름: BGR}}]."""
    found = []
    today = date_cls.today()
    for k in range(days):
        day = (today - timedelta(days=k)).isoformat()
        try:
            found = list_pairs(day, since) + found
        except Exception:
            if k == 0:
                raise
        if len(found) >= n:
            break
    out = []
    for p in sorted(found, key=lambda p: p["taken_at"], reverse=True)[:n]:
        out.append(dict(pair_id=p["pair_id"], taken_at=p["taken_at"],
                        images={name: fetch_image(row) for name, row in p["rows"].items()}))
    return out
