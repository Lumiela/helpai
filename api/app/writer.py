# -*- coding: utf-8 -*-
"""
InfluxDB 기록기 — bridge(프레임 → 숫자 토픽) + telegraf(숫자 토픽 → InfluxDB) 를 대체한다 (2026-09-28).

  main.py 가 원본 토픽(device/<앱>/<SN>/{public,status,ack})을 받으면 여기 record() 를 부른다.
  저장 형식은 telegraf 가 만들던 것과 똑같이 맞춘다 — 기존 이력·Grafana 대시보드·api 이력 라우트가
  그대로 이어지게 하기 위해서다.

      measurement  mqtt
      tags         cube=<SN>, kind=data|status|ack, topic=smartcube/<SN>/ctrl/<kind>, host=jetson
      fields       숫자만 (참/거짓 → 1.0/0.0, null·문자열 제거) — bridge.numeric_only 와 같은 규칙
      time         수신 시각
  ★ 값은 전부 float 로 쓴다. telegraf 가 JSON 숫자를 float 로 저장했으므로 정수로 쓰면 자료형 충돌이 난다.

  배치 쓰기(2초 또는 200점)·재시도(최대 5회)는 influxdb-client 가 한다. 실패는 세어서 /health 에 보인다.

환경변수
  WRITER_ENABLED      1(기본) | 0
  INFLUX_URL          기본 http://influxdb:8086
  INFLUX_WRITE_TOKEN  sensors 버킷 쓰기 전용 토큰 (없으면 INFLUX_TOKEN)
  INFLUX_ORG / INFLUX_BUCKET   기본 smartcube / sensors
  WRITER_HOST_TAG     기본 jetson (telegraf 의 hostname 과 동일)
"""
import os
import threading
import time
from typing import Optional

WRITER_ENABLED = os.environ.get("WRITER_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
INFLUX_URL = os.environ.get("INFLUX_URL", "http://influxdb:8086")
INFLUX_WRITE_TOKEN = os.environ.get("INFLUX_WRITE_TOKEN", "").strip() or os.environ.get("INFLUX_TOKEN", "").strip()
INFLUX_ORG = os.environ.get("INFLUX_ORG", "smartcube")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "sensors")
HOST_TAG = os.environ.get("WRITER_HOST_TAG", "jetson")
OUT_ROOT = "smartcube"
KIND_MAP = {"public": "data", "status": "status", "ack": "ack"}

_lock = threading.Lock()
_client = None
_write_api = None
stats = {"enabled": WRITER_ENABLED, "started": False, "queued": 0, "written_lines": 0, "failed_batches": 0,
         "retried_batches": 0, "last_ok": None, "last_error": None, "last_error_at": None}


def numeric_only(obj):
    """수집기가 저장할 수 있는 숫자 필드만 남긴다. 참/거짓은 1.0/0.0, null·문자열은 버린다. (bridge.py 와 동일)"""
    out = {}
    if not isinstance(obj, dict):
        return out
    for k, v in obj.items():
        if isinstance(v, bool):
            out[k] = 1.0 if v else 0.0
        elif isinstance(v, (int, float)):
            out[k] = float(v)
    return out


def build_point(sn: str, kind: str, obj, t: Optional[float] = None):
    """원본 프레임 → Point. 숫자 필드가 없으면 None."""
    from influxdb_client import Point, WritePrecision
    out_kind = KIND_MAP.get(kind)
    if out_kind is None:
        return None
    fields = numeric_only(obj)
    if not fields:
        return None
    p = (Point("mqtt")
         .tag("cube", sn)
         .tag("kind", out_kind)
         .tag("topic", "%s/%s/ctrl/%s" % (OUT_ROOT, sn, out_kind))
         .tag("host", HOST_TAG))
    for k, v in fields.items():
        p = p.field(k, v)
    p = p.time(int((t if t is not None else time.time()) * 1e9), WritePrecision.NS)
    return p


def count_lines(data) -> int:
    """배치 본문(line protocol)의 줄 수. 클라이언트가 bytes 로 주므로 둘 다 받는다."""
    if not data:
        return 0
    sep = b"\n" if isinstance(data, (bytes, bytearray)) else "\n"
    return data.count(sep) + 1


def _on_success(conf, data):
    with _lock:
        stats["written_lines"] += count_lines(data)
        stats["last_ok"] = time.time()


def _on_error(conf, data, exception):
    with _lock:
        stats["failed_batches"] += 1
        stats["last_error"] = str(exception)[:200]
        stats["last_error_at"] = time.time()


def _on_retry(conf, data, exception):
    with _lock:
        stats["retried_batches"] += 1


def start(log=print):
    """서버 기동 때 한 번. 토큰이 없으면 끄고 경고만 남긴다."""
    global _client, _write_api
    if not WRITER_ENABLED:
        log("[writer] WRITER_ENABLED=0 — InfluxDB 기록 안 함")
        return False
    if not INFLUX_WRITE_TOKEN:
        log("[writer] INFLUX_WRITE_TOKEN 없음 — InfluxDB 기록 안 함")
        stats["enabled"] = False
        return False
    from influxdb_client import InfluxDBClient, WriteOptions
    _client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_WRITE_TOKEN, org=INFLUX_ORG)
    _write_api = _client.write_api(
        write_options=WriteOptions(batch_size=200, flush_interval=2_000, jitter_interval=0,
                                   retry_interval=5_000, max_retries=5, max_retry_delay=30_000,
                                   exponential_base=2),
        success_callback=_on_success, error_callback=_on_error, retry_callback=_on_retry)
    stats["started"] = True
    log("[writer] InfluxDB 기록 시작: %s org=%s bucket=%s (measurement mqtt, host=%s)"
        % (INFLUX_URL, INFLUX_ORG, INFLUX_BUCKET, HOST_TAG))
    return True


def record(sn: str, kind: str, obj, t: Optional[float] = None) -> bool:
    """MQTT 스레드에서 호출. 배치 큐에 넣기만 하므로 빠르다. 예외는 삼키고 통계에 남긴다."""
    if _write_api is None:
        return False
    try:
        p = build_point(sn, kind, obj, t)
        if p is None:
            return False
        _write_api.write(bucket=INFLUX_BUCKET, record=p)
        with _lock:
            stats["queued"] += 1
        return True
    except Exception as e:   # noqa: BLE001 — 기록 실패가 수신 처리를 막으면 안 된다
        with _lock:
            stats["failed_batches"] += 1
            stats["last_error"] = str(e)[:200]
            stats["last_error_at"] = time.time()
        return False


def stop():
    global _client, _write_api
    try:
        if _write_api is not None:
            _write_api.close()        # 남은 배치를 밀어 보낸다
        if _client is not None:
            _client.close()
    finally:
        _write_api = None
        _client = None
        stats["started"] = False


def status():
    with _lock:
        return dict(stats)
