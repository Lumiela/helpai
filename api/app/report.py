# -*- coding: utf-8 -*-
"""
AI 리포트 (2026-10-06 창구 → 2026-10-07 젯슨 작성 방식, 계획서
edgeserver-JS/2026-10-07_AI리포트_젯슨LangGraph_계획서v4_랩탑ClaudeCode.md §1·§15·§16).

  · 역할 분담(사용자 결정 10-07): GPU 서버 LLM 은 추론만. 재료 만들기·LangGraph·보고서 작성은 이 api 안
    (재료 텍스트 = app/report_material.py, 그래프 = app/report_graph.py).
  · 이전 방식(GPU 서버가 작성해 Funnel 10000 /report/ 로 결과를 보냄)의 결과 수신 라우트·중계 키는 지웠다(사용자 결정 10-07).
    gpu-relay 의 /report/ 는 404.

일정 (사용자 결정 10-06·10-07)
  작성 시각      02:00 고정(한국 시간). 회차의 기준 시각(anchor) = 그날 02:00
  시도           02:00~02:10 동안 1분 간격. 못 끝내면 02:30~02:40 에 같은 간격으로 다시
  성공           보고서가 끝까지 완성되면(10-07). 그 회차는 다시 하지 않는다
  시간대 넘김    시간대가 끝날 때 아직 작성 중이면 중단하고 그 시간대는 실패(10-07). 다음 시간대에 처음부터
  중간 실패      다음 시도는 처음부터(10-07)
  한도 초과      재료가 컨텍스트 한도를 넘어 LLM 서버가 거절(400)하면 그 회차는 곧바로 실패, 반복하지 않음(10-07)
  재시도도 실패  그 회차는 "실패". 사용자가 재요청하면 곧바로(차례가 되면) 다시
  리포트 간격    소유자가 1~30일 정수로 정한다(기본 1일). 간격이 1일보다 길면 하루치씩 나눠 회차를 따로 만든다(10-07,
                 나중에 바뀔 수 있음). 3일 간격이면 02:00 에 하루치 회차 3개, 차례로. 못 끝낸 하루치만 다시(10-07)
  첫 회차        설정(또는 큐브 등록) 뒤 다음 02:00(R-4)
  간격 변경      마지막 회차 기준 시각 + 새 간격(R-1). 그 시각이 이미 지났으면 지난 회차들은 시간대가 지난 "실패"
  즉시 요청      소유자가 화면의 "받기"로 시작·끝 시각을 입력(최대 24시간, 10-07). 정기 일정에 영향 없음, kind = "now"
  여러 회차      한 번에 하나씩 차례로(GPU 1장). 사람이 요청한 회차(받기·재요청)를 먼저

저장 (젯슨 파일)
  REPORT_DIR/<iid>/<cube>/settings.json                         리포트 간격, 마지막·다음 기준 시각
  REPORT_DIR/<iid>/<cube>/<YYYY-MM-DD>/<작업 ID>.job.json        회차 상태·범위·시도 기록
  REPORT_DIR/<iid>/<cube>/<YYYY-MM-DD>/<작업 ID>.json            결과(그래프 출력). 다시 쓰면 이전 파일은 백업(R-6)
  REPORT_DIR/<iid>/<cube>/<YYYY-MM-DD>/<작업 ID>.input.txt       LLM 에 보낸 재료 텍스트(10-07)
  작업 ID = <iid>.<cube>.<YYYYMMDD>T<HHMM> (데이터 범위 끝, 한국 시간). 날짜 폴더도 범위 끝 날짜
            즉시 요청은 <iid>.<cube>.<YYYYMMDD>T<HHMMSS> (요청 시각)

회차 상태: pending(대기) → writing(작성 중, 진행 단계 표시) → done(완료) / failed(실패)
"""
import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from . import audit, auth, registry
from . import report_graph as rg
from . import report_material as rm

KST = timezone(timedelta(hours=9))

REPORT_DIR = os.environ.get("REPORT_DIR", "/data/reports")
EVERY = os.environ.get("REPORT_EVERY", "10m").strip() or "10m"     # 재료 집계 간격(R-8: 지금 10분 평균, 나중에 조절)
FIELDS = [f.strip() for f in os.environ.get(
    "REPORT_FIELDS", "temperature,humidity,lux,moisture_pct,fan1,led1,pump").split(",") if f.strip()]
AUDIT_ACTIONS = ("auto_control", "auto_off_manual", "crop_set", "auto_set")   # 재료의 자동 제어 기록

ANCHOR_HOUR, ANCHOR_MINUTE = 2, 0          # 02:00 고정
WINDOW_S = 600                             # 02:00~02:10
RETRY_OFFSET_S = 1800                      # 02:30~02:40
ATTEMPT_EVERY_S = 60                       # 1분 간격
INTERVAL_MIN, INTERVAL_MAX, INTERVAL_DEFAULT = 1, 30, 1
DAY_S = 86400
NOW_MAX_S = DAY_S                          # 받기 범위 최대 24시간(10-07)
TICK_S = float(os.environ.get("REPORT_TICK_SECONDS", "15") or "15")
CATCHUP_MAX = 60                           # 한 번에 만드는 지난 기준 시각 상한(안전장치)
ATTEMPTS_KEEP = 60

IID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SN_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
JOB_RE = re.compile(r"^([a-z0-9][a-z0-9_-]{0,63})\.([A-Za-z0-9_-]{1,32})\.(\d{8})T(\d{4}|\d{6})$")
EVERY_RE = re.compile(r"^\d{1,4}[smhd]$")
FIELD_RE = re.compile(r"^[a-z0-9_]{1,40}$")

STATUSES = ("pending", "writing", "done", "failed")
RETRYABLE = ("failed",)

_log = print
_jobs: Dict[str, dict] = {}                # job_id -> 회차 기록(파일과 같음)
_settings: Dict[str, dict] = {}            # "<iid>/<cube>" -> settings
_inputs: Dict[str, dict] = {}              # job_id -> 만든 재료(같은 회차 재시도에 재사용)
_run_lock: Optional[asyncio.Lock] = None
_wake: Optional[asyncio.Event] = None
_hooks: Dict[str, Callable] = {}
stats = {"running": False, "loaded": 0, "attempts": 0, "done": 0, "failed_attempts": 0,
         "last_error": None, "last_tick": None, "current": None}

router = APIRouter()


def set_logger(fn):
    global _log
    _log = fn


def bind(*, series: Callable, crop: Optional[Callable] = None):
    """series(cube, start_utc_iso, stop_utc_iso, fields, every) -> {field: [[iso, value], …]}
    crop(cube) -> {"crop_id", "profile", "auto_mode"} | None"""
    _hooks["series"] = series
    if crop is not None:
        _hooks["crop"] = crop


# ──────────────────────────────────────────────── 시각

def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, KST).isoformat()


def _utc(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_anchor_after(t: float) -> float:
    """t 이후(같거나 뒤) 가장 가까운 02:00 KST."""
    d = datetime.fromtimestamp(t, KST)
    a = d.replace(hour=ANCHOR_HOUR, minute=ANCHOR_MINUTE, second=0, microsecond=0)
    if a.timestamp() < t:
        a = a + timedelta(days=1)
    return a.timestamp()


def add_days(anchor: float, days: int) -> float:
    d = datetime.fromtimestamp(anchor, KST) + timedelta(days=days)
    return d.replace(hour=ANCHOR_HOUR, minute=ANCHOR_MINUTE, second=0, microsecond=0).timestamp()


def job_id_for(iid: str, cube: str, period_end: float) -> str:
    return "%s.%s.%s" % (iid, cube, datetime.fromtimestamp(period_end, KST).strftime("%Y%m%dT%H%M"))


def _day(t: float) -> str:
    return datetime.fromtimestamp(t, KST).strftime("%Y-%m-%d")


def window_of(anchor: float, now: float) -> str:
    """main | gap | retry | over | before"""
    dt = now - anchor
    if dt < 0:
        return "before"
    if dt < WINDOW_S:
        return "main"
    if dt < RETRY_OFFSET_S:
        return "gap"
    if dt < RETRY_OFFSET_S + WINDOW_S:
        return "retry"
    return "over"


def window_end(anchor: float, window: str) -> Optional[float]:
    if window == "main":
        return anchor + WINDOW_S
    if window == "retry":
        return anchor + RETRY_OFFSET_S + WINDOW_S
    return None


# ──────────────────────────────────────────────── 파일

def _cube_dir(iid: str, cube: str) -> str:
    return os.path.join(REPORT_DIR, iid, cube)


def _job_paths(job: dict):
    d = os.path.join(_cube_dir(job["instance"], job["cube"]), job["date"])
    base = os.path.join(d, job["job_id"])
    return d, base + ".job.json", base + ".json", base + ".input.txt"


def _write_json(path: str, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _write_text(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _save_job(job: dict):
    _write_json(_job_paths(job)[1], job)


def _key(iid: str, cube: str) -> str:
    return iid + "/" + cube


def settings(iid: str, cube: str, now: Optional[float] = None) -> dict:
    """큐브 설정. 없으면 기본값(간격 1일, 다음 02:00)으로 만든다(R-4)."""
    k = _key(iid, cube)
    s = _settings.get(k)
    if s is not None:
        return s
    path = os.path.join(_cube_dir(iid, cube), "settings.json")
    try:
        with open(path, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = None
    if not isinstance(s, dict) or not isinstance(s.get("interval_days"), int):
        now = time.time() if now is None else now
        s = {"interval_days": INTERVAL_DEFAULT, "last_anchor": None, "next_anchor": next_anchor_after(now),
             "updated_at": now}
        _write_json(path, s)
    _settings[k] = s
    return s


def _save_settings(iid: str, cube: str, s: dict):
    _settings[_key(iid, cube)] = s
    _write_json(os.path.join(_cube_dir(iid, cube), "settings.json"), s)


def set_interval(iid: str, cube: str, days: int, now: Optional[float] = None) -> dict:
    """간격 변경(R-1): 마지막 회차 기준 시각 + 새 간격. 회차가 아직 없으면 다음 02:00(R-4)."""
    now = time.time() if now is None else now
    s = dict(settings(iid, cube, now))
    s["interval_days"] = int(days)
    s["next_anchor"] = add_days(s["last_anchor"], days) if s.get("last_anchor") else next_anchor_after(now)
    s["updated_at"] = now
    _save_settings(iid, cube, s)
    return s


# 이전 방식(10-06, GPU 서버 작성)의 상태 → 지금 상태
_LEGACY = {"sending": "pending", "accepted": "failed", "no_result": "failed"}


def load():
    """기동 때 회차 파일을 읽어 메모리 색인을 만든다. 작성 중이던 회차는 대기(정기)·실패(사람 요청)로 바꾼다."""
    _jobs.clear()
    _settings.clear()
    _inputs.clear()
    n = 0
    if not os.path.isdir(REPORT_DIR):
        os.makedirs(REPORT_DIR, exist_ok=True)
    for root, _dirs, files in os.walk(REPORT_DIR):
        for name in files:
            if not name.endswith(".job.json"):
                continue
            try:
                with open(os.path.join(root, name), encoding="utf-8") as f:
                    job = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(job, dict) or not JOB_RE.match(str(job.get("job_id", ""))):
                continue
            st = job.get("status")
            if st in _LEGACY:
                job["status"] = _LEGACY[st]
                if job["status"] == "failed":
                    job["reason"] = job.get("reason") or "이전 방식(GPU 서버 작성)의 회차 — 재요청하면 젯슨에서 작성"
            elif st == "writing":
                job["progress"] = None
                if job.get("manual"):
                    job.update(status="failed", manual=False, reason="api 재시작으로 작성 중단")
                else:
                    job["status"] = "pending"
            elif st not in STATUSES:
                job["status"] = "failed"
            _jobs[job["job_id"]] = job
            n += 1
    stats["loaded"] = n
    return n


# ──────────────────────────────────────────────── 회차

def _job(iid: str, cube: str, jid: str, date: str, anchor: float, start: float, end: float, interval_days,
         kind: str, part, now: float) -> dict:
    return {"job_id": jid, "instance": iid, "cube": cube, "date": date,
            "anchor": _iso(anchor), "anchor_ts": anchor,
            "period_start": _iso(start), "period_end": _iso(end), "period_start_ts": start, "period_end_ts": end,
            "interval_days": interval_days, "part": part, "kind": kind, "status": "pending",
            "manual": kind == "now", "reason": None, "created_at": _iso(now),
            "started_at": None, "done_at": None, "progress": None, "result_bytes": None, "input_bytes": None,
            "overwrites": 0, "retries": 0, "attempts": []}


def _new_jobs(iid: str, cube: str, anchor: float, days: int, now: float) -> List[dict]:
    """기준 시각 하나 → 하루치 회차 days 개(차례로: 오래된 날부터). 같은 작업 ID 가 이미 있으면 건너뛴다."""
    made = []
    for i in range(days):
        end = add_days(anchor, -(days - 1 - i))
        start = add_days(end, -1)
        jid = job_id_for(iid, cube, end)
        if jid in _jobs:
            continue
        job = _job(iid, cube, jid, _day(end), anchor, start, end, days, "scheduled", [i + 1, days], now)
        if window_of(anchor, now) == "over":
            job["status"] = "failed"
            job["reason"] = "작성 시간대가 지남(서버가 그 시각에 판단하지 못함)"
        _jobs[jid] = job
        _save_job(job)
        made.append(job)
    return made


def new_now_job(iid: str, cube: str, start_ts: float, end_ts: float, now: Optional[float] = None) -> dict:
    """즉시 요청 회차. 범위는 사용자가 입력한 [시작, 끝). 정기 일정(settings)은 건드리지 않는다."""
    t = int(time.time() if now is None else now)
    jid = "%s.%s.%s" % (iid, cube, datetime.fromtimestamp(t, KST).strftime("%Y%m%dT%H%M%S"))
    while jid in _jobs:
        t += 1
        jid = "%s.%s.%s" % (iid, cube, datetime.fromtimestamp(t, KST).strftime("%Y%m%dT%H%M%S"))
    job = _job(iid, cube, jid, _day(t), float(t), start_ts, end_ts, round((end_ts - start_ts) / DAY_S, 3),
               "now", None, t)
    _jobs[jid] = job
    _save_job(job)
    return job


def ensure_jobs(iid: str, cube: str, now: float) -> List[dict]:
    """기준 시각이 된 회차를 만든다. 지난 기준 시각이 여러 개면 기준 시각마다 따로(데이터는 항상 설정 간격)."""
    s = settings(iid, cube, now)
    made = []
    for _ in range(CATCHUP_MAX):
        nxt = s.get("next_anchor")
        if not nxt or nxt > now:
            break
        days = int(s["interval_days"])
        made += _new_jobs(iid, cube, nxt, days, now)
        s = dict(s, last_anchor=nxt, next_anchor=add_days(nxt, days), updated_at=now)
        _save_settings(iid, cube, s)
    return made


def due_jobs(now: float) -> List[dict]:
    """지금 작성할 회차. 사람이 요청한 회차(받기·재요청)를 먼저, 그다음 정기 회차(기준 시각 → 큐브 → 날짜 순).
    시간대가 끝난 정기 회차는 실패로 바꾼다."""
    manual, sched = [], []
    for job in _jobs.values():
        if job["status"] != "pending":
            continue
        if job.get("manual"):
            manual.append(job)
            continue
        w = window_of(job["anchor_ts"], now)
        if w == "over":
            job["status"] = "failed"
            job["reason"] = job.get("reason") or "작성 시간대(02:00~02:10, 02:30~02:40) 안에 완성되지 않음"
            _save_job(job)
            continue
        if w not in ("main", "retry"):
            continue
        last = job["attempts"][-1]["ts"] if job["attempts"] else None
        if last is not None and now - last < ATTEMPT_EVERY_S - 1:
            continue
        sched.append(job)
    manual.sort(key=lambda j: (j.get("queued_ts") or 0, j["job_id"]))
    sched.sort(key=lambda j: (j["anchor_ts"], j["cube"], j["period_end_ts"]))
    return manual + sched


# ──────────────────────────────────────────────── 재료

async def _auto_records(iid: str, cube: str, start_ts: float, stop_ts: float):
    """Supabase audit 에서 그 기간의 자동 제어 기록. 실패하면 (None, 사유)."""
    if not audit.ENABLED:
        return None, "Supabase 미설정"
    params = [("select", "at,action,email,payload,result"), ("sn", "eq." + cube), ("instance_id", "eq." + iid),
              ("action", "in.(%s)" % ",".join(AUDIT_ACTIONS)),
              ("at", "gte." + _utc(start_ts)), ("at", "lt." + _utc(stop_ts)), ("order", "at.asc"), ("limit", "5000")]
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(audit.SUPABASE_URL + "/rest/v1/audit", params=params,
                            headers={"apikey": audit.SUPABASE_SERVICE_KEY,
                                     "Authorization": "Bearer " + audit.SUPABASE_SERVICE_KEY})
        if r.status_code >= 300:
            return None, "HTTP %d" % r.status_code
        return r.json(), None
    except Exception as e:   # noqa: BLE001
        return None, str(e)[:120]


async def build_input(job: dict) -> dict:
    """재료 텍스트(app/report_material.py). 센서 값 조회가 실패하면 예외 — 빈 재료로 작성하지 않는다."""
    jid = job["job_id"]
    if jid in _inputs:
        return _inputs[jid]
    every = EVERY if EVERY_RE.match(EVERY) else "10m"
    fields = [f for f in FIELDS if FIELD_RE.match(f)]
    fn = _hooks.get("series")
    if fn is None:
        raise RuntimeError("이력 조회 함수가 연결되지 않음")
    try:
        series = await asyncio.to_thread(fn, job["cube"], _utc(job["period_start_ts"]), _utc(job["period_end_ts"]),
                                         fields, every)
    except Exception as e:   # noqa: BLE001
        raise RuntimeError("센서 값 조회 실패: %s" % str(e)[:160]) from e
    records, rec_error = await _auto_records(job["instance"], job["cube"], job["period_start_ts"], job["period_end_ts"])
    crop = None
    if "crop" in _hooks:
        try:
            crop = _hooks["crop"](job["cube"])
        except Exception:   # noqa: BLE001
            crop = None
    data = rm.build(period_start=job["period_start_ts"], period_end=job["period_end_ts"], every=every, fields=fields,
                    series=series, crop=crop, records=records, records_error=rec_error)
    if rec_error is None:
        _inputs[jid] = data
    return data


# ──────────────────────────────────────────────── 작성

def _attempt_reason(job: dict, err: str) -> str:
    return "%s 실패: %s" % ("재요청" if job.get("retries") else "요청", err)


async def _write(job: dict, attempt: dict, clock: Callable[[], float]):
    """재료 → 그래프 → 결과 저장. 실패하면 예외."""
    if not rg.ready():
        raise RuntimeError("LLM 서버 주소·키 미설정(REPORT_LLM_URL, HELPAI_LLM_KEY)")
    data = await build_input(job)
    _d, _jp, rp, ip = _job_paths(job)
    _write_text(ip, data["text"])
    job["input_bytes"] = data["bytes"]
    attempt.update(input_bytes=data["bytes"], rows=data["rows"], records=data["records"])

    def on_step(i, n, name, label):
        job["progress"] = {"step": i, "total": n, "name": name, "label": label}
        _save_job(job)

    out = await rg.run(data["text"], on_step=on_step)
    attempt["calls"] = out["calls"]
    now = clock()
    result = {"job_id": job["job_id"], "instance": job["instance"], "cube": job["cube"], "kind": job.get("kind"),
              "period_start": job["period_start"], "period_end": job["period_end"], "generated_at": _iso(now),
              "graph": rg.GRAPH_NAME, "steps": out["outputs"],
              "llm": {"calls": out["calls"], "truncated": any(c.get("finish_reason") == "length" for c in out["calls"])}}
    if os.path.exists(rp):
        os.replace(rp, rp + ".bak-" + datetime.fromtimestamp(now, KST).strftime("%Y%m%d-%H%M%S"))
        job["overwrites"] = int(job.get("overwrites") or 0) + 1
    _write_json(rp, result)
    job["result_bytes"] = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))


async def attempt(job: dict, clock: Callable[[], float], window: str) -> bool:
    """한 회차를 한 번 작성한다. 정기 회차는 시간대가 끝나면 중단(10-07)."""
    now = clock()
    end = window_end(job["anchor_ts"], window) if not job.get("manual") else None
    if end is not None and end - now <= 0:
        return False
    att = {"ts": now, "at": _iso(now), "window": window, "ok": False, "error": None, "kind": None}
    job.update(status="writing", started_at=_iso(now), progress=None)
    _save_job(job)
    stats["current"] = job["job_id"]
    stats["attempts"] += 1
    ok, final = False, False
    to = asyncio.timeout(None if end is None else max(0.0, end - now))
    try:
        async with to:
            await _write(job, att, clock)
        ok = True
    except TimeoutError as e:
        if to.expired():
            att.update(error="작성 시간대 종료(%s)로 중단" % ("02:10" if window == "main" else "02:40"), kind="window_end")
        else:
            att.update(error=str(e)[:200] or "시간 초과", kind="error")
    except rg.ContextOverflow as e:
        att.update(error="컨텍스트 한도 초과: %s" % e, kind="context")
        final = True
    except asyncio.CancelledError:
        att.update(error="api 종료로 중단", kind="cancelled")
        job.update(status="pending" if not job.get("manual") else "failed", progress=None,
                   attempts=(job.get("attempts") or [])[-(ATTEMPTS_KEEP - 1):] + [att])
        _save_job(job)
        raise
    except Exception as e:   # noqa: BLE001
        att.update(error=(str(e) or type(e).__name__)[:200], kind="error")
    finally:
        stats["current"] = None
    att["ok"] = ok
    job["attempts"] = (job.get("attempts") or [])[-(ATTEMPTS_KEEP - 1):] + [att]
    job["progress"] = None
    if ok:
        stats["done"] += 1
        job.update(status="done", done_at=_iso(clock()), reason=None, manual=False)
        _inputs.pop(job["job_id"], None)
    else:
        stats["failed_attempts"] += 1
        stats["last_error"] = att["error"]
        if job.get("manual"):
            job.update(status="failed", manual=False, reason=_attempt_reason(job, att["error"]))
        elif final:
            job.update(status="failed", reason=att["error"])
        else:
            job["status"] = "pending"
        if job["status"] == "failed":
            _inputs.pop(job["job_id"], None)
    _save_job(job)
    _log("[report] %s %s → %s%s" % (job["job_id"], window, "완료" if ok else "실패",
                                    "" if ok else " (%s)" % att["error"]))
    return ok


def _lock() -> asyncio.Lock:
    global _run_lock
    if _run_lock is None:
        _run_lock = asyncio.Lock()
    return _run_lock


def wake():
    if _wake is not None:
        _wake.set()


def _clock(now: Optional[float]) -> Callable[[], float]:
    if now is None:
        return time.time
    m0 = time.monotonic()
    return lambda: now + (time.monotonic() - m0)


async def tick(now: Optional[float] = None) -> int:
    """회차 만들기 → 작성할 회차를 하나씩 차례로 작성. 시도한 수를 돌려준다.
    now 를 주면(시험) 그 시각부터 실제 흐른 시간만큼 시계가 간다."""
    clock = _clock(now)
    t = clock()
    stats["last_tick"] = _iso(t)
    for iid in registry.ids():
        for cube in registry.cubes_of(iid):
            ensure_jobs(iid, cube, t)
    n = 0
    async with _lock():
        for job in due_jobs(clock()):
            if job["status"] != "pending":
                continue
            if job.get("manual"):
                w = "now" if job.get("kind") == "now" and not job.get("retries") else "manual"
            else:
                w = window_of(job["anchor_ts"], clock())
                if w not in ("main", "retry"):
                    continue
            await attempt(job, clock, w)
            n += 1
    return n


async def run():
    global _wake
    _wake = asyncio.Event()
    stats["running"] = True
    _log("[report] 리포트 일정 루프 시작: 02:00 KST, 저장 %s, LLM %s, 그래프 %s" % (
        REPORT_DIR, "설정됨" if rg.ready() else "미설정(REPORT_LLM_URL, HELPAI_LLM_KEY)", rg.GRAPH_NAME))
    try:
        while True:
            try:
                await asyncio.wait_for(_wake.wait(), timeout=TICK_S)
            except TimeoutError:
                pass
            _wake.clear()
            try:
                await tick()
            except Exception as e:   # noqa: BLE001
                stats["last_error"] = str(e)[:200]
                _log("[report] 일정 오류: %s" % str(e)[:160])
    except asyncio.CancelledError:
        stats["running"] = False
        return


async def close():
    """api 종료 때(main.py lifespan). 작성 중이던 회차는 attempt() 가 취소를 받아 기록한다."""
    return None


def status():
    counts = {s: 0 for s in STATUSES}
    for j in _jobs.values():
        counts[j["status"]] = counts.get(j["status"], 0) + 1
    return {"dir": REPORT_DIR, "llm_ready": rg.ready(), "graph": rg.GRAPH_NAME,
            "every": EVERY, "fields": FIELDS, "jobs": counts, **stats}


def read_result(job: dict):
    rp = _job_paths(job)[2]
    try:
        with open(rp, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def public_job(job: dict, attempts: bool = False) -> dict:
    d = {k: v for k, v in job.items() if not k.endswith("_ts") and k != "attempts"}
    d.setdefault("kind", "scheduled")
    d["attempt_count"] = len(job.get("attempts") or [])
    d["last_attempt"] = (job.get("attempts") or [None])[-1]
    if attempts:
        d["attempts"] = job.get("attempts") or []
    return d


# ──────────────────────────────────────────────── 라우트: 대시보드용 (443 /i/<iid>/api/…)

def _check_cube(iid: str, sn: str) -> str:
    if not SN_RE.match(sn):
        raise HTTPException(400, "잘못된 일련번호")
    if registry.instance_of(sn) != iid:
        raise HTTPException(404, "이 인스턴스에 등록되지 않은 큐브")
    return sn


def _settings_view(iid: str, sn: str) -> dict:
    s = settings(iid, sn)
    return {"sn": sn, "interval_days": s["interval_days"], "min": INTERVAL_MIN, "max": INTERVAL_MAX,
            "anchor_time": "%02d:%02d" % (ANCHOR_HOUR, ANCHOR_MINUTE),
            "windows": ["02:00-02:10", "02:30-02:40"],
            "last_anchor": _iso(s["last_anchor"]) if s.get("last_anchor") else None,
            "next_anchor": _iso(s["next_anchor"]) if s.get("next_anchor") else None,
            "next_period_start": _iso(s["next_anchor"] - s["interval_days"] * DAY_S) if s.get("next_anchor") else None,
            "now_max_hours": NOW_MAX_S // 3600, "llm_ready": rg.ready(), "graph": rg.GRAPH_NAME}


@router.get("/i/{iid}/cubes/{sn}/report-settings")
def settings_get(iid: str, sn: str, user: auth.User = Depends(auth.reader)):
    _check_cube(iid, sn)
    return _settings_view(iid, sn)


class IntervalSet(BaseModel):
    interval_days: int = Field(..., ge=INTERVAL_MIN, le=INTERVAL_MAX)


@router.put("/i/{iid}/cubes/{sn}/report-settings")
async def settings_put(iid: str, sn: str, body: IntervalSet, user: auth.User = Depends(auth.owner)):
    _check_cube(iid, sn)
    before = settings(iid, sn)["interval_days"]
    set_interval(iid, sn, body.interval_days)
    audit.fire(iid, user, sn, "report_settings", {"interval_days": {"from": before, "to": body.interval_days}}, "ok")
    return _settings_view(iid, sn)


@router.get("/i/{iid}/cubes/{sn}/reports")
def reports_list(iid: str, sn: str, limit: int = Query(50, ge=1, le=500),
                 user: auth.User = Depends(auth.reader)):
    _check_cube(iid, sn)
    jobs = [j for j in _jobs.values() if j["instance"] == iid and j["cube"] == sn]
    jobs.sort(key=lambda j: (j["anchor_ts"], j["period_end_ts"]), reverse=True)
    return {"sn": sn, "reports": [public_job(j) for j in jobs[:limit]], "total": len(jobs)}


def _job_or_404(job_id: str) -> dict:
    if not JOB_RE.match(job_id or ""):
        raise HTTPException(404, "작업 없음")
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "작업 없음")
    return job


def _own_job(iid: str, sn: str, job_id: str) -> dict:
    job = _job_or_404(job_id)
    if job["instance"] != iid or job["cube"] != sn:
        raise HTTPException(404, "작업 없음")
    return job


@router.get("/i/{iid}/cubes/{sn}/reports/{job_id}")
def report_get(iid: str, sn: str, job_id: str, user: auth.User = Depends(auth.reader)):
    _check_cube(iid, sn)
    job = _own_job(iid, sn, job_id)
    out = public_job(job, attempts=True)
    out["result"] = read_result(job) if job["status"] == "done" else None
    return out


class NowRequest(BaseModel):
    start: datetime
    end: datetime


def _ts(d: datetime) -> float:
    return (d if d.tzinfo else d.replace(tzinfo=KST)).timestamp()


@router.post("/i/{iid}/cubes/{sn}/reports/now")
async def report_now(iid: str, sn: str, body: NowRequest, user: auth.User = Depends(auth.owner)):
    """즉시 요청(화면의 "받기"): 입력한 범위(최대 24시간)로 회차를 만들고 차례가 되면 곧바로 작성한다. 정기 일정에는 영향 없음."""
    _check_cube(iid, sn)
    st, et, now = _ts(body.start), _ts(body.end), time.time()
    if not st < et:
        raise HTTPException(400, "시작 시각이 끝 시각보다 앞이어야 합니다")
    if et > now + 60:
        raise HTTPException(400, "끝 시각이 지금보다 뒤입니다")
    if et - st > NOW_MAX_S:
        raise HTTPException(400, "범위는 최대 %d시간입니다" % (NOW_MAX_S // 3600))
    job = new_now_job(iid, sn, st, et, now)
    job["queued_ts"] = now
    _save_job(job)
    wake()
    audit.fire(iid, user, sn, "report_now", {"job_id": job["job_id"], "start": job["period_start"], "end": job["period_end"]},
               "queued")
    return public_job(job)


@router.post("/i/{iid}/cubes/{sn}/reports/{job_id}/retry")
async def report_retry(iid: str, sn: str, job_id: str, user: auth.User = Depends(auth.owner)):
    """실패 회차를 다시 작성한다(사용자 결정). 그 회차의 범위 그대로, 차례가 되면 곧바로."""
    _check_cube(iid, sn)
    job = _own_job(iid, sn, job_id)
    if job["status"] not in RETRYABLE:
        raise HTTPException(409, "재요청할 수 있는 상태가 아님(%s)" % job["status"])
    job.update(status="pending", manual=True, retries=int(job.get("retries") or 0) + 1, queued_ts=time.time(),
               reason=None)
    _save_job(job)
    wake()
    audit.fire(iid, user, sn, "report_retry", {"job_id": job_id}, "queued")
    return public_job(job)
