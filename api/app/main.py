# -*- coding: utf-8 -*-
"""
스마트큐브 API — 큐브 소유 고객들의 인스턴스 백엔드, 다중 인스턴스 · 게이트웨이 자동 등록 · 카메라 (v0.6, 2026-09-29 오후).

    브라우저 --HTTPS(Funnel)--> nginx(web) /i/<iid>/api/ --> 이 서비스(api:8000) /i/<iid>/...
                                          /api/          -->                      /health, /me, /claim
                                                        ├─ mosquitto:1883  원본 토픽 구독(현재값·상태·응답), 제어·설정 발행
                                                        ├─ influxdb:8086   이력 기록(writer) + 이력 조회
                                                        └─ Supabase        JWT 검증(키만) · 등록부(instances·cubes, 기동 때) · 조작 기록(audit)

이용자 모델 (사용자 결정 2026-09-29, J56~J58 + 오후 판정):
  · api 하나가 인스턴스 여러 개를 맡는다. 인스턴스 ID 는 경로 /i/{iid}/ 에서. 컨테이너는 하나.
  · 인스턴스의 이용자는 **큐브 소유자**. 소유자는 조회·제어·보정 전부. 아니면 보기도 403.
  · 큐브(SN)→인스턴스, 인스턴스→소유자는 Supabase 표(cubes, instances). **게이트웨이가 자동 등록**(운영자 사전 등록 없음):
    가입자가 큐브 SN(CUBE+숫자 3자리)을 넣으면(POST /claim) 새 인스턴스(i-xxxxxxxx)·큐브·브로커 계정을 만들고
    소유자가 된다. 이미 있는 SN 은 거절(소유자가 비어 있는 옛 사전 등록분만 넘겨받음). 장치 추가는 POST /i/{iid}/cubes.
  · 브로커 계정은 인스턴스당 하나(사용자명 = 인스턴스 ID, mosquitto 동적 보안 플러그인, app/broker_admin.py).
    소유자는 GET /i/{iid}/broker 로 계정·비밀번호·주소를 보고, POST /i/{iid}/broker/rotate 로 재발급.
  · 운영자(서비스 제공자)는 마스터 키 + 사유로 인스턴스별 **읽기 전용** 접근(짧은 시간). 전부 audit 에 남김.

라우트:
    공개     GET  /health
    전역     GET  /me                                내 소유 인스턴스 목록·운영자 여부·살아 있는 읽기 접근
             POST /claim {sn}                         큐브 SN 으로 인스턴스 자동 생성·소유 등록 → {instance_id}
    인스턴스 GET  /i/{iid}/me                         이 인스턴스에 대한 접근(owner/operator/none)·큐브 목록
             POST /i/{iid}/operator/access            {master_key, reason} → 읽기 접근 발급   GET 상태 / DELETE 반납
             GET  /i/{iid}/instance/status, /cubes, /cubes/{sn}/values|status|history|stream   (소유자 또는 운영자 읽기)
             POST /i/{iid}/cubes/{sn}/control|refresh|config                                   (소유자만)
             POST /i/{iid}/cubes {sn}                 큐브 추가 · GET /i/{iid}/broker 계정 · POST /i/{iid}/broker/rotate  (소유자만)
    카메라   장치(HTTP Basic = 인스턴스 브로커 계정): POST /dev/cameras/hello · POST /dev/snapshots?cam&trigger&req (JPEG 본문)
             · POST /dev/frames?cam (스트리밍 프레임 JPEG)
             사람: GET /i/{iid}/cameras · POST /i/{iid}/cameras {cube, number} · GET /i/{iid}/cubes/{sn}/snapshots[?cam&date&limit]
             · GET /i/{iid}/cubes/{sn}/snapshots/{cam}/{day}/{file} · POST /i/{iid}/cubes/{sn}/snapshot {cam}(즉시 촬영, 소유자)
             · POST /i/{iid}/cameras/{cam}/stream/start|stop (소유자) · GET /i/{iid}/cameras/{cam}/stream (MJPEG, 소유자·운영자 읽기)
             카메라 SN = <큐브 SN>-C<번호>. 12시간 주기 촬영은 서버가 SNAPSHOT_TIMES 마다 snap 명령(app/camera.py).
    자동 제어 GET  /i/{iid}/crops                     작물표(app/crops.json)          (소유자 또는 운영자 읽기)
    (v0.7)   GET  /i/{iid}/cubes/{sn}/auto            작물·켜짐·지금 걸린 규칙·센서 점검·이탈 상태
             PUT  /i/{iid}/cubes/{sn}/crop {crop_id}  작물 배정(null = 해제, 자동도 꺼짐)          (소유자만)
             PUT  /i/{iid}/cubes/{sn}/auto {enabled}  자동 제어 켜기/끄기(급수 포함, 작물 없으면 409)
             규칙은 app/autoctl.py. 사람이 /control 로 조작하면 그 큐브의 자동 제어가 꺼진다.

기록·통신(09-28 그대로):
  · 원본 토픽을 받는 김에 InfluxDB 에 직접 기록(app/writer.py). 저장 형식은 telegraf 와 동일.
  · 제어 명령은 펌웨어 규칙대로 7개 키(session, seq, pump, led1, led2, fan1, fan2). 세션·순번 규칙 동일.
  · 로그 시각은 KST.
"""
import asyncio
import json
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

import paho.mqtt.client as mqtt
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import base64
import hmac

from fastapi import Request
from fastapi.responses import FileResponse

from . import agframe, audit, auth, autoctl, broker_admin, camera, crops, registry, report, writer

VERSION = "0.9.2"   # 2026-10-08: 즉시 촬영 배율(zoom) — 펌웨어 cube-cam-0.6-zoom 시험용 · 2026-10-12: 즉시 촬영 대기 기본 25 → 60초

# ══════════════════════════════════════════════ 설정 (환경변수, 젯슨 /data/.env 와 compose 에서)
BROKER_HOST = os.environ.get("BROKER_HOST", "mosquitto")
BROKER_PORT = int(os.environ.get("BROKER_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
AG_APP = os.environ.get("AG_APP", "ag.smartcube.esp32")

INFLUX_URL = os.environ.get("INFLUX_URL", "http://influxdb:8086")
INFLUX_READ_TOKEN = os.environ.get("INFLUX_READ_TOKEN", "").strip() or os.environ.get("INFLUX_TOKEN", "").strip()
INFLUX_ORG = os.environ.get("INFLUX_ORG", "smartcube")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "sensors")

ACK_TIMEOUT_S = float(os.environ.get("ACK_TIMEOUT", "5"))
SSE_KEEPALIVE_S = 15

SN_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
SN_REGISTER_RE = re.compile(r"^CUBE\d{3}$")      # 등록·추가 때 받는 형식(사용자 결정 09-29 오후: CUBE + 숫자 3자리)
IID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
FIELD_RE = re.compile(r"^[a-z0-9_]{1,40}$")
RANGE_RE = re.compile(r"^-\d{1,5}[smhd]$")     # Flux 에 그대로 들어가므로 모양을 제한한다
EVERY_RE = re.compile(r"^\d{1,4}[smhd]$")       # 집계 창(aggregateWindow every). 예: 5m
MAX_FIELDS = 12

KST = timezone(timedelta(hours=9))
STARTED = time.time()


def log(*a):
    print(datetime.now(KST).strftime("%m-%d %H:%M:%S"), *a, flush=True)


audit.set_logger(log)
registry.set_logger(log)
broker_admin.set_logger(log)
camera.set_logger(log)
autoctl.set_logger(log)
report.set_logger(log)


# ══════════════════════════════════════════════ 상태
_state = {}
_state_lock = threading.Lock()
_pending = {}            # 제어 명령 응답 대기: (sn, seq) -> Future
_pending_status = {}     # 설정 프레임 뒤 status 대기: sn -> (Future, 측정 완료까지 기다릴지)
_subs = {}               # SSE 구독자: sn -> set(Queue)
_loop: Optional[asyncio.AbstractEventLoop] = None
_mqtt: Optional[mqtt.Client] = None
_mqtt_connected = False
_rx_count = {"public": 0, "status": 0, "ack": 0, "bad": 0}


def _cube(sn):
    return _state.setdefault(sn, {"session": None, "last_seq": 0, "values": None,
                                  "status": None, "seen": None, "values_at": None})


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _push_to_subscribers(sn, item):
    if _loop is None:
        return
    for q in list(_subs.get(sn, ())):
        _loop.call_soon_threadsafe(q.put_nowait, item)


def _resolve_ack(sn, seq, result):
    key = (sn, int(seq))
    fut = _pending.get(key)
    if fut is None or _loop is None:
        return
    def _set():
        if not fut.done():
            fut.set_result(result)
    _loop.call_soon_threadsafe(_set)


def _resolve_status(sn, obj):
    ent = _pending_status.get(sn)
    if ent is None or _loop is None:
        return
    fut, wait_measured = ent
    if wait_measured and obj.get("cal_pending"):
        return
    def _set():
        if not fut.done():
            fut.set_result(obj)
    _loop.call_soon_threadsafe(_set)


# ══════════════════════════════════════════════ MQTT (paho 네트워크 스레드)
def _on_connect(client, userdata, flags, reason_code, properties=None):
    global _mqtt_connected
    if reason_code != 0:
        log("브로커 접속 실패:", reason_code)
        return
    _mqtt_connected = True
    for kind in ("public", "status", "ack"):
        t = "device/%s/+/%s" % (AG_APP, kind)
        client.subscribe(t, qos=0)
        log("구독:", t)
    group = "device/%s" % AG_APP
    client.publish(group, b"", qos=0, retain=False)
    log("status 요청 발행:", group)


def _on_disconnect(client, userdata, flags, reason_code, properties=None):
    global _mqtt_connected
    _mqtt_connected = False
    log("브로커 연결 끊김:", reason_code)


def _on_message(client, userdata, msg):
    parts = msg.topic.split("/")
    if len(parts) != 4:
        return
    _, app, sn, kind = parts
    try:
        obj, _opts = agframe.decode(msg.payload)
    except agframe.FrameError as e:
        _rx_count["bad"] += 1
        log("해석 실패 [%s] %s" % (msg.topic, e))
        return
    if kind in _rx_count:
        _rx_count[kind] += 1

    now = time.time()
    if camera.cube_of(sn):
        # 카메라 보드(SN = <큐브>-C<n>): status 만 카메라 상태에 두고 큐브 상태에는 넣지 않는다.
        # InfluxDB 에는 칩 온도·신호 세기만 카메라 SN 으로 기록한다(2026-10-02, camera.HISTORY_FIELDS)
        camera.touch(sn, obj if kind == "status" else None)
        if kind == "status":
            writer.record(sn, kind, camera.history_values(obj), now)
            log("카메라 status %s: %s" % (sn, json.dumps({k: obj.get(k) for k in ("fw", "sensor", "stream", "heap") if k in obj}, ensure_ascii=False)))
        return
    writer.record(sn, kind, obj, now)   # 이력 기록. 실패해도 아래 처리는 계속

    with _state_lock:
        c = _cube(sn)
        c["seen"] = now
        if kind == "public":
            sess = obj.get("session")
            if sess and sess != c["session"]:
                c["session"] = sess
                c["last_seq"] = 0
                log("세션 갱신 %s → %s" % (sn, sess))
            c["values"] = obj
            c["values_at"] = now
            c["last_seq"] = max(c["last_seq"], _int(obj.get("applied_seq")))
        elif kind == "status":
            c["status"] = obj
            sess = obj.get("session")
            if sess and sess != c["session"]:
                c["session"] = sess
                c["last_seq"] = 0
                log("세션 갱신(status) %s → %s" % (sn, sess))
            seeded = max(c["last_seq"], _int(obj.get("last_received_seq")))
            if seeded != c["last_seq"]:
                log("순번 이어받음 %s: %d → %d" % (sn, c["last_seq"], seeded))
                c["last_seq"] = seeded

    if kind == "public":
        _push_to_subscribers(sn, {"sn": sn, "t": now, "values": obj})
    elif kind == "ack":
        _resolve_ack(sn, obj.get("seq", 0), obj.get("result", "?"))
    elif kind == "status":
        _resolve_status(sn, obj)


def _mqtt_start():
    global _mqtt
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="smartcube-api")
    if MQTT_USER:
        c.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    c.on_connect = _on_connect
    c.on_disconnect = _on_disconnect
    c.on_message = _on_message
    c.reconnect_delay_set(min_delay=1, max_delay=30)
    c.connect_async(BROKER_HOST, BROKER_PORT, keepalive=60)
    c.loop_start()
    _mqtt = c
    log("브로커 접속 시도: %s:%d" % (BROKER_HOST, BROKER_PORT))


def _mqtt_stop():
    if _mqtt is not None:
        _mqtt.loop_stop()
        _mqtt.disconnect()


# ══════════════════════════════════════════════ InfluxDB 조회
_influx = None


def _influx_client():
    global _influx
    if _influx is None:
        from influxdb_client import InfluxDBClient
        _influx = InfluxDBClient(url=INFLUX_URL, token=INFLUX_READ_TOKEN, org=INFLUX_ORG)
    return _influx


# AI 리포트 재료 조회 전용(2026-10-07 사용자 결정: 리포트 조회만 시간 초과 60초, 다른 조회는 기본 10초 그대로).
# 10-07 실측: 하루치 조회 최대 10.1초 — 기본 10초면 재료 단계에서 실패한다.
REPORT_INFLUX_TIMEOUT_MS = 60_000
_influx_report = None


def _influx_report_client():
    global _influx_report
    if _influx_report is None:
        from influxdb_client import InfluxDBClient
        _influx_report = InfluxDBClient(url=INFLUX_URL, token=INFLUX_READ_TOKEN, org=INFLUX_ORG,
                                        timeout=REPORT_INFLUX_TIMEOUT_MS)
    return _influx_report


def _query_history(sn, start, fields, every=None):
    flux = [
        'from(bucket:"%s")' % INFLUX_BUCKET,
        '|> range(start: %s)' % start,
        '|> filter(fn:(r) => r._measurement == "mqtt" and r.cube == "%s" and r.kind == "data")' % sn,
    ]
    if fields:
        if len(fields) == 1:
            flux.append('|> filter(fn:(r) => r._field == "%s")' % fields[0])
        else:
            flux.append('|> filter(fn:(r) => contains(value: r._field, set: [%s]))'
                        % ", ".join('"%s"' % f for f in fields))
    if every:
        flux.append('|> aggregateWindow(every: %s, fn: mean, createEmpty: false)' % every)
    flux.append('|> keep(columns:["_time","_field","_value"])')
    tables = _influx_client().query_api().query("\n".join(flux))
    out = []
    for table in tables:
        for rec in table.records:
            out.append({"time": rec.get_time().isoformat(), "field": rec.get_field(),
                        "value": rec.get_value()})
    return out


# ══════════════════════════════════════════════ FastAPI
@asynccontextmanager
async def lifespan(app):
    global _loop
    _loop = asyncio.get_running_loop()
    if not auth.configured():
        raise RuntimeError("AUTH_MODE=required 인데 SUPABASE_URL / SUPABASE_JWT_SECRET 이 빠졌습니다. "
                           "개발·병행 시험이면 AUTH_MODE=off 를 명시하세요.")
    if auth.AUTH_MODE != "off" and not (registry.ENABLED or registry.STATIC):
        raise RuntimeError("등록부(instances·cubes)를 읽을 SUPABASE_URL / SUPABASE_SERVICE_KEY 가 없습니다.")
    registry.start(log)
    if registry.ENABLED and not registry.STATIC:
        ok = await registry.load()
        if not ok:
            log("등록부 첫 적재 실패 — %d초마다 재시도한다. 그동안 모든 인스턴스는 404" % registry.RELOAD_S)
    log("smartcube-api %s | 인증 %s | 등록부 %s(인스턴스 %d, 큐브 %d) | 감사 기록 %s | 브로커 관리 %s" % (
        VERSION, auth.describe(), registry.stats["source"], len(registry.ids()),
        registry.status()["cubes"], "켬" if audit.ENABLED else "로그만",
        "켬" if broker_admin.ENABLED else "끔"))
    writer.start(log)
    _mqtt_start()
    registry.on_reload(_schedule_broker_ensure)
    _schedule_broker_ensure()
    os.makedirs(camera.SNAPSHOT_DIR, exist_ok=True)
    sched = asyncio.get_running_loop().create_task(_camera_scheduler())
    log("카메라: 저장 %s, 주기 촬영 %s KST, 즉시 촬영 대기 %.0f초, 스트림 상한 %d초" % (
        camera.SNAPSHOT_DIR, ",".join(camera.SNAPSHOT_TIMES) or "-", camera.SNAPSHOT_TIMEOUT_S, camera.STREAM_MAX_S))
    if crops.load():
        cs = crops.status()
        log("작물표 %s: %d종%s" % (crops.stats["file"], cs["crops"],
                                   " (검사 실패 %d: %s)" % (cs["invalid"], crops.listing()["invalid"]) if cs["invalid"] else ""))
    else:
        log("작물표를 읽지 못함(%s) — 자동 제어는 모든 큐브에서 판단하지 않는다" % crops.stats["error"])
    auto_task = asyncio.get_running_loop().create_task(autoctl.run())
    log("리포트: 회차 %d개 읽음" % report.load())
    report_task = asyncio.get_running_loop().create_task(report.run())
    yield
    report_task.cancel()
    auto_task.cancel()
    sched.cancel()
    _mqtt_stop()
    writer.stop()
    await registry.close()
    await audit.close()
    await report.close()


app = FastAPI(title="smartcube-api", version=VERSION, lifespan=lifespan)
app.include_router(report.router)   # AI 리포트(2026-10-07 젯슨 작성 방식, app/report.py)


# ── 브로커 계정 확인(백그라운드) ──
_ensure_running = False


async def _broker_ensure_all():
    """등록부의 모든 인스턴스에 역할·ACL·계정이 있게 한다. 비밀번호가 없으면 만들어 저장한다. 실패는 로그만."""
    global _ensure_running
    if _ensure_running or not broker_admin.ENABLED or registry.STATIC:
        return
    _ensure_running = True
    try:
        loop = asyncio.get_running_loop()
        for iid in registry.ids():
            if iid in broker_admin._ensured:
                continue
            try:
                pw = await registry.get_secret(iid)
                if not pw:
                    pw = broker_admin.new_password()
                    await registry.set_secret(iid, pw)
            except Exception as e:   # noqa: BLE001
                log("[broker] %s 비밀 조회·저장 실패: %s" % (iid, str(e)[:120]))
                continue
            await loop.run_in_executor(None, broker_admin.ensure_or_log, iid, registry.cubes_of(iid), pw, registry.cameras_of(iid))
    finally:
        _ensure_running = False


def _schedule_broker_ensure():
    if _loop is not None:
        _loop.create_task(_broker_ensure_all())


# ── 카메라 명령·스케줄 ──
_last_fired: Optional[str] = None


def _cam_publish(cam: str, payload: dict) -> bool:
    if _mqtt is None or not _mqtt_connected:
        return False
    _mqtt.publish("device/%s/%s/notify/config" % (AG_APP, cam), agframe.encode(payload), qos=0, retain=False)
    return True


async def _stream_stop(cam: str, reason: str):
    camera.stream_mark(cam, False)
    _cam_publish(cam, {"stream": 0})
    log("[camera] %s 스트리밍 정지 (%s)" % (cam, reason))


async def _camera_scheduler():
    """20초마다: 스트리밍 자동 중지 검사, SNAPSHOT_TIMES 에 모든 카메라 주기 촬영."""
    global _last_fired
    while True:
        try:
            await asyncio.sleep(20)
            for cam in list(camera._state):
                why = camera.stream_should_stop(cam)
                if why:
                    await _stream_stop(cam, why)
            key = camera.due_now(last_fired=_last_fired)
            if key:
                _last_fired = key
                cams = registry.all_cameras()
                n = 0
                for cam in cams:
                    if _cam_publish(cam, {"snap": 1, "trigger": "periodic"}):
                        n += 1
                camera.stats["scheduled"] += n
                log("[camera] 주기 촬영 명령 %s → 카메라 %d대" % (key, n))
        except asyncio.CancelledError:
            return
        except Exception as e:   # noqa: BLE001
            log("[camera] 스케줄러 오류: %s" % str(e)[:120])


def _check_sn(sn):
    if not SN_RE.match(sn):
        raise HTTPException(400, "잘못된 일련번호")
    return sn


def _check_cube(iid, sn):
    """SN 모양 + 이 인스턴스에 등록된 큐브인지. 다른 인스턴스의 큐브는 404 (존재를 알리지 않는다)."""
    _check_sn(sn)
    if registry.instance_of(sn) != iid:
        raise HTTPException(404, "이 인스턴스에 등록되지 않은 큐브")
    return sn


def _instance_status(iid):
    sns = registry.cubes_of(iid)
    with _state_lock:
        cubes = {}
        for sn in sns:
            c = _state.get(sn)
            if c is None:
                cubes[sn] = {"session": None, "last_seq": 0, "seen": None, "has_values": False,
                             "has_status": False, "fw": None}
            else:
                cubes[sn] = {"session": c["session"], "last_seq": c["last_seq"], "seen": c["seen"],
                             "has_values": c["values"] is not None, "has_status": c["status"] is not None,
                             "fw": (c["status"] or {}).get("fw")}
    inst = registry.instance(iid) or {}
    return {"version": VERSION, "instance_id": iid, "instance_name": inst.get("name"),
            "uptime_s": int(time.time() - STARTED), "mqtt_connected": _mqtt_connected,
            "rx": dict(_rx_count), "cubes": cubes, "writer": writer.status(), "audit": audit.status(),
            "registry": registry.status(), "broker_admin": broker_admin.status(), "auth": auth.describe(),
            "camera": camera.status(), "cameras": {cam: camera.info(cam) for cam in registry.cameras_of(iid)},
            "auto": autoctl.status(),
            "report": report.status(),
            "sse_subscribers": {sn: len(q) for sn, q in _subs.items() if sn in sns}}


# ── 공개 ──

@app.get("/health")
def health():
    """공개. 살아 있는지·브로커·기록기·등록부 크기만. 큐브 값·인스턴스 이름은 없다."""
    w = writer.status()
    reg = registry.status()
    return {"ok": True, "version": VERSION, "mqtt_connected": _mqtt_connected,
            "uptime_s": int(time.time() - STARTED), "auth_mode": auth.AUTH_MODE,
            "instances": reg["instances"], "cubes": reg["cubes"],
            "registry": {"source": reg["source"], "loaded_at": reg["loaded_at"], "last_error": reg["last_error"]},
            "broker_admin": {"enabled": broker_admin.ENABLED, "ensured": len(broker_admin.stats["ensured"]),
                             "last_error": broker_admin.stats["last_error"]},
            "camera": {"cameras": reg.get("cameras", 0), "snapshots": camera.stats["snapshots"],
                       "streaming": len(camera.status()["streaming"])},
            "auto": {"running": autoctl.stats["running"], "interval_s": autoctl.INTERVAL_S,
                     "enabled_cubes": len(registry.auto_cubes()), "columns": registry.auto_columns(),
                     "crops": crops.status()["crops"]},
            "writer": {"enabled": w["enabled"], "queued": w["queued"], "written_lines": w["written_lines"],
                       "failed_batches": w["failed_batches"], "last_error": w["last_error"]}}


# ── 전역: 내 정보·큐브 등록 ──

@app.get("/me")
def me(user: auth.User = Depends(auth.current_user)):
    """로그인만 하면 누구나. 내가 소유한 인스턴스 목록, 운영자 여부, 살아 있는 읽기 접근."""
    return user.as_dict()


class Claim(BaseModel):
    sn: str = Field(..., min_length=1, max_length=32)


def _register_sn(raw: str) -> str:
    sn = raw.strip().upper()
    if not SN_REGISTER_RE.match(sn):
        raise HTTPException(400, "시리얼 번호는 CUBE + 숫자 3자리 (예: CUBE001)")
    return sn


async def _broker_after_change(iid: str, new_cube: Optional[str] = None):
    """등록·추가 뒤 브로커 역할·ACL·계정 반영. 실패는 로그만(다음 재적재 때 다시)."""
    if not broker_admin.ENABLED or registry.STATIC:
        return
    loop = asyncio.get_running_loop()
    if new_cube and iid in broker_admin._ensured:
        try:
            await loop.run_in_executor(None, broker_admin.add_cube, iid, new_cube)
            log("[broker] %s 에 큐브 %s ACL 추가" % (iid, new_cube))
            return
        except Exception as e:   # noqa: BLE001
            log("[broker] %s 큐브 %s ACL 추가 실패: %s" % (iid, new_cube, str(e)[:120]))
            broker_admin.forget(iid)
    _schedule_broker_ensure()


@app.post("/claim")
async def claim(body: Claim, user: auth.User = Depends(auth.current_user)):
    """큐브 SN 으로 인스턴스 등록(회원가입). 게이트웨이가 인스턴스·큐브·브로커 계정을 자동으로 만든다.
    이미 있는 SN 은 거절(소유자가 비어 있는 옛 사전 등록분만 넘겨받는다)."""
    sn = _register_sn(body.sn)
    uid = user.user_uuid()
    if not uid and not user.local:
        raise HTTPException(400, "사용자 ID 가 UUID 가 아님")
    owner = uid or user.sub
    iid = registry.instance_of(sn)
    if iid is not None:
        inst = registry.instance(iid) or {}
        if inst.get("owner_id") == owner:
            return {"instance_id": iid, "sn": sn, "already": True}
        if inst.get("owner_id"):
            audit.fire(iid, user, sn, "claim_denied", {}, "already_owned")
            raise HTTPException(409, "이미 등록된 시리얼 번호입니다")
        try:
            ok = await registry.claim(iid, owner)
        except Exception as e:   # noqa: BLE001
            raise HTTPException(502, "등록 실패(Supabase): %s" % str(e)[:120])
        if not ok:
            audit.fire(iid, user, sn, "claim_denied", {}, "already_owned")
            raise HTTPException(409, "이미 등록된 시리얼 번호입니다")
        audit.fire(iid, user, sn, "claim", {"mode": "preregistered"}, "ok")
        log("[claim] %s → %s (%s, 사전 등록분)" % (sn, iid, user.email))
        await _broker_after_change(iid)
        return {"instance_id": iid, "sn": sn, "already": False}
    try:
        iid = await registry.create_instance(owner, sn)
    except RuntimeError as e:
        if "duplicate" in str(e):
            audit.fire("", user, sn, "claim_denied", {}, "already_owned")
            raise HTTPException(409, "이미 등록된 시리얼 번호입니다")
        raise HTTPException(502, "등록 실패(Supabase): %s" % str(e)[:120])
    except Exception as e:   # noqa: BLE001
        raise HTTPException(502, "등록 실패(Supabase): %s" % str(e)[:120])
    audit.fire(iid, user, sn, "claim", {"mode": "auto"}, "ok")
    log("[claim] %s → 새 인스턴스 %s (%s)" % (sn, iid, user.email))
    await _broker_after_change(iid)
    return {"instance_id": iid, "sn": sn, "already": False}


# ── 인스턴스: 내 접근·운영자 접근 ──

@app.get("/i/{iid}/me")
def instance_me(iid: str, user: auth.User = Depends(auth.current_user)):
    """로그인만 하면 누구나. 이 인스턴스에 대한 내 접근(owner/operator/none)과 큐브 목록."""
    auth.require_instance(iid)
    return user.as_dict(iid)


class OperatorAccess(BaseModel):
    master_key: str = Field(..., min_length=1, max_length=200)
    reason: str = Field(..., min_length=2, max_length=300)


@app.post("/i/{iid}/operator/access")
async def operator_access(iid: str, body: OperatorAccess, user: auth.User = Depends(auth.current_user)):
    """운영자 계정 + 마스터 키 + 사유 → 이 인스턴스에 OPERATOR_GRANT_MINUTES 동안 읽기 전용 접근. 발급·거절 모두 기록."""
    auth.require_instance(iid)
    if not user.operator:
        raise HTTPException(403, "운영자 계정이 아닙니다")
    if not auth.OPERATOR_MASTER_KEY:
        raise HTTPException(503, "이 서버에는 운영자 마스터 키가 설정돼 있지 않습니다")
    if not auth.master_key_ok(body.master_key):
        audit.fire(iid, user, "*", "operator_access_denied", {"reason": body.reason}, "bad_key")
        raise HTTPException(403, "마스터 키가 맞지 않습니다")
    auth.issue_grant(user, iid, body.reason)
    audit.fire(iid, user, "*", "operator_access", {"reason": body.reason, "minutes": auth.OPERATOR_GRANT_MINUTES}, "granted")
    log("[operator] %s → %s 읽기 접근 발급 (%d분): %s" % (user.email, iid, auth.OPERATOR_GRANT_MINUTES, body.reason))
    return user.as_dict(iid)


@app.get("/i/{iid}/operator/access")
def operator_access_status(iid: str, user: auth.User = Depends(auth.current_user)):
    auth.require_instance(iid)
    return {"operator": user.operator, "grant": auth.grant_for(user.sub, iid) if user.operator else None}


@app.delete("/i/{iid}/operator/access")
async def operator_release(iid: str, user: auth.User = Depends(auth.current_user)):
    auth.require_instance(iid)
    if auth.revoke_grant(user.sub, iid):
        audit.fire(iid, user, "*", "operator_release", {}, "released")
    return user.as_dict(iid)


# ── 조회 (소유자 또는 운영자 읽기) ──

@app.get("/i/{iid}/instance/status")
def instance_status(iid: str, user: auth.User = Depends(auth.reader)):
    return _instance_status(iid)


@app.get("/i/{iid}/cubes")
def cubes(iid: str, user: auth.User = Depends(auth.reader)):
    """이 인스턴스에 등록된 큐브. 아직 접속한 적 없는 큐브도 나온다(seen 없음)."""
    out = []
    with _state_lock:
        for sn in registry.cubes_of(iid):
            c = _state.get(sn)
            out.append({"sn": sn, "session": c["session"] if c else None, "seen": c["seen"] if c else None,
                        "has_values": bool(c and c["values"] is not None)})
    for d in out:
        s = registry.cube_settings(d["sn"])
        d.update({"crop_id": s["crop_id"], "auto_mode": s["auto_mode"]})
    return out


@app.get("/i/{iid}/cubes/{sn}/values")
def values(iid: str, sn: str, user: auth.User = Depends(auth.reader)):
    _check_cube(iid, sn)
    with _state_lock:
        c = _state.get(sn)
        if not c or c["values"] is None:
            raise HTTPException(404, "아직 이 큐브의 텔레메트리를 받지 못함")
        return {"sn": sn, "seen": c["seen"], "session": c["session"], "values": c["values"]}


@app.get("/i/{iid}/cubes/{sn}/status")
def status(iid: str, sn: str, user: auth.User = Depends(auth.reader)):
    _check_cube(iid, sn)
    with _state_lock:
        c = _state.get(sn)
        if not c or c["status"] is None:
            raise HTTPException(404, "아직 이 큐브의 상태 프레임을 받지 못함")
        return {"sn": sn, "status": c["status"]}


@app.get("/i/{iid}/cubes/{sn}/history")
async def history(iid: str, sn: str,
                  start: str = Query("-1h", description="Flux 상대 시각. 예: -15m, -1h, -7d"),
                  field: Optional[str] = Query(None, description="필드 이름. 쉼표로 여러 개. 예: moisture_raw,led1"),
                  every: Optional[str] = Query(None, description="집계 창(평균). 예: 5m. 없으면 원본 그대로"),
                  user: auth.User = Depends(auth.reader)):
    _check_cube(iid, sn)
    if not RANGE_RE.match(start):
        raise HTTPException(400, "start 는 -15m, -1h, -7d 꼴")
    fields = [f.strip() for f in field.split(",")] if field else []
    fields = [f for f in fields if f]
    if len(fields) > MAX_FIELDS:
        raise HTTPException(400, "필드는 최대 %d개" % MAX_FIELDS)
    for f in fields:
        if not FIELD_RE.match(f):
            raise HTTPException(400, "잘못된 필드 이름: %s" % f)
    if every and not EVERY_RE.match(every):
        raise HTTPException(400, "every 는 30s, 5m, 1h 꼴")
    if not INFLUX_READ_TOKEN:
        raise HTTPException(503, "INFLUX_READ_TOKEN 미설정")
    loop = asyncio.get_running_loop()
    try:
        rows = await loop.run_in_executor(None, _query_history, sn, start, fields, every)
    except Exception as e:
        raise HTTPException(502, "InfluxDB 조회 실패: %s" % e)
    return {"sn": sn, "start": start, "field": field, "fields": fields, "every": every,
            "count": len(rows), "rows": rows}


@app.get("/i/{iid}/cubes/{sn}/stream")
async def stream(iid: str, sn: str, user: auth.User = Depends(auth.reader)):
    """SSE. 접속 즉시 마지막 값 1건, 이후 새 텔레메트리마다 1건. 15초마다 keepalive. 헤더 인증(fetch 스트림)."""
    _check_cube(iid, sn)
    q: asyncio.Queue = asyncio.Queue()
    _subs.setdefault(sn, set()).add(q)

    async def gen():
        try:
            with _state_lock:
                c = _state.get(sn)
                if c and c["values"] is not None:
                    yield "data: %s\n\n" % json.dumps(
                        {"sn": sn, "t": c["seen"], "values": c["values"]}, ensure_ascii=False)
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), SSE_KEEPALIVE_S)
                    yield "data: %s\n\n" % json.dumps(item, ensure_ascii=False)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _subs.get(sn, set()).discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── 큐브 추가·브로커 계정 (소유자만) ──

class AddCube(BaseModel):
    sn: str = Field(..., min_length=1, max_length=32)


@app.post("/i/{iid}/cubes")
async def add_cube(iid: str, body: AddCube, user: auth.User = Depends(auth.owner)):
    """내 인스턴스에 큐브 추가(장치 추가 버튼). SN 형식 검사, 이미 등록된 SN 은 거절."""
    sn = _register_sn(body.sn)
    if registry.instance_of(sn) == iid:
        return {"instance_id": iid, "sn": sn, "already": True, "cubes": registry.cubes_of(iid)}
    if registry.instance_of(sn) is not None:
        audit.fire(iid, user, sn, "cube_add_denied", {}, "already_registered")
        raise HTTPException(409, "이미 등록된 시리얼 번호입니다")
    try:
        await registry.add_cube(iid, sn)
    except RuntimeError as e:
        if "duplicate" in str(e):
            audit.fire(iid, user, sn, "cube_add_denied", {}, "already_registered")
            raise HTTPException(409, "이미 등록된 시리얼 번호입니다")
        raise HTTPException(502, "추가 실패(Supabase): %s" % str(e)[:120])
    except Exception as e:   # noqa: BLE001
        raise HTTPException(502, "추가 실패(Supabase): %s" % str(e)[:120])
    audit.fire(iid, user, sn, "cube_add", {}, "ok")
    log("[cube] %s 에 %s 추가 (%s)" % (iid, sn, user.email))
    await _broker_after_change(iid, sn)
    return {"instance_id": iid, "sn": sn, "already": False, "cubes": registry.cubes_of(iid)}


async def _broker_info(iid: str):
    try:
        pw = await registry.get_secret(iid)
    except Exception as e:   # noqa: BLE001
        raise HTTPException(502, "비밀 조회 실패: %s" % str(e)[:120])
    d = broker_admin.public_endpoint()
    d.update({"instance_id": iid, "username": iid, "password": pw, "ready": iid in broker_admin._ensured,
              "cubes": registry.cubes_of(iid)})
    return d


@app.get("/i/{iid}/broker")
async def broker_info(iid: str, user: auth.User = Depends(auth.owner)):
    """이 인스턴스의 장치용 브로커 계정(펌웨어 secrets.h 에 넣을 값). 소유자만. 비밀번호는 저장된 평문."""
    info = await _broker_info(iid)
    if info["password"] is None:
        # 아직 만들어지지 않았으면 지금 만든다
        pw = broker_admin.new_password()
        try:
            await registry.set_secret(iid, pw)
        except Exception as e:   # noqa: BLE001
            raise HTTPException(502, "비밀 저장 실패: %s" % str(e)[:120])
        broker_admin.forget(iid)
        _schedule_broker_ensure()
        info = await _broker_info(iid)
    audit.fire(iid, user, "*", "broker_view", {}, "ok")
    return info


@app.post("/i/{iid}/broker/rotate")
async def broker_rotate(iid: str, user: auth.User = Depends(auth.owner)):
    """브로커 비밀번호 재발급. 접속 중인 장치는 다음 재접속부터 새 비밀번호가 필요하다."""
    pw = broker_admin.new_password()
    try:
        await registry.set_secret(iid, pw)
    except Exception as e:   # noqa: BLE001
        raise HTTPException(502, "비밀 저장 실패: %s" % str(e)[:120])
    if broker_admin.ENABLED and not registry.STATIC:
        try:
            await asyncio.get_running_loop().run_in_executor(None, broker_admin.set_password, iid, pw)
        except Exception as e:   # noqa: BLE001
            broker_admin.forget(iid)
            _schedule_broker_ensure()
            raise HTTPException(502, "브로커 반영 실패(비밀은 저장됨, 다시 시도): %s" % str(e)[:120])
    audit.fire(iid, user, "*", "broker_rotate", {}, "ok")
    log("[broker] %s 비밀번호 재발급 (%s)" % (iid, user.email))
    return await _broker_info(iid)


# ── 제어 (소유자만) ──

class Control(BaseModel):
    """비우면 마지막 텔레메트리의 현재값을 그대로 쓴다(부분 변경 가능)."""
    pump: Optional[bool] = None
    led1: Optional[int] = Field(None, ge=0, le=100)
    led2: Optional[int] = Field(None, ge=0, le=100)
    fan1: Optional[int] = Field(None, ge=0, le=100)
    fan2: Optional[int] = Field(None, ge=0, le=100)


async def _send_control(iid: str, sn: str, body: dict, user, action: str = "control",
                        audit_extra: Optional[dict] = None, require_auto: bool = False):
    """사람 제어와 자동 제어가 함께 쓰는 송신(2026-10-06 분리). 순번은 같은 잠금 안에서 하나씩 올린다.
    body 의 빠진 키는 마지막 텔레메트리 값으로 채운다. require_auto=True(자동 제어)면 보내기 직전에
    자동 제어가 아직 켜져 있는지 본다 — 사람 조작으로 꺼졌으면 보내지 않는다(사람 명령을 덮어쓰지 않게)."""
    if _mqtt is None or not _mqtt_connected:
        raise HTTPException(503, "브로커 미접속")
    if require_auto and not registry.cube_settings(sn)["auto_mode"]:
        return {"sn": sn, "seq": None, "sent": None, "result": "skipped"}
    with _state_lock:
        c = _state.get(sn)
        if not c or not c["session"] or c["values"] is None:
            raise HTTPException(409, "세션을 모름 — 큐브 텔레메트리를 먼저 받아야 한다")
        cur = c["values"]
        payload = {
            "session": c["session"],
            "seq": c["last_seq"] + 1,
            "pump": bool(cur.get("pump", False)) if body.get("pump") is None else bool(body["pump"]),
            "led1": int(cur.get("led1", 0)) if body.get("led1") is None else int(body["led1"]),
            "led2": int(cur.get("led2", 0)) if body.get("led2") is None else int(body["led2"]),
            "fan1": int(cur.get("fan1", 0)) if body.get("fan1") is None else int(body["fan1"]),
            "fan2": int(cur.get("fan2", 0)) if body.get("fan2") is None else int(body["fan2"]),
        }
        c["last_seq"] = payload["seq"]
        seq = payload["seq"]

    sent = {k: v for k, v in payload.items() if k != "session"}
    requested = dict(body)
    fut = asyncio.get_running_loop().create_future()
    _pending[(sn, seq)] = fut
    result = "timeout"
    try:
        _mqtt.publish("device/%s/%s/notify/control" % (AG_APP, sn), agframe.encode(payload), qos=0, retain=False)
        try:
            result = await asyncio.wait_for(fut, ACK_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise HTTPException(504, "응답(ack) 없음 — 큐브가 오프라인이거나 세션이 바뀜")
    finally:
        _pending.pop((sn, seq), None)
        payload_audit = {"requested": requested, "sent": sent}
        if audit_extra:
            payload_audit.update(audit_extra)
        audit.fire(iid, user, sn, action, payload_audit, result)
    return {"sn": sn, "seq": seq, "sent": sent, "result": result}


async def _persist_auto_off(sn: str):
    try:
        await registry.update_cube_settings(sn, keep_on_fail=True, auto_mode=False)
    except Exception as e:   # noqa: BLE001
        log("[auto] %s 자동 제어 끔 저장 실패(메모리에는 반영, 재적재 때 다시 씀): %s" % (sn, str(e)[:120]))


def _manual_override(iid: str, sn: str, user):
    """사람 조작이면 그 큐브의 자동 제어를 끈다(AC-12). 메모리에 즉시 반영하고 저장은 기다리지 않는다
    (긴급 정지가 Supabase 응답을 기다리면 안 되므로)."""
    if not registry.cube_settings(sn)["auto_mode"]:
        return
    registry.override_local(sn, auto_mode=False)
    autoctl.note_manual(sn)
    asyncio.get_running_loop().create_task(_persist_auto_off(sn))
    audit.fire(iid, user, sn, "auto_off_manual", {}, "ok")
    log("[auto] %s 사람 조작으로 자동 제어 끔 (%s)" % (sn, getattr(user, "email", "")))


@app.post("/i/{iid}/cubes/{sn}/control")
async def control(iid: str, sn: str, body: Control, user: auth.User = Depends(auth.owner)):
    _check_cube(iid, sn)
    _manual_override(iid, sn, user)
    return await _send_control(iid, sn, body.model_dump(exclude_none=True), user)


# ── 자동 제어 (2026-10-06, app/autoctl.py · app/crops.py) ──

def _auto_snapshot(sn: str):
    with _state_lock:
        c = _state.get(sn)
        if not c:
            return None, None, None
        return (dict(c["values"]) if c["values"] else None, dict(c["status"]) if c["status"] else None,
                c.get("values_at"))


async def _auto_send(iid: str, sn: str, body: dict, extra: dict) -> str:
    r = await _send_control(iid, sn, body, autoctl.ACTOR, action="auto_control", audit_extra=extra, require_auto=True)
    return r["result"]


autoctl.bind(enabled=registry.auto_cubes, snapshot=_auto_snapshot, send=_auto_send)


# ── AI 리포트 (2026-10-06 → 10-07 젯슨 작성 방식, app/report.py) ──

def _report_series(cube, start, stop, fields, every):
    """리포트 재료: [start, stop) 구간을 every 평균으로(시각 = 창 끝, KST). start·stop 은 UTC RFC3339.
    조회 시간 초과는 리포트 전용 60초(_influx_report_client, 10-07)."""
    flux = [
        'from(bucket:"%s")' % INFLUX_BUCKET,
        '|> range(start: %s, stop: %s)' % (start, stop),
        '|> filter(fn:(r) => r._measurement == "mqtt" and r.cube == "%s" and r.kind == "data")' % cube,
        '|> filter(fn:(r) => contains(value: r._field, set: [%s]))' % ", ".join('"%s"' % f for f in fields),
        '|> aggregateWindow(every: %s, fn: mean, createEmpty: false)' % every,
        '|> keep(columns:["_time","_field","_value"])',
    ]
    out = {f: [] for f in fields}
    for table in _influx_report_client().query_api().query("\n".join(flux)):
        for rec in table.records:
            out.setdefault(rec.get_field(), []).append([rec.get_time().astimezone(KST).isoformat(), rec.get_value()])
    for f in out:
        out[f].sort()
    return out


def _report_crop(sn):
    s = registry.cube_settings(sn)
    return {"crop_id": s["crop_id"], "profile": crops.profile(s["crop_id"]), "auto_mode": s["auto_mode"]}


report.bind(series=_report_series, crop=_report_crop)


def _need_auto_columns():
    if not registry.auto_columns():
        raise HTTPException(503, "Supabase cubes 표에 자동 제어 칸이 없습니다(SQL v6 실행 필요)")


def _auto_view(sn: str):
    s = registry.cube_settings(sn)
    return {"sn": sn, "crop_id": s["crop_id"], "crop": crops.profile(s["crop_id"]),
            "crop_invalid": crops.invalid_reason(s["crop_id"]), "enabled": s["auto_mode"],
            "available": registry.auto_columns(),
            "interval_s": autoctl.INTERVAL_S, "state": autoctl.view(sn)}


@app.get("/i/{iid}/crops")
def crops_list(iid: str, user: auth.User = Depends(auth.reader)):
    return crops.listing()


@app.get("/i/{iid}/cubes/{sn}/auto")
def auto_get(iid: str, sn: str, user: auth.User = Depends(auth.reader)):
    _check_cube(iid, sn)
    return _auto_view(sn)


class CropSet(BaseModel):
    crop_id: Optional[str] = Field(None, max_length=40)


@app.put("/i/{iid}/cubes/{sn}/crop")
async def crop_set(iid: str, sn: str, body: CropSet, user: auth.User = Depends(auth.owner)):
    """작물 배정. null(또는 빈 값)이면 해제하고 자동 제어도 끈다(작물 없는 큐브는 자동 불가, AC-16)."""
    _check_cube(iid, sn)
    _need_auto_columns()
    cid = (body.crop_id or "").strip() or None
    if cid is not None and crops.profile(cid) is None:
        raise HTTPException(400, "작물표에 없는 작물 ID: %s" % cid)
    fields = {"crop_id": cid}
    if cid is None:
        fields["auto_mode"] = False
    try:
        await registry.update_cube_settings(sn, **fields)
    except Exception as e:   # noqa: BLE001
        raise HTTPException(502, "저장 실패(Supabase): %s" % str(e)[:120])
    autoctl.reset(sn)
    audit.fire(iid, user, sn, "crop_set", {"crop_id": cid}, "ok")
    log("[auto] %s 작물 %s (%s)" % (sn, cid, user.email))
    return _auto_view(sn)


class AutoSet(BaseModel):
    enabled: bool


@app.put("/i/{iid}/cubes/{sn}/auto")
async def auto_set(iid: str, sn: str, body: AutoSet, user: auth.User = Depends(auth.owner)):
    """자동 제어 켜기/끄기(급수 규칙 포함 — 별도 스위치 없음, 사용자 정정 2026-10-06 저녁).
    켜고 끌 때마다 판정 기억을 버린다(일시 정지 해제 포함)."""
    _check_cube(iid, sn)
    _need_auto_columns()
    if body.enabled and crops.profile(registry.cube_settings(sn)["crop_id"]) is None:
        raise HTTPException(409, "작물을 먼저 선택하세요")
    fields = {"auto_mode": body.enabled}
    try:
        await registry.update_cube_settings(sn, **fields)
    except Exception as e:   # noqa: BLE001
        raise HTTPException(502, "저장 실패(Supabase): %s" % str(e)[:120])
    autoctl.reset(sn)
    audit.fire(iid, user, sn, "auto_set", fields, "ok")
    log("[auto] %s 설정 %s (%s)" % (sn, fields, user.email))
    return _auto_view(sn)


@app.post("/i/{iid}/cubes/{sn}/refresh")
async def refresh(iid: str, sn: str, user: auth.User = Depends(auth.owner)):
    _check_cube(iid, sn)
    if _mqtt is None or not _mqtt_connected:
        raise HTTPException(503, "브로커 미접속")
    _mqtt.publish("device/%s/%s/notify/public" % (AG_APP, sn), b"", qos=0, retain=False)
    audit.fire(iid, user, sn, "refresh", {}, "sent")
    return {"sn": sn, "requested": True}


class SoilConfig(BaseModel):
    """토양 보정. air/water 직접 지정(0~32767), cal = air|water(원격 측정)|reset."""
    air: Optional[int] = Field(None, ge=0, le=32767)
    water: Optional[int] = Field(None, ge=0, le=32767)
    cal: Optional[str] = None


@app.post("/i/{iid}/cubes/{sn}/config")
async def config(iid: str, sn: str, body: SoilConfig, user: auth.User = Depends(auth.owner)):
    _check_cube(iid, sn)
    if _mqtt is None or not _mqtt_connected:
        raise HTTPException(503, "브로커 미접속")
    if body.cal is not None and body.cal not in ("air", "water", "reset"):
        raise HTTPException(400, "cal 은 air, water, reset 중 하나")
    payload = {}
    if body.air is not None:
        payload["air"] = body.air
    if body.water is not None:
        payload["water"] = body.water
    if body.cal:
        payload["cal_" + body.cal] = 1
    if not payload:
        raise HTTPException(400, "air, water, cal 중 하나는 있어야 한다")
    wait_measured = body.cal in ("air", "water")

    fut = asyncio.get_running_loop().create_future()
    _pending_status[sn] = (fut, wait_measured)
    result = "timeout"
    st = None
    try:
        _mqtt.publish("device/%s/%s/notify/config" % (AG_APP, sn), agframe.encode(payload), qos=0, retain=False)
        try:
            st = await asyncio.wait_for(fut, ACK_TIMEOUT_S)
            result = "ok"
        except asyncio.TimeoutError:
            raise HTTPException(504, "status 응답 없음 — 큐브가 오프라인이거나 설정 프레임을 모르는 펌웨어")
    finally:
        cur = _pending_status.get(sn)
        if cur is not None and cur[0] is fut:
            _pending_status.pop(sn, None)
        audit.fire(iid, user, sn, "config", payload, result)
    return {"sn": sn, "sent": payload, "air": st.get("air"), "water": st.get("water"),
            "cal_pending": bool(st.get("cal_pending", False)), "ads1115_ok": st.get("ads1115_ok")}


# ══════════════════════════════════════════════ 카메라 (2026-09-29 오후, app/camera.py)

async def device_auth(request: Request) -> str:
    """장치 인증: HTTP Basic 사용자명 = 인스턴스 ID, 비밀번호 = 인스턴스 브로커 비밀번호. 돌려주는 값 = iid."""
    h = request.headers.get("authorization", "")
    if h[:6].lower() != "basic ":
        raise HTTPException(401, "장치 인증 필요(Basic)", headers={"WWW-Authenticate": "Basic realm=smartcube-device"})
    try:
        raw = base64.b64decode(h[6:].strip()).decode("utf-8")
        iid, _, pw = raw.partition(":")
    except Exception:   # noqa: BLE001
        raise HTTPException(401, "장치 인증 형식 오류", headers={"WWW-Authenticate": "Basic realm=smartcube-device"})
    if not IID_RE.match(iid) or registry.instance(iid) is None:
        raise HTTPException(401, "없는 인스턴스", headers={"WWW-Authenticate": "Basic realm=smartcube-device"})
    try:
        real = await registry.secret_cached(iid)
    except Exception as e:   # noqa: BLE001
        raise HTTPException(502, "비밀 조회 실패: %s" % str(e)[:120])
    if not real or not pw or not hmac.compare_digest(real.encode("utf-8"), pw.encode("utf-8")):
        raise HTTPException(401, "장치 인증 실패", headers={"WWW-Authenticate": "Basic realm=smartcube-device"})
    return iid


def _check_camera_sn(cam: str) -> str:
    cam = cam.strip().upper()
    if not camera.CAM_RE.match(cam):
        raise HTTPException(400, "카메라 SN 은 <큐브 SN>-C<번호> (예: CUBE001-C1)")
    return cam


async def _ensure_camera(iid: str, cam: str, name: Optional[str] = None) -> bool:
    """등록돼 있지 않으면 등록한다(큐브가 이 인스턴스 것일 때만). 돌려주는 값 = 새로 등록했는지."""
    d = registry.camera(cam)
    if d is not None:
        if d["instance_id"] != iid:
            raise HTTPException(403, "다른 인스턴스의 카메라")
        return False
    cube = camera.cube_of(cam)
    if registry.instance_of(cube) != iid:
        raise HTTPException(404, "이 인스턴스에 등록되지 않은 큐브: %s" % cube)
    try:
        await registry.add_camera(iid, cam, cube, name)
    except RuntimeError as e:
        if "duplicate" in str(e):
            return False
        raise HTTPException(502, "카메라 등록 실패(Supabase): %s" % str(e)[:120])
    except Exception as e:   # noqa: BLE001
        raise HTTPException(502, "카메라 등록 실패(Supabase): %s" % str(e)[:120])
    log("[camera] %s 등록 (인스턴스 %s, 큐브 %s)" % (cam, iid, cube))
    await _broker_after_change(iid, cam)
    return True


def _check_camera(iid: str, cam: str) -> str:
    cam = _check_camera_sn(cam)
    d = registry.camera(cam)
    if d is None or d["instance_id"] != iid:
        raise HTTPException(404, "이 인스턴스에 등록되지 않은 카메라")
    return cam


def _cube_telemetry(cube: str):
    with _state_lock:
        c = _state.get(cube)
        if not c:
            return None
        return {"seen": c["seen"], "session": c["session"], "values": c["values"]}


# ── 장치 쪽 (카메라 보드) ──

class CameraHello(BaseModel):
    sn: str = Field(..., min_length=1, max_length=32)
    model: Optional[str] = None
    fw: Optional[str] = None
    sensor: Optional[str] = None


@app.post("/dev/cameras/hello")
async def dev_camera_hello(body: CameraHello, iid: str = Depends(device_auth)):
    """카메라 부팅 때. 처음이면 자동 등록(큐브가 이 인스턴스 것이어야 함). 서버 설정을 돌려준다."""
    cam = _check_camera_sn(body.sn)
    new = await _ensure_camera(iid, cam)
    fields = {k: v for k, v in body.model_dump().items() if v is not None and k != "sn"}
    camera.touch(cam, {**(camera.info(cam)["status"] or {}), **fields} if fields else None)   # 빈 hello 로 지우지 않는다
    return {"ok": True, "instance_id": iid, "camera": cam, "cube": camera.cube_of(cam), "registered": new,
            "snapshot_times": camera.SNAPSHOT_TIMES, "stream_max_s": camera.STREAM_MAX_S,
            "stream_on": camera.info(cam)["stream"]["on"]}


@app.post("/dev/snapshots")
async def dev_snapshot_upload(request: Request, cam: str = Query(...), trigger: str = Query("device"),
                              req: Optional[str] = Query(None),
                              zoom: Optional[int] = Query(None, ge=100, le=400, description="촬영 배율 %(1배면 생략). 사이드카에만 기록"),
                              iid: str = Depends(device_auth)):
    """카메라가 찍은 JPEG 본문(image/jpeg). req 가 있으면 기다리던 즉시 촬영 요청을 깨운다."""
    cam = _check_camera_sn(cam)
    await _ensure_camera(iid, cam)
    data = await request.body()
    if len(data) < 100 or data[:2] != b"\xff\xd8":
        raise HTTPException(400, "JPEG 가 아님")
    if len(data) > camera.SNAPSHOT_MAX_BYTES:
        raise HTTPException(413, "너무 큼(%d바이트, 상한 %d)" % (len(data), camera.SNAPSHOT_MAX_BYTES))
    meta = {"camera_status": camera.info(cam)["status"], "cube_telemetry": _cube_telemetry(camera.cube_of(cam)),
            "request_id": req, "content_type": request.headers.get("content-type")}
    if zoom:
        meta["zoom_pct"] = zoom
    try:
        info = await asyncio.get_running_loop().run_in_executor(None, camera.store, iid, cam, data, trigger, meta)
    except Exception as e:   # noqa: BLE001
        camera.stats["last_error"] = str(e)[:200]
        raise HTTPException(500, "저장 실패: %s" % str(e)[:120])
    resolved = camera.resolve(req, info)
    log("[camera] %s 스냅샷 %s (%d바이트, %s%s)" % (cam, info["path"], len(data), trigger, ", 요청 응답" if resolved else ""))
    return {"ok": True, "resolved": resolved, **info}


@app.post("/dev/frames")
async def dev_frame_upload(request: Request, cam: str = Query(...), iid: str = Depends(device_auth)):
    """스트리밍 프레임(JPEG). 저장하지 않고 최신 한 장만 시청자에게. 응답의 stop 이 true 면 카메라는 스트리밍을 멈춘다."""
    cam = _check_camera(iid, cam)
    data = await request.body()
    if len(data) < 100 or data[:2] != b"\xff\xd8":
        raise HTTPException(400, "JPEG 가 아님")
    if len(data) > camera.FRAME_MAX_BYTES:
        raise HTTPException(413, "프레임이 너무 큼")
    await camera.push_frame(cam, data)
    st = camera.info(cam)["stream"]
    stop = not st["on"] or camera.stream_should_stop(cam) is not None
    if stop and st["on"]:
        await _stream_stop(cam, camera.stream_should_stop(cam) or "off")
    return {"ok": True, "seq": st["frames"], "viewers": st["viewers"], "stop": stop}


# ── 사람 쪽 ──

@app.get("/i/{iid}/cameras")
def cameras_list(iid: str, user: auth.User = Depends(auth.reader)):
    out = []
    for cam in registry.cameras_of(iid):
        d = registry.camera(cam) or {}
        out.append({**camera.info(cam), "name": d.get("name")})
    return out


class AddCamera(BaseModel):
    cube: str = Field(..., min_length=1, max_length=32)
    number: int = Field(1, ge=1, le=99)
    name: Optional[str] = Field(None, max_length=60)


@app.post("/i/{iid}/cameras")
async def cameras_add(iid: str, body: AddCamera, user: auth.User = Depends(auth.owner)):
    """장치 연결 페이지 "카메라 추가": 큐브 + 번호 → <큐브>-C<번호>."""
    cube = body.cube.strip().upper()
    _check_cube(iid, cube)
    cam = "%s-C%d" % (cube, body.number)
    new = await _ensure_camera(iid, cam, body.name)
    audit.fire(iid, user, cam, "camera_add", {"cube": cube, "number": body.number}, "ok" if new else "already")
    return {"camera": cam, "cube": cube, "already": not new, "cameras": registry.cameras_of(iid)}


@app.get("/i/{iid}/cubes/{sn}/snapshots")
async def snapshots_list(iid: str, sn: str, cam: Optional[str] = Query(None), date: Optional[str] = Query(None),
                         limit: int = Query(50, ge=1, le=500), user: auth.User = Depends(auth.reader)):
    _check_cube(iid, sn)
    if cam:
        cam = _check_camera(iid, cam)
        if camera.cube_of(cam) != sn:
            raise HTTPException(400, "이 큐브의 카메라가 아님")
    if date and not camera.DATE_RE.match(date):
        raise HTTPException(400, "date 는 YYYY-MM-DD")
    rows = await asyncio.get_running_loop().run_in_executor(None, camera.list_snapshots, iid, sn, cam, limit, date)
    return {"cube": sn, "camera": cam, "count": len(rows), "rows": rows, "cameras": registry.cameras_of(iid, sn)}


@app.get("/i/{iid}/cubes/{sn}/snapshots/{cam}/{day}/{name}")
def snapshot_file(iid: str, sn: str, cam: str, day: str, name: str, user: auth.User = Depends(auth.reader)):
    _check_cube(iid, sn)
    cam = _check_camera(iid, cam)
    p = camera.file_path(iid, sn, cam, day, name)
    if not p:
        raise HTTPException(404, "없는 파일")
    return FileResponse(p, media_type="image/jpeg" if name.endswith(".jpg") else "application/json",
                        headers={"Cache-Control": "private, max-age=86400"})


class SnapshotRequest(BaseModel):
    cam: Optional[str] = None
    zoom: Optional[float] = None      # 1·1.5·2·3 (배). 2026-10-08, 펌웨어 cube-cam-0.6-zoom 만 적용. 그 촬영 한 번에만


@app.post("/i/{iid}/cubes/{sn}/snapshot")
async def snapshot_request(iid: str, sn: str, body: SnapshotRequest = SnapshotRequest(), user: auth.User = Depends(auth.owner)):
    """즉시 촬영: 카메라에 snap 명령 → 업로드 도착까지 SNAPSHOT_TIMEOUT 대기 → 파일 정보.
    zoom 이 있으면 명령에 zoom:<%> 를 붙인다(100 이면 생략). 줌을 모르는 펌웨어는 무시하고 1배로 찍는다."""
    _check_cube(iid, sn)
    pct = 100
    if body.zoom is not None:
        pct = camera.zoom_pct(body.zoom)
        if pct is None:
            raise HTTPException(400, "zoom 은 1, 1.5, 2, 3 중 하나")
    cams = registry.cameras_of(iid, sn)
    if body.cam:
        cam = _check_camera(iid, body.cam)
        if camera.cube_of(cam) != sn:
            raise HTTPException(400, "이 큐브의 카메라가 아님")
    elif len(cams) == 1:
        cam = cams[0]
    elif not cams:
        raise HTTPException(404, "이 큐브에 등록된 카메라가 없음")
    else:
        raise HTTPException(400, "카메라가 여러 대라 cam 을 지정해야 함: %s" % ", ".join(cams))
    req = camera.new_request_id()
    fut = camera.wait_for(req, asyncio.get_running_loop())
    result = "timeout"
    payload = {"snap": 1, "req": req, "trigger": "request"}
    if pct != 100:
        payload["zoom"] = pct
    try:
        if not _cam_publish(cam, payload):
            camera.drop(req)
            raise HTTPException(503, "브로커 미접속")
        try:
            info = await asyncio.wait_for(fut, camera.SNAPSHOT_TIMEOUT_S)
            result = "ok"
        except asyncio.TimeoutError:
            camera.drop(req)
            raise HTTPException(504, "카메라가 %.0f초 안에 사진을 올리지 않음(오프라인이거나 촬영 실패)" % camera.SNAPSHOT_TIMEOUT_S)
    finally:
        audit.fire(iid, user, cam, "snapshot_request", {"cube": sn, "req": req, "zoom": pct}, result)
    return {"camera": cam, **info}


@app.post("/i/{iid}/cameras/{cam}/stream/start")
async def stream_start(iid: str, cam: str, user: auth.User = Depends(auth.owner)):
    cam = _check_camera(iid, cam)
    if not _cam_publish(cam, {"stream": 1, "max_s": camera.STREAM_MAX_S}):
        raise HTTPException(503, "브로커 미접속")
    camera.stream_mark(cam, True)
    audit.fire(iid, user, cam, "stream_start", {"max_s": camera.STREAM_MAX_S}, "sent")
    log("[camera] %s 스트리밍 시작 명령 (%s)" % (cam, user.email))
    return camera.info(cam)


@app.post("/i/{iid}/cameras/{cam}/stream/stop")
async def stream_stop_route(iid: str, cam: str, user: auth.User = Depends(auth.owner)):
    cam = _check_camera(iid, cam)
    await _stream_stop(cam, "user")
    audit.fire(iid, user, cam, "stream_stop", {}, "sent")
    return camera.info(cam)


@app.get("/i/{iid}/cameras/{cam}/stream")
async def stream_view(iid: str, cam: str, frames: int = Query(0, ge=0, le=100000, description="0 = 무제한. 시험·curl 용 상한"),
                      user: auth.User = Depends(auth.reader)):
    """MJPEG 본문(multipart 경계 형식). 헤더 인증이라 화면은 fetch 로 읽어 img 에 그린다. 프레임이 없으면 10초마다 빈 경계.
    2026-09-30: Content-Type 을 application/octet-stream 으로 보낸다. iOS(WebKit: 사파리·크롬 모두)는
    multipart/x-mixed-replace 응답을 네트워크 계층에서 특별 취급해 fetch 가 "Load failed" 로 끝난다.
    본문 바이트는 그대로이고 경계 문자열은 X-Mjpeg-Boundary 헤더로 준다(화면 파서가 이 헤더를 읽는다)."""
    cam = _check_camera(iid, cam)
    camera.viewer_enter(cam)
    boundary = "smartcubeframe"

    async def gen():
        seq = 0
        sent = 0
        try:
            while frames == 0 or sent < frames:
                got = await camera.next_frame(cam, seq, timeout=10.0)
                if got is None:
                    yield ("--%s\r\nContent-Type: text/plain\r\nContent-Length: 0\r\n\r\n\r\n" % boundary).encode()
                    continue
                seq, data = got
                head = "--%s\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\nX-Frame-Seq: %d\r\n\r\n" % (boundary, len(data), seq)
                yield head.encode() + data + b"\r\n"
                sent += 1
        finally:
            if camera.viewer_leave(cam) == 0:
                camera.note_viewer_left(cam)

    return StreamingResponse(gen(), media_type="application/octet-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "X-Mjpeg-Boundary": boundary})
