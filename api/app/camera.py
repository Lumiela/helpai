# -*- coding: utf-8 -*-
"""
카메라 — 스냅샷 저장·조회, 즉시 촬영 대기, 스트리밍 프레임 중계, 12시간 주기 촬영 스케줄 (2026-09-29 오후, 사용자 결정).

  · 카메라 보드는 센서·설비 제어 보드와 별개(계획서 09-28). 한 큐브에 여러 대.
  · 카메라 SN = <큐브 SN>-C<번호> (예: CUBE001-C1). SN 만으로 큐브를 알 수 있어 연결표가 따로 없다.
    카메라 목록은 registry(Supabase cameras 표). 처음 접속(hello/업로드)하면 자동 등록, 화면에서 "카메라 추가"도 가능.
  · 장치 인증 = 인스턴스 브로커 계정(HTTP Basic, main.device_auth). 카메라는 자기 인스턴스의 큐브에만 붙는다.
  · 저장: SNAPSHOT_DIR/<iid>/<cube>/<cam>/<YYYY-MM-DD>/<HHMMSS>_<trigger>.jpg + 같은 이름 .json
    (촬영 시각·트리거·크기·카메라 status·촬영 순간 큐브 텔레메트리 = 모델 학습용 메타). 보관은 무기한(결정 대기 C3).
  · 즉시 촬영: api 가 카메라 notify/config 로 {"snap":1,"req":"<id>"} 를 보내고 업로드 도착을 SNAPSHOT_TIMEOUT 초 기다린다.
  · 주기 촬영: 서버가 SNAPSHOT_TIMES(KST, 기본 "09:00,21:00" — 실제 시각은 결정 대기 C1)마다 모든 카메라에 snap.
  · 스트리밍: 시작 명령 {"stream":1} → 카메라가 프레임마다 POST → 최신 프레임만 메모리 → 시청 라우트가 MJPEG.
    시청자 0 이 STREAM_IDLE_SECONDS(15) 지속되거나 STREAM_MAX_SECONDS(기본 300, 결정 대기 C4) 지나면 {"stream":0}.
    프레임은 저장하지 않는다.
"""
import asyncio
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

SNAPSHOT_DIR = os.environ.get("SNAPSHOT_DIR", "/data/snapshots")
SNAPSHOT_TIMEOUT_S = float(os.environ.get("SNAPSHOT_TIMEOUT", "60"))   # 2026-10-12 사용자 결정: 25 → 60(안정성 우선). 펌웨어 SNAP_REQ_WAIT_MS 와 같아야 함
SNAPSHOT_TIMES = [t.strip() for t in os.environ.get("SNAPSHOT_TIMES", "09:00,21:00").split(",") if t.strip()]
SNAPSHOT_MAX_BYTES = int(os.environ.get("SNAPSHOT_MAX_BYTES", str(6 * 1024 * 1024)))
FRAME_MAX_BYTES = int(os.environ.get("FRAME_MAX_BYTES", str(1024 * 1024)))
STREAM_MAX_S = int(os.environ.get("STREAM_MAX_SECONDS", "300"))
STREAM_IDLE_S = int(os.environ.get("STREAM_IDLE_SECONDS", "15"))
KST = timezone(timedelta(hours=9))

CAM_RE = re.compile(r"^(CUBE\d{3})-C(\d{1,2})$")     # 카메라 SN → (큐브 SN, 번호)
# 2026-10-02: 카메라 status 중 InfluxDB 에 카메라별(cube=<카메라 SN>, kind=status)로 남길 값 (사용자 지시)
HISTORY_FIELDS = ("chip_temp", "rssi_dbm")
FILE_RE = re.compile(r"^\d{6}_(periodic|request|device)\.(jpg|json)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TRIGGERS = ("periodic", "request", "device")

_log = print
_state: Dict[str, dict] = {}          # cam sn -> {"seen", "status", "last_file", "last_taken", "stream": {...}}
_pending: Dict[str, asyncio.Future] = {}   # req id -> Future(파일 정보)
_frames: Dict[str, dict] = {}          # cam sn -> {"data": bytes, "t": float, "seq": int, "cond": asyncio.Condition}
_viewers: Dict[str, int] = {}
stats = {"snapshots": 0, "frames": 0, "bytes": 0, "last_error": None, "scheduled": 0}


def set_logger(fn):
    global _log
    _log = fn


def cube_of(cam_sn: str) -> Optional[str]:
    m = CAM_RE.match(cam_sn)
    return m.group(1) if m else None


def cam_number(cam_sn: str) -> Optional[int]:
    m = CAM_RE.match(cam_sn)
    return int(m.group(2)) if m else None


def _cam(sn: str) -> dict:
    return _state.setdefault(sn, {"seen": None, "status": None, "last_file": None, "last_taken": None,
                                  "stream": {"on": False, "since": None, "until": None, "frames": 0, "last_frame": None}})


def touch(sn: str, status: Optional[dict] = None):
    c = _cam(sn)
    c["seen"] = time.time()
    if status is not None:
        c["status"] = status


def history_values(status) -> dict:
    """카메라 status 에서 InfluxDB 에 기록할 값만 고른다."""
    if not isinstance(status, dict):
        return {}
    return {k: status[k] for k in HISTORY_FIELDS if k in status}


# 즉시 촬영 배율(2026-10-08, 펌웨어 cube-cam-0.6-zoom): 화면 값(배) → 카메라에 보내는 %(정수). 그 촬영 한 번에만 적용.
ZOOM_PCTS = {1.0: 100, 1.5: 150, 2.0: 200, 3.0: 300}


def zoom_pct(zoom) -> Optional[int]:
    """1·1.5·2·3 만 허용. 아니면 None."""
    try:
        return ZOOM_PCTS.get(float(zoom))
    except (TypeError, ValueError):
        return None


def info(sn: str) -> dict:
    c = _cam(sn)
    return {"sn": sn, "cube": cube_of(sn), "number": cam_number(sn), "seen": c["seen"], "status": c["status"],
            "last_file": c["last_file"], "last_taken": c["last_taken"],
            "stream": dict(c["stream"], viewers=_viewers.get(sn, 0))}


# ──────────────────────────────────────────────── 스냅샷 저장·조회

def _dir(iid: str, cube: str, cam: str, day: Optional[str] = None) -> str:
    p = os.path.join(SNAPSHOT_DIR, iid, cube, cam)
    return os.path.join(p, day) if day else p


def store(iid: str, cam: str, data: bytes, trigger: str, meta: dict) -> dict:
    """JPEG 와 사이드카 json 을 저장하고 파일 정보를 돌려준다. 같은 초에 두 장이면 뒤에 -2, -3 …"""
    cube = cube_of(cam)
    if not cube:
        raise ValueError("bad camera sn")
    if trigger not in TRIGGERS:
        trigger = "device"
    now = datetime.now(KST)
    day = now.strftime("%Y-%m-%d")
    d = _dir(iid, cube, cam, day)
    os.makedirs(d, exist_ok=True)
    base = now.strftime("%H%M%S") + "_" + trigger
    name = base + ".jpg"
    n = 1
    while os.path.exists(os.path.join(d, name)):
        n += 1
        name = "%s-%d.jpg" % (base, n)
    path = os.path.join(d, name)
    with open(path, "wb") as f:
        f.write(data)
    side = dict(meta)
    side.update({"instance_id": iid, "cube": cube, "camera": cam, "camera_number": cam_number(cam),
                 "taken_at": now.isoformat(), "trigger": trigger, "bytes": len(data), "file": name, "date": day})
    with open(path[:-4] + ".json", "w", encoding="utf-8") as f:
        json.dump(side, f, ensure_ascii=False, indent=1)
    c = _cam(cam)
    c["seen"] = time.time()
    c["last_file"] = "%s/%s" % (day, name)
    c["last_taken"] = now.isoformat()
    stats["snapshots"] += 1
    stats["bytes"] += len(data)
    return {"camera": cam, "cube": cube, "date": day, "file": name, "path": c["last_file"],
            "taken_at": side["taken_at"], "trigger": trigger, "bytes": len(data), "zoom_pct": side.get("zoom_pct")}


def list_snapshots(iid: str, cube: str, cam: Optional[str] = None, limit: int = 50, date: Optional[str] = None) -> List[dict]:
    """최신순. cam 이 없으면 큐브의 모든 카메라."""
    out = []
    root = os.path.join(SNAPSHOT_DIR, iid, cube)
    if not os.path.isdir(root):
        return out
    cams = [cam] if cam else sorted(os.listdir(root))
    for c in cams:
        cdir = os.path.join(root, c)
        if not os.path.isdir(cdir):
            continue
        days = [date] if date else sorted(os.listdir(cdir), reverse=True)
        for day in days:
            ddir = os.path.join(cdir, day)
            if not DATE_RE.match(day) or not os.path.isdir(ddir):
                continue
            for name in sorted(os.listdir(ddir), reverse=True):
                if not name.endswith(".jpg"):
                    continue
                st = os.stat(os.path.join(ddir, name))
                taken = None
                zoom = None
                jp = os.path.join(ddir, name[:-4] + ".json")
                if os.path.exists(jp):
                    try:
                        with open(jp, encoding="utf-8") as f:
                            side = json.load(f)
                        taken = side.get("taken_at")
                        zoom = side.get("zoom_pct")
                    except Exception:   # noqa: BLE001
                        taken = None
                out.append({"camera": c, "cube": cube, "date": day, "file": name, "path": "%s/%s" % (day, name),
                            "bytes": st.st_size, "taken_at": taken, "zoom_pct": zoom,
                            "trigger": name.split("_", 1)[1].split(".")[0].split("-")[0] if "_" in name else None})
    out.sort(key=lambda r: (r["date"], r["file"]), reverse=True)
    return out[:limit]


def file_path(iid: str, cube: str, cam: str, day: str, name: str) -> Optional[str]:
    """경로 조작 방지: 조각 하나하나 모양을 검사한다."""
    if not (CAM_RE.match(cam) and cube_of(cam) == cube and DATE_RE.match(day)):
        return None
    if not re.match(r"^\d{6}_(periodic|request|device)(-\d+)?\.(jpg|json)$", name):
        return None
    p = os.path.join(_dir(iid, cube, cam, day), name)
    return p if os.path.isfile(p) else None


# ──────────────────────────────────────────────── 즉시 촬영 대기

def new_request_id() -> str:
    return secrets.token_hex(6)


def wait_for(req: str, loop: asyncio.AbstractEventLoop) -> asyncio.Future:
    fut = loop.create_future()
    _pending[req] = fut
    return fut


def resolve(req: Optional[str], result: dict) -> bool:
    if not req:
        return False
    fut = _pending.pop(req, None)
    if fut is None or fut.done():
        return False
    fut.set_result(result)
    return True


def drop(req: str):
    _pending.pop(req, None)


# ──────────────────────────────────────────────── 스트리밍 프레임 중계

def _hub(sn: str) -> dict:
    h = _frames.get(sn)
    if h is None:
        h = {"data": None, "t": None, "seq": 0, "cond": asyncio.Condition()}
        _frames[sn] = h
    return h


async def push_frame(sn: str, data: bytes):
    h = _hub(sn)
    async with h["cond"]:
        h["data"] = data
        h["t"] = time.time()
        h["seq"] += 1
        h["cond"].notify_all()
    c = _cam(sn)
    c["seen"] = h["t"]
    c["stream"]["frames"] += 1
    c["stream"]["last_frame"] = h["t"]
    stats["frames"] += 1


async def next_frame(sn: str, after_seq: int, timeout: float = 10.0):
    """after_seq 보다 새 프레임을 기다린다. 없으면 None (keepalive 용)."""
    h = _hub(sn)
    async with h["cond"]:
        if h["seq"] > after_seq and h["data"] is not None:
            return h["seq"], h["data"]
        try:
            await asyncio.wait_for(h["cond"].wait_for(lambda: h["seq"] > after_seq), timeout)
        except asyncio.TimeoutError:
            return None
        return h["seq"], h["data"]


def viewer_enter(sn: str) -> int:
    _viewers[sn] = _viewers.get(sn, 0) + 1
    return _viewers[sn]


def viewer_leave(sn: str) -> int:
    _viewers[sn] = max(0, _viewers.get(sn, 0) - 1)
    return _viewers[sn]


def stream_mark(sn: str, on: bool):
    s = _cam(sn)["stream"]
    now = time.time()
    if on:
        s.update({"on": True, "since": now, "until": now + STREAM_MAX_S, "frames": 0})
    else:
        s.update({"on": False, "until": None})


def stream_should_stop(sn: str) -> Optional[str]:
    """켜져 있는데 끌 이유가 있으면 그 이유. 없으면 None."""
    s = _cam(sn)["stream"]
    if not s["on"]:
        return None
    now = time.time()
    if s["until"] and now >= s["until"]:
        return "max_time"
    if _viewers.get(sn, 0) == 0 and s["since"] and now - s["since"] > STREAM_IDLE_S:
        last = s.get("_viewer_left_at") or s["since"]
        if now - last > STREAM_IDLE_S:
            return "no_viewers"
    return None


def note_viewer_left(sn: str):
    _cam(sn)["stream"]["_viewer_left_at"] = time.time()


# ──────────────────────────────────────────────── 주기 촬영 스케줄

def due_now(now: Optional[datetime] = None, last_fired: Optional[str] = None) -> Optional[str]:
    """이번 분이 SNAPSHOT_TIMES 중 하나면 그 시각 키('2026-09-29 09:00'), 이미 쐈으면 None."""
    now = now or datetime.now(KST)
    hm = now.strftime("%H:%M")
    if hm not in SNAPSHOT_TIMES:
        return None
    key = now.strftime("%Y-%m-%d ") + hm
    return None if key == last_fired else key


def status():
    return dict(stats, cameras=len(_state), streaming=[sn for sn, c in _state.items() if c["stream"]["on"]],
                snapshot_dir=SNAPSHOT_DIR, times=SNAPSHOT_TIMES, timeout_s=SNAPSHOT_TIMEOUT_S,
                stream_max_s=STREAM_MAX_S, pending=len(_pending))
