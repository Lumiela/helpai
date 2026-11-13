#!/usr/bin/env python3
"""leafdx_request: 네이버 서버에서 사진을 젯슨 판정 창구로 보내고 결과를 저장한다 — 2026-11-06 랩탑 ClaudeCode

  python3 /data/tools/leafdx_request.py <사진 경로> [<사진 경로> ...]
  python3 /data/tools/leafdx_request.py --pending [--since 2026-11-06] [--limit 8]   # 결과가 아직 없는 스냅샷만

저장 위치(2026-11-06 사용자 결정: 별도 폴더에 JSON + 시각화):
  /data/snapshots/<상대 경로>/<이름>.jpg  →  /data/leafdx/<상대 경로>/<이름>.json , <이름>_vis.jpg
  스냅샷 폴더 밖의 사진은 /data/leafdx/_adhoc/ 아래.
스냅샷 폴더에는 아무것도 쓰지 않는다(관제 화면의 사진 목록에 섞이지 않게).

젯슨은 요청 때만 모델을 올리므로 한 장에 1분 안팎 걸린다. 한 번에 한 건만 처리한다.
외부 의존성 없음(표준 라이브러리만). 운영 중인 api 컨테이너와 무관하게 호스트에서 실행한다.
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

URL = os.environ.get("LEAFDX_URL", "http://100.96.221.46:8001")       # 젯슨 Tailscale 주소(직결)
SNAP_ROOT = Path(os.environ.get("SNAPSHOT_DIR", "/data/snapshots"))
OUT_ROOT = Path(os.environ.get("LEAFDX_OUT", "/data/leafdx"))
KEY_FILE = Path(os.environ.get("LEAFDX_KEY_FILE", str(Path.home() / ".leafdx" / "api_key")))
TIMEOUT_S = int(os.environ.get("LEAFDX_TIMEOUT", "420"))


def out_base(photo: Path) -> Path:
    try:
        rel = photo.resolve().relative_to(SNAP_ROOT.resolve())
        return OUT_ROOT / rel.parent / photo.stem
    except ValueError:
        return OUT_ROOT / "_adhoc" / photo.stem


def request_one(photo: Path, key: str) -> dict:
    req = urllib.request.Request(
        URL + "/infer", data=photo.read_bytes(), method="POST",
        headers={"Content-Type": "application/octet-stream", "X-Api-Key": key, "X-Filename": photo.name},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error": "http %d" % e.code}
    except Exception as e:  # 연결 실패·시간 초과
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def save(photo: Path, resp: dict) -> Path:
    base = out_base(photo)
    base.parent.mkdir(parents=True, exist_ok=True)
    vis = resp.get("vis_jpg_b64")
    record = {
        "source": str(photo),
        "requested_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "elapsed_s": resp.get("elapsed_s"),
    }
    record.update(resp["result"])
    Path(str(base) + ".json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    if vis:
        Path(str(base) + "_vis.jpg").write_bytes(base64.b64decode(vis))
    return base


def pending(since, limit):
    todo = []
    for p in SNAP_ROOT.rglob("*.jpg"):
        if since and p.parent.name < since:      # 날짜 폴더 이름(YYYY-MM-DD) 비교
            continue
        if not Path(str(out_base(p)) + ".json").exists():
            todo.append(p)
    todo.sort(key=lambda p: p.stat().st_mtime)
    return todo[-limit:] if limit else todo


def main() -> int:
    ap = argparse.ArgumentParser(description="사진을 젯슨 판정 창구로 보내고 결과를 저장")
    ap.add_argument("photos", nargs="*", help="사진 경로")
    ap.add_argument("--pending", action="store_true", help="결과가 아직 없는 스냅샷을 처리")
    ap.add_argument("--since", help="--pending 일 때 이 날짜(YYYY-MM-DD) 이후만")
    ap.add_argument("--limit", type=int, default=8, help="--pending 일 때 최근 몇 장까지(기본 8, 0=전부)")
    a = ap.parse_args()

    photos = [Path(p) for p in a.photos]
    if a.pending:
        photos += pending(a.since, a.limit)
    if not photos:
        ap.error("사진 경로를 주거나 --pending 을 쓰세요")
    key = KEY_FILE.read_text().strip()

    failed = 0
    for p in photos:
        if not p.is_file():
            print("%s  없음" % p, flush=True)
            failed += 1
            continue
        resp = request_one(p, key)
        if not resp.get("ok"):
            print("%s  실패: %s" % (p.name, json.dumps({k: v for k, v in resp.items() if k != "ok"}, ensure_ascii=False)[:400]), flush=True)
            failed += 1
            if resp.get("error") in ("memory low", "unauthorized"):
                break                                # 계속 보내 봐야 같은 결과
            continue
        base = save(p, resp)
        r = resp["result"]
        print("%s  잎 %s  %s  %ss  → %s.json" % (p.name, len(r.get("leaves", [])), r.get("counts"), resp.get("elapsed_s"), base), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
