#!/usr/bin/env python3
"""collect_pairs.py — 네이버 서버의 동시 촬영(_pair) 사진을 요청번호로 짝지어 캘리브레이션용 L_NN/R_NN 으로 모은다 (2026-11-11)

    python collect_pairs.py --since 2026-11-11T15:19 --out calib [--left auto|CUBE001-C1|CUBE001-C2] [--width 640]

  1) api 목록에서 두 카메라의 _pair 사진(지정 시각 이후)을 받아 사이드카의 request_id(<pair_id>a / <pair_id>b)로 짝을 맞춘다
  2) 왼쪽 카메라 판정(--left auto): 첫 쌍에서 SIFT 매칭점의 시차 중앙값 부호로 (양수 = C1 이 왼쪽)
     ※ 좌우는 항상 **카메라 이름(SN)** 으로 정한다. 대시보드의 A/B 선택 순서(요청번호의 a/b)는 쌍을 찾는 데만 쓴다.
  3) 두 사진 모두에서 체커보드 내부 코너(기본 9×6)가 잡히는 쌍만 out/L_NN.jpg, R_NN.jpg 로 저장 (원본 JPEG 그대로)
  인증은 api 가 auth_mode=off 일 때 그대로 되고, 켜져 있으면 LEAFDX_API_TOKEN 환경변수의 Bearer 토큰을 쓴다.
"""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

import cv2
import numpy as np

BASE = os.environ.get("SMARTCUBE_API", "https://smartcubens.duckdns.org/i/jetson-01/api")
CUBE = os.environ.get("SMARTCUBE_CUBE", "CUBE001")
CAMS = ("CUBE001-C1", "CUBE001-C2")


def get(path, binary=False):
    req = urllib.request.Request(BASE + path, headers={"Accept": "application/json"})
    tok = os.environ.get("LEAFDX_API_TOKEN")
    if tok:
        req.add_header("Authorization", "Bearer " + tok)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return data if binary else json.loads(data.decode("utf-8"))


def list_pairs(date, since):
    """카메라별 목록 → _pair 사진 → 사이드카 request_id 로 묶기. 반환 {pair_id: {카메라 SN: row}}"""
    groups = {}
    for cam in CAMS:
        rows = get("/cubes/%s/snapshots?cam=%s&date=%s&limit=500" % (CUBE, cam, date))["rows"]
        for row in rows:
            if row.get("trigger") != "pair" or (row.get("taken_at") or "") < since:
                continue
            side = get("/cubes/%s/snapshots/%s/%s/%s" % (CUBE, cam, row["date"], row["file"][:-4] + ".json"))
            rid = side.get("request_id") or ""
            if rid[-1:] not in ("a", "b"):
                continue
            row = dict(row, cam=cam, ab=rid[-1], cap_at=side.get("cap_at"), cmd_ms=side.get("cmd_ms"))
            groups.setdefault(rid[:-1], {})[cam] = row
    return groups


def fetch(row):
    return get("/cubes/%s/snapshots/%s/%s/%s" % (CUBE, row["cam"], row["date"], row["file"]), binary=True)


def decode_small(data, width):
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    s = width / img.shape[1]
    return cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)


def median_disparity(a, b):
    """SIFT 매칭점의 x 차이(a − b) 중앙값. 양수면 a 가 왼쪽 카메라"""
    ga, gb = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    sift = cv2.SIFT_create(nfeatures=4000)
    ka, da = sift.detectAndCompute(ga, None)
    kb, db = sift.detectAndCompute(gb, None)
    if da is None or db is None:
        return None
    knn = cv2.BFMatcher().knnMatch(da, db, k=2)
    good = [m for m, n in (p for p in knn if len(p) == 2) if m.distance < 0.75 * n.distance]
    if len(good) < 20:
        return None
    pa = np.float32([ka[m.queryIdx].pt for m in good])
    pb = np.float32([kb[m.trainIdx].pt for m in good])
    d = (pa[:, 0] - pb[:, 0])[np.abs(pa[:, 1] - pb[:, 1]) < 3]
    return float(np.median(d)) if len(d) >= 10 else None


def corners_ok(img, board):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ok, _ = cv2.findChessboardCornersSB(g, board, cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_EXHAUSTIVE)
    if not ok:
        ok, _ = cv2.findChessboardCorners(g, board, cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE)
    return bool(ok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="이 시각 이후(ISO, 예 2026-11-11T15:19)")
    ap.add_argument("--date", help="날짜 폴더(기본 = --since 의 날짜)")
    ap.add_argument("--out", default="calib")
    ap.add_argument("--left", default="auto", help="auto | CUBE001-C1 | CUBE001-C2")
    ap.add_argument("--board", default="9x6", help="내부 코너 수 가로x세로")
    ap.add_argument("--width", type=int, default=640, help="판정용 축소 가로 (캘리브레이션 CALIB_WIDTH 와 같게)")
    a = ap.parse_args()
    date = a.date or a.since[:10]
    board = tuple(int(v) for v in a.board.lower().split("x"))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    groups = list_pairs(date, a.since)
    complete = {k: v for k, v in groups.items() if set(v) == set(CAMS)}
    print("동시 촬영 쌍: 전체 %d, 양쪽 다 있음 %d" % (len(groups), len(complete)))
    if not complete:
        return 1

    first_time = lambda s: min(r["taken_at"] for r in s.values())
    order = sorted(complete.items(), key=lambda kv: first_time(kv[1]))
    imgs = {pid: {cam: fetch(r) for cam, r in s.items()} for pid, s in order}

    left_cam = a.left
    if left_cam == "auto":
        pid0, _ = order[0]
        d = median_disparity(decode_small(imgs[pid0][CAMS[0]], a.width), decode_small(imgs[pid0][CAMS[1]], a.width))
        if d is None:
            print("왼쪽 판정 실패(매칭점 부족) → --left 로 지정하세요")
            return 1
        left_cam = CAMS[0] if d > 0 else CAMS[1]
        print("왼쪽 카메라 판정: %s (첫 쌍 %s 의 시차 중앙값 %+.1f px)" % (left_cam, pid0, d))
    right_cam = CAMS[1] if left_cam == CAMS[0] else CAMS[0]

    kept = 0
    for pid, s in order:
        la, ra = imgs[pid][left_cam], imgs[pid][right_cam]
        ok_l = corners_ok(decode_small(la, a.width), board)
        ok_r = corners_ok(decode_small(ra, a.width), board)
        caps = [s[c].get("cap_at") for c in (left_cam, right_cam)]
        skew = (caps[1] - caps[0]) if all(caps) else None
        ab = "A=%s" % next(c for c, r in s.items() if r["ab"] == "a")      # 대시보드에서 A 로 고른 카메라(참고)
        if ok_l and ok_r:
            (out / ("L_%02d.jpg" % kept)).write_bytes(la)
            (out / ("R_%02d.jpg" % kept)).write_bytes(ra)
            print("%s  %s  %s  저장 L_%02d/R_%02d  (촬영 시각 차 R−L %s ms)" % (pid, first_time(s)[11:19], ab, kept, kept, skew))
            kept += 1
        else:
            print("%s  %s  %s  제외: 코너 검출 L %s / R %s" % (pid, first_time(s)[11:19], ab, "OK" if ok_l else "실패", "OK" if ok_r else "실패"))
    print("왼쪽 = %s, 오른쪽 = %s | 저장된 쌍: %d → %s" % (left_cam, right_cam, kept, out.resolve()))
    return 0 if kept else 1


if __name__ == "__main__":
    sys.exit(main())
