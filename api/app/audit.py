# -*- coding: utf-8 -*-
"""
조작·접근 기록 — Supabase 의 audit 표에 남긴다 (2026-09-28, 09-29 인스턴스 ID 추가, 09-29 오후 다중 인스턴스: 호출마다 iid).

  · 비동기·최선 노력이다. 기록에 실패해도 제어는 그대로 진행되고, 실패는 로그와 /health 통계에만 남는다.
  · Supabase REST(PostgREST)에 서비스 키로 쓴다. 서비스 키는 행 단위 보안을 우회하므로 이 서버 밖으로
    나가면 안 된다(.env 에만).
  · SUPABASE_URL 이나 SUPABASE_SERVICE_KEY 가 없으면 로그 한 줄로 대신한다.

audit 표 열: at, instance_id, user_id(uuid|null), email, sn, action, payload(jsonb), result
  action: control | config | refresh | operator_access | operator_access_denied | operator_release | claim | claim_denied | cube_add | cube_add_denied | broker_view | broker_rotate | camera_add | snapshot_request | stream_start | stream_stop
          | auto_control(자동 제어 명령, email "auto") | auto_off_manual(사람 조작으로 자동 꺼짐) | crop_set | auto_set   (2026-10-06)
          | report_settings | report_retry | report_now(AI 리포트 간격 변경·재요청·즉시 요청)   (2026-10-06)
  sn: 큐브 일련번호. 인스턴스 단위 동작은 "*"
"""
import asyncio
import json
import os
import time
from typing import Optional

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
ENABLED = bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)
TIMEOUT_S = 5.0

stats = {"enabled": ENABLED, "sent": 0, "failed": 0, "last_error": None, "last_error_at": None}
_client = None
_log = print


def set_logger(fn):
    global _log
    _log = fn


def _http():
    global _client
    if _client is None:
        import httpx
        _client = httpx.AsyncClient(timeout=TIMEOUT_S)
    return _client


async def close():
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        finally:
            _client = None


async def record(iid: str, user, sn: str, action: str, payload, result: str) -> bool:
    email = getattr(user, "email", "") or ""
    user_uuid = user.user_uuid() if hasattr(user, "user_uuid") else None
    line = "[audit] %s %s %s %s %s → %s" % (iid or "-", email or "(익명)", action, sn, json.dumps(payload, ensure_ascii=False), result)
    if not ENABLED:
        _log(line + "  (Supabase 미설정, 로그만)")
        return False
    body = {"instance_id": iid or None, "user_id": user_uuid, "email": email or None,
            "sn": sn, "action": action, "payload": payload, "result": result}
    try:
        r = await _http().post(
            SUPABASE_URL + "/rest/v1/audit",
            json=body,
            headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": "Bearer " + SUPABASE_SERVICE_KEY,
                     "Prefer": "return=minimal", "Content-Type": "application/json"})
        if r.status_code >= 300:
            raise RuntimeError("HTTP %d %s" % (r.status_code, r.text[:120]))
        stats["sent"] += 1
        return True
    except Exception as e:   # noqa: BLE001
        stats["failed"] += 1
        stats["last_error"] = str(e)[:200]
        stats["last_error_at"] = time.time()
        _log(line + "  (기록 실패: %s)" % str(e)[:120])
        return False


def fire(iid: str, user, sn: str, action: str, payload, result: str) -> Optional[asyncio.Task]:
    """이벤트 루프 안에서 호출. 기다리지 않는다."""
    try:
        return asyncio.get_running_loop().create_task(record(iid, user, sn, action, payload, result))
    except RuntimeError:
        _log("[audit] 이벤트 루프 밖에서 호출됨 — 기록 생략")
        return None


def status():
    return dict(stats)
