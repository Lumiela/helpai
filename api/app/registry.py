# -*- coding: utf-8 -*-
"""
큐브·인스턴스 등록부 (2026-09-29 오후, 다중 인스턴스 · 게이트웨이 자동 등록).

  · Supabase 표 instances(id, owner_id, name) · cubes(sn, instance_id) · instance_secrets(instance_id, broker_password)
    를 **기동 때 한 번 읽어** 메모리에 둔다(비밀은 안 읽는다). REGISTRY_RELOAD_SECONDS(기본 60초)마다 다시 읽고,
    등록·추가 직후에도 다시 읽는다. 요청마다 Supabase 에 묻지 않는다(사용자 결정 J54 와 같은 원칙).
  · 회원가입·장치 추가는 **게이트웨이(api)가 자동 처리**(사용자 결정 2026-09-29 오후): 운영자 사전 등록 없음.
    - 모르는 SN 이면 새 인스턴스(i-<8자 무작위>)와 큐브를 만들고 소유자를 채운다.
    - 이미 있는 SN 은 거절(소유자가 비어 있는 옛 사전 등록분만 넘겨받는다).
  · 인스턴스당 브로커 계정 하나(사용자명 = 인스턴스 ID). 비밀번호는 instance_secrets 에만(서비스 키 전용, RLS 정책 없음).
  · Supabase 가 없을 때(시험·AUTH_MODE=off)는 INSTANCES_STATIC="jetson-01:CUBE001,CUBE002;jetson-02:CUBE003"
    으로 고정 등록부를 쓰고, 등록·비밀은 메모리에서만 처리한다.
  · 2026-10-06 자동 제어: cubes 표의 crop_id · auto_mode(SQL v6)도 함께 읽는다(같은 SQL 로 만든 auto_irrigation 칸은
    쓰지 않는다 — 급수는 자동 제어에 포함, 사용자 정정 2026-10-06 저녁). 칸이 아직 없으면
    예전 두 칸만 읽고(자동 제어 설정 불가) 등록부는 그대로 동작한다. 화면·사람 조작으로 바꾼 값은 메모리에 먼저
    반영하고 Supabase 에 쓴다. 쓰기에 실패하면 덮어쓰기 값(_overrides)으로 유지하고 재적재 때마다 다시 쓴다
    (사람 조작으로 자동을 끈 것이 Supabase 장애 때문에 재적재로 되살아나지 않게).
"""
import asyncio
import os
import secrets
import threading
import time
from typing import Dict, List, Optional

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
RELOAD_S = int(os.environ.get("REGISTRY_RELOAD_SECONDS", "60") or "60")
STATIC = os.environ.get("INSTANCES_STATIC", "").strip()
ENABLED = bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)
TIMEOUT_S = 8.0

_lock = threading.Lock()
_instances: Dict[str, dict] = {}     # iid -> {"owner_id": str|None, "name": str|None}
_cubes: Dict[str, str] = {}          # sn -> iid
_secrets: Dict[str, str] = {}        # 고정 등록부(시험)에서만 씀
_cameras: Dict[str, dict] = {}       # cam sn -> {"cube": sn, "instance_id": iid, "name": str|None}
_secret_cache: Dict[str, tuple] = {} # iid -> (password, expires)  장치 인증(HTTP Basic)용, SECRET_CACHE_S
SECRET_CACHE_S = int(os.environ.get("SECRET_CACHE_SECONDS", "300") or "300")
SETTING_KEYS = ("crop_id", "auto_mode")
SETTING_DEFAULTS = {"crop_id": None, "auto_mode": False}
_settings: Dict[str, dict] = {}      # sn -> 자동 제어 설정(Supabase 값)
_overrides: Dict[str, dict] = {}     # sn -> Supabase 에 아직 못 쓴 값(이게 우선)
stats = {"source": "static" if STATIC else ("supabase" if ENABLED else "none"),
         "loaded_at": None, "loads": 0, "failed": 0, "last_error": None,
         "auto_columns": None if ENABLED and not STATIC else True}
_client = None
_task: Optional[asyncio.Task] = None
_log = print
_on_reload = []   # 재적재 뒤 부를 콜백(브로커 계정 확인 등)


def set_logger(fn):
    global _log
    _log = fn


def on_reload(fn):
    _on_reload.append(fn)


# ──────────────────────────────────────────────── 조회 (요청 경로에서 씀)

def instance(iid: str) -> Optional[dict]:
    with _lock:
        d = _instances.get(iid)
        return dict(d) if d is not None else None


def ids() -> List[str]:
    with _lock:
        return sorted(_instances)


def instance_of(sn: str) -> Optional[str]:
    with _lock:
        return _cubes.get(sn)


def cubes_of(iid: str) -> List[str]:
    with _lock:
        return sorted(sn for sn, i in _cubes.items() if i == iid)


def camera(sn: str) -> Optional[dict]:
    with _lock:
        d = _cameras.get(sn)
        return dict(d) if d else None


def cameras_of(iid: str, cube: Optional[str] = None) -> List[str]:
    with _lock:
        return sorted(sn for sn, d in _cameras.items() if d["instance_id"] == iid and (cube is None or d["cube"] == cube))


def all_cameras() -> List[str]:
    with _lock:
        return sorted(_cameras)


def cube_settings(sn: str) -> dict:
    """자동 제어 설정. 덮어쓰기 값이 있으면 그것이 우선."""
    with _lock:
        d = dict(SETTING_DEFAULTS)
        d.update(_settings.get(sn) or {})
        d.update(_overrides.get(sn) or {})
        return d


def auto_cubes() -> List[tuple]:
    """자동 제어가 켜진 큐브: [(iid, sn, 설정)]."""
    with _lock:
        sns = sorted(_cubes)
    out = []
    for sn in sns:
        s = cube_settings(sn)
        iid = instance_of(sn)
        if s["auto_mode"] and iid:
            out.append((iid, sn, s))
    return out


def auto_columns() -> bool:
    """cubes 표에 자동 제어 칸(SQL v6)이 있는지. 고정 등록부(시험)는 항상 True."""
    return bool(stats["auto_columns"])


async def _patch_cube(sn: str, fields: dict):
    _raise(await _http().patch(SUPABASE_URL + "/rest/v1/cubes", params={"sn": "eq." + sn}, json=fields,
                               headers={"Prefer": "return=minimal", "Content-Type": "application/json"}))


def override_local(sn: str, **fields):
    """메모리에만 즉시 반영(기다리지 않음). 사람 조작으로 자동을 끌 때 — 저장은 update_cube_settings 가 뒤따른다."""
    fields = {k: v for k, v in fields.items() if k in SETTING_KEYS}
    with _lock:
        _overrides.setdefault(sn, {}).update(fields)


async def update_cube_settings(sn: str, keep_on_fail: bool = False, **fields) -> dict:
    """메모리에 먼저 반영하고 Supabase 에 쓴다. 쓰기에 실패하면 예외를 다시 던진다.
    keep_on_fail=True(사람 조작으로 자동 끔)면 덮어쓰기 값을 남겨 재적재 때 다시 쓰고, 아니면(화면 설정) 되돌린다."""
    fields = {k: v for k, v in fields.items() if k in SETTING_KEYS}
    with _lock:
        before = dict(_overrides.get(sn) or {})
        _overrides.setdefault(sn, {}).update(fields)
    if not ENABLED or STATIC:
        with _lock:
            _settings.setdefault(sn, {}).update(_overrides.pop(sn, {}))
        return cube_settings(sn)
    try:
        await _patch_cube(sn, fields)
    except Exception:
        if not keep_on_fail:
            with _lock:
                if before:
                    _overrides[sn] = before
                else:
                    _overrides.pop(sn, None)
        raise
    with _lock:
        _settings.setdefault(sn, {}).update(fields)
        ov = _overrides.get(sn, {})
        for k, v in fields.items():
            if ov.get(k) == v:
                ov.pop(k, None)
        if not ov:
            _overrides.pop(sn, None)
    return cube_settings(sn)


async def flush_overrides():
    """재적재 뒤: 아직 못 쓴 값을 다시 쓴다."""
    with _lock:
        pending = {sn: dict(v) for sn, v in _overrides.items()}
    for sn, fields in pending.items():
        try:
            await _patch_cube(sn, fields)
            with _lock:
                _settings.setdefault(sn, {}).update(fields)
                if _overrides.get(sn) == fields:
                    _overrides.pop(sn, None)
            _log("[registry] %s 자동 제어 설정 뒤늦게 저장: %s" % (sn, fields))
        except Exception as e:   # noqa: BLE001
            _log("[registry] %s 자동 제어 설정 저장 재시도 실패: %s" % (sn, str(e)[:120]))


def new_instance_id() -> str:
    while True:
        iid = "i-" + secrets.token_hex(4)
        if instance(iid) is None:
            return iid


def status():
    with _lock:
        out = dict(stats)
        out["instances"] = len(_instances)
        out["cubes"] = len(_cubes)
        out["cameras"] = len(_cameras)
        return out


# ──────────────────────────────────────────────── 적재

def _apply(instances: Dict[str, dict], cubes: Dict[str, str], cameras: Optional[Dict[str, dict]] = None,
           settings: Optional[Dict[str, dict]] = None):
    with _lock:
        _instances.clear()
        _instances.update(instances)
        _cubes.clear()
        _cubes.update(cubes)
        if cameras is not None:
            _cameras.clear()
            _cameras.update(cameras)
        if settings is not None:
            _settings.clear()
            _settings.update(settings)
        stats["loaded_at"] = time.time()
        stats["loads"] += 1


def set_static(spec: str):
    """'jetson-01:CUBE001,CUBE002;jetson-02:CUBE003' 꼴. 소유자는 없음(시험에서는 토큰이 정한다)."""
    instances, cubes = {}, {}
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        iid, _, sns = part.partition(":")
        iid = iid.strip()
        if not iid:
            continue
        instances[iid] = {"owner_id": None, "name": None}
        for sn in sns.split(","):
            sn = sn.strip()
            if sn:
                cubes[sn] = iid
    _apply(instances, cubes, {}, {})
    _overrides.clear()
    _secrets.clear()
    _secret_cache.clear()


def _http():
    global _client
    if _client is None:
        import httpx
        _client = httpx.AsyncClient(timeout=TIMEOUT_S,
                                    headers={"apikey": SUPABASE_SERVICE_KEY,
                                             "Authorization": "Bearer " + SUPABASE_SERVICE_KEY})
    return _client


def _raise(r):
    if r.status_code >= 300:
        raise RuntimeError("HTTP %d %s" % (r.status_code, r.text[:120]))
    return r


async def load() -> bool:
    """Supabase 에서 다시 읽는다. 실패하면 이전 내용을 유지하고 False."""
    if not ENABLED:
        return bool(_instances)
    try:
        r1 = _raise(await _http().get(SUPABASE_URL + "/rest/v1/instances", params={"select": "id,owner_id,name"}))
        r2 = await _http().get(SUPABASE_URL + "/rest/v1/cubes", params={"select": "sn,instance_id," + ",".join(SETTING_KEYS)})
        if r2.status_code >= 300:
            # SQL v6 전: 자동 제어 칸이 없다 → 예전 두 칸만 읽는다(등록부는 그대로 동작)
            if stats["auto_columns"] is not False:
                _log("[registry] cubes 표에 자동 제어 칸 없음(HTTP %d) — SQL v6 실행 전. 자동 제어 설정 불가" % r2.status_code)
            stats["auto_columns"] = False
            r2 = _raise(await _http().get(SUPABASE_URL + "/rest/v1/cubes", params={"select": "sn,instance_id"}))
        else:
            stats["auto_columns"] = True
        r3 = await _http().get(SUPABASE_URL + "/rest/v1/cameras", params={"select": "sn,cube_sn,instance_id,name"})
        instances = {row["id"]: {"owner_id": row.get("owner_id"), "name": row.get("name")}
                     for row in r1.json() if isinstance(row.get("id"), str)}
        rows2 = r2.json()
        cubes = {row["sn"]: row["instance_id"] for row in rows2
                 if isinstance(row.get("sn"), str) and isinstance(row.get("instance_id"), str)
                 and row["instance_id"] in instances}
        settings = {row["sn"]: {"crop_id": row.get("crop_id"), "auto_mode": row.get("auto_mode") is True}
                    for row in rows2 if row.get("sn") in cubes}
        cameras = None
        if r3.status_code < 300:
            cameras = {row["sn"]: {"cube": row["cube_sn"], "instance_id": row["instance_id"], "name": row.get("name")}
                       for row in r3.json() if isinstance(row.get("sn"), str) and row.get("instance_id") in instances}
        else:
            _log("[registry] cameras 표 없음/실패 (HTTP %d) — 카메라 등록부 비움" % r3.status_code)
        _apply(instances, cubes, cameras if cameras is not None else {}, settings)
        stats["last_error"] = None
        if _overrides:
            await flush_overrides()
        for fn in _on_reload:
            try:
                fn()
            except Exception as e:   # noqa: BLE001
                _log("[registry] 재적재 콜백 실패: %s" % str(e)[:120])
        return True
    except Exception as e:   # noqa: BLE001
        stats["failed"] += 1
        stats["last_error"] = str(e)[:200]
        _log("[registry] 적재 실패: %s" % str(e)[:160])
        return False


# ──────────────────────────────────────────────── 쓰기 (서비스 키)

async def claim(iid: str, user_uuid: str) -> bool:
    """소유자가 비어 있을 때만 채운다(옛 사전 등록분 넘겨받기). 성공하면 True."""
    if not ENABLED:
        with _lock:
            d = _instances.get(iid)
            if d is None or d.get("owner_id"):
                return False
            d["owner_id"] = user_uuid
        return True
    r = _raise(await _http().patch(
        SUPABASE_URL + "/rest/v1/instances",
        params={"id": "eq." + iid, "owner_id": "is.null"},
        json={"owner_id": user_uuid},
        headers={"Prefer": "return=representation", "Content-Type": "application/json"}))
    if not r.json():
        return False
    await load()
    return True


async def create_instance(owner_uuid: str, sn: str, name: Optional[str] = None) -> str:
    """새 인스턴스 + 첫 큐브. 돌려주는 값 = 인스턴스 ID. SN 이 이미 있으면 RuntimeError('duplicate')."""
    iid = new_instance_id()
    if not ENABLED:
        with _lock:
            if sn in _cubes:
                raise RuntimeError("duplicate")
            _instances[iid] = {"owner_id": owner_uuid, "name": name}
            _cubes[sn] = iid
        return iid
    _raise(await _http().post(SUPABASE_URL + "/rest/v1/instances",
                              json={"id": iid, "owner_id": owner_uuid, "name": name},
                              headers={"Prefer": "return=minimal", "Content-Type": "application/json"}))
    r = await _http().post(SUPABASE_URL + "/rest/v1/cubes", json={"sn": sn, "instance_id": iid},
                           headers={"Prefer": "return=minimal", "Content-Type": "application/json"})
    if r.status_code == 409:
        # 동시에 같은 SN 을 넣은 경우: 방금 만든 빈 인스턴스는 지운다
        await _http().delete(SUPABASE_URL + "/rest/v1/instances", params={"id": "eq." + iid})
        raise RuntimeError("duplicate")
    _raise(r)
    await load()
    return iid


async def add_cube(iid: str, sn: str) -> None:
    """기존 인스턴스에 큐브 추가. SN 이 이미 있으면 RuntimeError('duplicate')."""
    if not ENABLED:
        with _lock:
            if sn in _cubes:
                raise RuntimeError("duplicate")
            _cubes[sn] = iid
        return
    r = await _http().post(SUPABASE_URL + "/rest/v1/cubes", json={"sn": sn, "instance_id": iid},
                           headers={"Prefer": "return=minimal", "Content-Type": "application/json"})
    if r.status_code == 409:
        raise RuntimeError("duplicate")
    _raise(r)
    await load()


async def add_camera(iid: str, cam_sn: str, cube: str, name: Optional[str] = None) -> None:
    """카메라 등록(자동 또는 화면). 이미 있으면 RuntimeError('duplicate')."""
    if not ENABLED:
        with _lock:
            if cam_sn in _cameras:
                raise RuntimeError("duplicate")
            _cameras[cam_sn] = {"cube": cube, "instance_id": iid, "name": name}
        return
    r = await _http().post(SUPABASE_URL + "/rest/v1/cameras", json={"sn": cam_sn, "cube_sn": cube, "instance_id": iid, "name": name},
                           headers={"Prefer": "return=minimal", "Content-Type": "application/json"})
    if r.status_code == 409:
        raise RuntimeError("duplicate")
    _raise(r)
    await load()


async def secret_cached(iid: str) -> Optional[str]:
    """장치 인증용. SECRET_CACHE_S 동안 메모리에 둔다(스트리밍 프레임마다 Supabase 를 부르지 않게)."""
    ent = _secret_cache.get(iid)
    now = time.time()
    if ent and ent[1] > now:
        return ent[0]
    pw = await get_secret(iid)
    _secret_cache[iid] = (pw, now + SECRET_CACHE_S)
    return pw


def secret_forget(iid: str):
    _secret_cache.pop(iid, None)


async def get_secret(iid: str) -> Optional[str]:
    """브로커 비밀번호. 없으면 None. 캐시하지 않는다(요청 때만)."""
    if not ENABLED:
        return _secrets.get(iid)
    r = _raise(await _http().get(SUPABASE_URL + "/rest/v1/instance_secrets",
                                 params={"select": "broker_password", "instance_id": "eq." + iid}))
    rows = r.json()
    return rows[0].get("broker_password") if rows else None


async def set_secret(iid: str, password: str) -> None:
    _secret_cache.pop(iid, None)
    if not ENABLED:
        _secrets[iid] = password
        return
    _raise(await _http().post(SUPABASE_URL + "/rest/v1/instance_secrets",
                              json={"instance_id": iid, "broker_password": password},
                              headers={"Prefer": "resolution=merge-duplicates,return=minimal",
                                       "Content-Type": "application/json"}))


async def _reload_loop():
    while True:
        await asyncio.sleep(RELOAD_S)
        await load()


def start(loop_log=None):
    """이벤트 루프 안에서 호출. 고정 등록부면 그것을, 아니면 Supabase 를 읽고 주기 재적재를 시작한다."""
    global _task
    if loop_log:
        set_logger(loop_log)
    if STATIC:
        set_static(STATIC)
        return
    if ENABLED and RELOAD_S > 0:
        _task = asyncio.get_running_loop().create_task(_reload_loop())


async def close():
    global _client, _task
    if _task is not None:
        _task.cancel()
        _task = None
    if _client is not None:
        try:
            await _client.aclose()
        finally:
            _client = None
