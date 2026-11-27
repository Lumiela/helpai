# -*- coding: utf-8 -*-
"""
shadow 수집기 — 젯슨에서 1 분마다 서버 api 를 **읽기만** 하고, 모델 답과 autoctl 기준 판단을 나란히 기록한다.

    서버 api (읽기 전용 GET 4 개, 소유자 토큰)
      /i/{iid}/cubes/{sn}/values    지금 센서·출력값, seen(epoch)
      /i/{iid}/cubes/{sn}/status    토양 보정값 air·water
      /i/{iid}/cubes/{sn}/auto      autoctl 상태(enabled·rules·alerts·last_action·pump_count·pump_last_at)
      /i/{iid}/cubes/{sn}/history   최근 35 분(추세·고착·급수 후 수분) + 오늘 펌프(autoctl 기록이 없을 때)
    → Snapshot → state(features.py) → laya-edge /v1/systemone → 묶음 calibration.yaml 적용
    → 기준 판단(labeler.HARD = autoctl 규칙) 과 함께 <log_dir>/shadow-YYYY-MM-DD.jsonl (KST 날짜)

★ 명령을 보내는 코드는 없다. ApiClient 는 GET 만, 경로 끝은 values·status·auto·history 만 허용한다.
  autoctl 이 켜진 큐브에 제어 라우트로 명령을 보내면 사람 조작으로 처리돼 autoctl 이 꺼진다(docs/05 §7).

인증: 서버는 Supabase 로그인 토큰(JWT, 보통 1 시간)을 받는다. 서비스 토큰이 따로 없어서
  auth.mode = supabase_refresh  — refresh token 으로 access token 을 스스로 갱신(새 refresh token 은 파일에 저장, 0600)
  auth.mode = static            — 환경변수의 Bearer 토큰 그대로(시험·짧은 확인용)
  auth.mode = none              — 서버가 AUTH_MODE=off 일 때만
토큰 값은 절대 기록·출력하지 않는다.
"""
import base64
import json
import os
import signal
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import calibration as cal
from . import labeler
from .features import FEATURE_VERSION, Snapshot, build_state, soil_calibrated_from_status
from .predict import HttpPredictor, normalize_response
from .questions import load_questions, questions_sha256

KST = timezone(timedelta(hours=9))
UTC = timezone.utc
ALLOWED_LEAVES = ("values", "status", "auto", "history")
HISTORY_FIELDS = ("temperature", "humidity", "moisture_pct", "pump")


# ───────────────────────────────────────── 인증
class AuthError(Exception):
    pass


def _jwt_exp(token):
    """서명 검증 없이 exp 만 읽는다(갱신 시점 판단용). 못 읽으면 None."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload)).get("exp"))
    except Exception:
        return None


class TokenProvider:
    def __init__(self, cfg, env=os.environ, opener=None, clock=time.time):
        self.mode = (cfg or {}).get("mode", "supabase_refresh")
        self.cfg = cfg or {}
        self.env = env
        self.clock = clock
        self.opener = opener or urllib.request.urlopen
        self._access = None
        self._exp = 0.0

    def _refresh_token(self):
        f = self.cfg.get("refresh_token_file")
        if f and Path(f).exists():
            t = Path(f).read_text(encoding="utf-8").strip()
            if t:
                return t
        t = self.env.get(self.cfg.get("refresh_token_env", "SUPABASE_REFRESH_TOKEN"), "").strip()
        if not t:
            raise AuthError("refresh token 이 없다 — 환경변수 %s 또는 파일 %s"
                            % (self.cfg.get("refresh_token_env", "SUPABASE_REFRESH_TOKEN"), f))
        return t

    def _save_refresh_token(self, token):
        f = self.cfg.get("refresh_token_file")
        if not f:
            return
        p = Path(f)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(token, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, p)

    def _refresh(self):
        url = self.env.get(self.cfg.get("supabase_url_env", "SUPABASE_URL"), "").rstrip("/")
        key = self.env.get(self.cfg.get("anon_key_env", "SUPABASE_ANON_KEY"), "")
        if not url or not key:
            raise AuthError("SUPABASE_URL·SUPABASE_ANON_KEY 환경변수가 필요하다")
        body = json.dumps({"refresh_token": self._refresh_token()}).encode()
        req = urllib.request.Request(url + "/auth/v1/token?grant_type=refresh_token", data=body, method="POST",
                                     headers={"apikey": key, "Content-Type": "application/json"})
        try:
            with self.opener(req, timeout=15) as r:
                d = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise AuthError("토큰 갱신 실패 HTTP %d — refresh token 이 만료·재사용됐을 수 있다(매뉴얼 03 §5)" % e.code)
        except (urllib.error.URLError, OSError) as e:
            raise AuthError("토큰 갱신 실패: %s" % e.__class__.__name__)
        if not d.get("access_token"):
            raise AuthError("토큰 갱신 응답에 access_token 이 없다")
        if d.get("refresh_token"):
            self._save_refresh_token(d["refresh_token"])        # Supabase refresh token 은 1 회용 — 새 것을 저장
        self._access = d["access_token"]
        self._exp = _jwt_exp(self._access) or (self.clock() + float(d.get("expires_in", 3600)))

    def header(self):
        if self.mode == "none":
            return None
        if self.mode == "static":
            t = self.env.get(self.cfg.get("static_token_env", "HELPAI_TOKEN"), "").strip()
            if not t:
                raise AuthError("환경변수 %s 에 토큰이 없다" % self.cfg.get("static_token_env", "HELPAI_TOKEN"))
            return "Bearer " + t
        if self._access is None or self.clock() > self._exp - 120:
            self._refresh()
        return "Bearer " + self._access

    def invalidate(self):
        self._access, self._exp = None, 0.0


# ───────────────────────────────────────── 서버 api (읽기 전용)
class ApiError(Exception):
    def __init__(self, status, detail=""):
        super().__init__("HTTP %s %s" % (status, detail))
        self.status = status
        self.detail = detail


class ApiClient:
    """GET 만 한다. 제어·설정 경로는 만들 수 없다."""

    def __init__(self, base_url, instance, tokens, timeout=10.0, opener=None):
        if not instance:
            raise ValueError("api.instance(인스턴스 ID)가 필요하다 — configs/collector.yaml 또는 COLLECTOR_INSTANCE")
        self.base = base_url.rstrip("/")
        self.iid = instance
        self.tokens = tokens
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen

    def url(self, sn, leaf, params=None):
        if leaf not in ALLOWED_LEAVES:
            raise ValueError("읽기 전용 수집기: %r 경로는 허용하지 않는다" % leaf)
        u = "%s/i/%s/cubes/%s/%s" % (self.base, urllib.parse.quote(self.iid, safe=""),
                                     urllib.parse.quote(sn, safe=""), leaf)
        if params:
            u += "?" + urllib.parse.urlencode(params)
        return u

    def get(self, sn, leaf, params=None, _retry=True):
        req = urllib.request.Request(self.url(sn, leaf, params), method="GET")
        h = self.tokens.header()
        if h:
            req.add_header("Authorization", h)
        try:
            with self.opener(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode()).get("detail", "")
            except Exception:
                pass
            finally:
                e.close()
            if e.code == 401 and _retry:
                self.tokens.invalidate()                         # 만료 직전 경계 — 한 번만 다시
                return self.get(sn, leaf, params, _retry=False)
            raise ApiError(e.code, str(detail)[:200])
        except (urllib.error.URLError, OSError) as e:
            raise ApiError("network", e.__class__.__name__)


# ───────────────────────────────────────── 이력 → 맥락
def _series(rows):
    out = {}
    for r in rows or []:
        try:
            t = datetime.fromisoformat(str(r["time"]).replace("Z", "+00:00"))
            v = r["value"]
        except (KeyError, ValueError):
            continue
        if isinstance(v, bool):
            v = 1.0 if v else 0.0
        if isinstance(v, (int, float)):
            out.setdefault(r["field"], []).append((t, float(v)))
    for k in out:
        out[k].sort()
    return out


def _trend(series, now, minutes=30):
    if not series:
        return None
    cutoff = now - timedelta(minutes=minutes)
    old = next((v for t, v in series if t >= cutoff), None)
    return None if old is None else round(series[-1][1] - old, 2)


def _flat(series, now, minutes=30):
    xs = [v for t, v in series if t >= now - timedelta(minutes=minutes)]
    return len(xs) >= 10 and (max(xs) - min(xs)) < 0.05


def _pulses_today(pump_series, now):
    """오늘(KST) 펌프 0→양수 전환 시각들."""
    day = now.astimezone(KST).date()
    edges, prev = [], 0.0
    for t, v in pump_series:
        if t.astimezone(KST).date() != day:
            prev = v
            continue
        if v > 0 and prev <= 0:
            edges.append(t)
        prev = v
    return edges


def _value_before(series, t):
    vals = [v for tt, v in series if tt <= t]
    return vals[-1] if vals else None


def build_snapshot(now, values_resp, status_resp, auto_resp, history_rows, today_pump_rows=None):
    """서버 응답 → Snapshot. now 는 UTC aware datetime. 어떤 응답이 없어도(None) 만들 수 있는 만큼 만든다."""
    v = (values_resp or {}).get("values") or {}
    seen = (values_resp or {}).get("seen")
    num = lambda k: float(v[k]) if isinstance(v.get(k), (int, float)) and not isinstance(v.get(k), bool) else None
    st = (status_resp or {}).get("status") or {}
    cal_ok = soil_calibrated_from_status(st.get("air"), st.get("water")) if st else None
    hist = _series(history_rows)
    state = (auto_resp or {}).get("state") or {}
    local = now.astimezone(KST)

    count, last_pulse = 0, None
    if state.get("pump_last_at"):
        lp = datetime.fromtimestamp(float(state["pump_last_at"]), UTC)
        if lp.astimezone(KST).date() == local.date():
            count, last_pulse = int(state.get("pump_count") or 0), lp
    elif today_pump_rows is not None:
        edges = _pulses_today(_series(today_pump_rows).get("pump", []), now)
        count, last_pulse = len(edges), (edges[-1] if edges else None)
    since = None if last_pulse is None else (now - last_pulse).total_seconds() / 60.0
    change = None
    moist = hist.get("moisture_pct", [])
    if last_pulse is not None and since is not None and since <= 30 and moist:
        before = _value_before(moist, last_pulse)
        cur = num("moisture_pct")
        if before is not None and cur is not None:
            change = round(cur - before, 2)

    led, fan = num("led1"), num("fan1")
    return Snapshot(
        local_time=local.replace(tzinfo=None, second=0, microsecond=0),
        temperature=num("temperature"), humidity=num("humidity"), lux=num("lux"), soil_moisture=num("moisture_pct"),
        soil_calibrated=cal_ok, soil_sensor_ok=v.get("ads1115_ok") is not False,
        led_pct=None if led is None else int(round(led)), fan_pct=None if fan is None else int(round(fan)),
        pump_on=v.get("pump") is True, irrigations_today=count, minutes_since_irrigation=since,
        moisture_change_since_irrigation=change,
        temp_trend_30m=_trend(hist.get("temperature"), now), humidity_trend_30m=_trend(hist.get("humidity"), now),
        temp_flat_30m=_flat(hist.get("temperature", []), now),
        max_data_age_s=None if not isinstance(seen, (int, float)) else max(0.0, now.timestamp() - float(seen)),
    )


# ───────────────────────────────────────── 묶음 정보 (젯슨 /models/current)
def bundle_info(models_dir, questions):
    out = {"revision": None, "temperatures": {}, "mismatch": []}
    if not models_dir:
        return out
    cur = Path(models_dir) / "current"
    try:
        m = json.loads((cur / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        out["mismatch"].append("manifest 없음")
        return out
    out["revision"] = m.get("revision")
    if m.get("questions_sha256") != questions_sha256(questions):
        out["mismatch"].append("questions")
    if m.get("feature_version") != FEATURE_VERSION:
        out["mismatch"].append("feature_version")
    try:
        c = yaml.safe_load((cur / "calibration.yaml").read_text(encoding="utf-8")) or {}
        out["temperatures"] = {k: float(v) for k, v in (c.get("temperatures") or {}).items()}
    except (OSError, ValueError, TypeError):
        out["mismatch"].append("calibration 없음")
    return out


# ───────────────────────────────────────── 수집기
class Collector:
    def __init__(self, cfg, profile, questions, api, model=None, log=print, clock=lambda: datetime.now(UTC)):
        self.cfg = cfg
        self.profile = profile
        self.questions = questions
        self.api = api
        self.model = model
        self.log = log
        self.clock = clock
        self.log_dir = Path(cfg.get("log_dir", "data/shadow"))
        self.models_dir = (cfg.get("laya") or {}).get("models_dir")
        self.window = int(cfg.get("history_window_min", 35))

    def _get(self, sn, leaf, errors, params=None):
        try:
            return self.api.get(sn, leaf, params)
        except ApiError as e:
            errors[leaf] = "%s %s" % (e.status, e.detail)
        except AuthError as e:
            errors[leaf] = "auth: %s" % e
        return None

    def observe(self, sn):
        """큐브 하나 한 번. 기록 한 줄(dict)을 돌려준다. 예외를 밖으로 내지 않는다."""
        now = self.clock()
        errors = {}
        values = self._get(sn, "values", errors)
        status = self._get(sn, "status", errors)
        auto = self._get(sn, "auto", errors)
        fields = ",".join(HISTORY_FIELDS)
        hist = self._get(sn, "history", errors, {"start": "-%dm" % self.window, "field": fields, "every": "1m"})
        today = None
        if not ((auto or {}).get("state") or {}).get("pump_last_at"):
            mins = int((now.astimezone(KST) - now.astimezone(KST).replace(hour=0, minute=0, second=0,
                                                                          microsecond=0)).total_seconds() // 60) + 1
            r = self._get(sn, "history", errors, {"start": "-%dm" % max(mins, 1), "field": "pump", "every": "1m"})
            today = (r or {}).get("rows")
        rec = {"t": now.isoformat(timespec="seconds"), "sn": sn, "iid": self.api.iid,
               "feature_version": FEATURE_VERSION, "questions_sha256": questions_sha256(self.questions)}
        if values is None:
            rec["skipped"] = "values 없음"
            rec["errors"] = errors
            return rec
        snap = build_snapshot(now, values, status, auto, (hist or {}).get("rows"), today)
        state = build_state(snap, self.profile)
        rec["snapshot"] = snap.to_dict()
        rec["state"] = state
        rec["baseline"] = {q: labeler.HARD[q][1](snap, self.profile) for q in self.questions if q in labeler.HARD}
        st = (auto or {}).get("state") or {}
        rec["autoctl"] = {"enabled": (auto or {}).get("enabled"), "crop_id": (auto or {}).get("crop_id"),
                          "phase": st.get("phase"), "rules": st.get("rules"), "alerts": st.get("alerts"),
                          "paused": st.get("paused"), "last_eval": st.get("last_eval"),
                          "last_action": st.get("last_action"), "pump_count": st.get("pump_count")}
        rec["model"] = self._ask_model(state)
        if errors:
            rec["errors"] = errors
        return rec

    def _ask_model(self, state):
        if self.model is None:
            return {"skipped": "laya 꺼짐"}
        info = bundle_info(self.models_dir, self.questions)
        t0 = time.time()
        try:
            resp = self.model.ask(state, self.questions)
            raw = normalize_response(self.questions, resp)
        except Exception as e:                           # 모델 장애는 기록만 하고 계속
            return {"error": "%s: %s" % (e.__class__.__name__, str(e)[:160]), "revision": info["revision"]}
        calp = {q: cal.apply_temperature(p, info["temperatures"].get(q, 1.0)) for q, p in raw.items()}
        return {"revision": resp.get("revision", info["revision"]), "mismatch": info["mismatch"],
                "latency_ms": round((time.time() - t0) * 1000.0, 1), "raw": raw, "calibrated": calp,
                "top": {q: labeler.argmax(p) for q, p in calp.items()},
                "confidence": {q: round(max(p.values()), 4) for q, p in calp.items()}}

    def write(self, rec):
        self.log_dir.mkdir(parents=True, exist_ok=True)
        day = datetime.fromisoformat(rec["t"]).astimezone(KST).strftime("%Y-%m-%d")
        with open(self.log_dir / ("shadow-%s.jsonl" % day), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def tick(self):
        recs = []
        for sn in self.cfg.get("cubes", []):
            rec = self.observe(sn)
            self.write(rec)
            recs.append(rec)
            m = rec.get("model", {})
            self.log("[shadow] %s %s %s%s" % (
                sn, "skip: " + rec["skipped"] if "skipped" in rec else
                " ".join("%s=%s" % (q, a) for q, a in rec["baseline"].items()),
                ("| model %s" % (m.get("error") or m.get("skipped") or
                                 " ".join("%s=%s" % kv for kv in m.get("top", {}).items()))) if "baseline" in rec else "",
                (" | errors %s" % ",".join(rec["errors"])) if rec.get("errors") else ""))
        return recs

    def run(self):
        every = float(self.cfg.get("every_s", 60))
        stop = {"flag": False}
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, lambda *_: stop.update(flag=True))
            except (ValueError, OSError):
                pass
        self.log("[shadow] 시작 — 큐브 %s, %d s 마다, 기록 %s (명령은 보내지 않는다)"
                 % (",".join(self.cfg.get("cubes", [])), every, self.log_dir))
        while not stop["flag"]:
            t0 = time.time()
            try:
                self.tick()
            except Exception as e:                        # 수집기는 죽지 않는다
                self.log("[shadow] tick 오류: %r" % e)
            while not stop["flag"] and time.time() - t0 < every:
                time.sleep(0.5)
        self.log("[shadow] 종료")


# ───────────────────────────────────────── 설정·조립
def load_config(path, env=os.environ):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    api = cfg.setdefault("api", {})
    api["base_url"] = env.get("COLLECTOR_API_BASE", api.get("base_url", ""))
    api["instance"] = env.get("COLLECTOR_INSTANCE", api.get("instance") or "")
    if env.get("COLLECTOR_CUBES"):
        cfg["cubes"] = [s.strip() for s in env["COLLECTOR_CUBES"].split(",") if s.strip()]
    if env.get("COLLECTOR_LAYA") in ("0", "1"):
        cfg.setdefault("laya", {})["enabled"] = env["COLLECTOR_LAYA"] == "1"
    return cfg


def make(cfg, profile, questions_path=None, env=os.environ, log=print):
    questions = load_questions(questions_path)
    tokens = TokenProvider(cfg.get("auth"), env=env)
    api = ApiClient(cfg["api"]["base_url"], cfg["api"]["instance"], tokens, float(cfg["api"].get("timeout_s", 10)))
    lc = cfg.get("laya") or {}
    model = None
    if lc.get("enabled", True):
        model = HttpPredictor(lc.get("url", "http://laya-edge:8000"), api_key=env.get(lc.get("key_env", "LAYA_API_KEY")),
                              timeout=float(lc.get("timeout_s", 8)))
    return Collector(cfg, profile, questions, api, model, log=log)


# ───────────────────────────────────────── 보고서
def report(paths):
    """shadow 기록 → 질문별 모델·기준 일치율, 확신, 오류·지연."""
    from collections import Counter, defaultdict
    n = skipped = 0
    agree = defaultdict(lambda: [0, 0])
    conf = defaultdict(list)
    errs = Counter()
    lat = []
    auto_on = 0
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                n += 1
                if "skipped" in r:
                    skipped += 1
                for k in (r.get("errors") or {}):
                    errs[k] += 1
                if (r.get("autoctl") or {}).get("enabled"):
                    auto_on += 1
                m = r.get("model") or {}
                if m.get("error"):
                    errs["model"] += 1
                if "top" not in m:
                    continue
                lat.append(m.get("latency_ms", 0))
                for q, a in m["top"].items():
                    b = (r.get("baseline") or {}).get(q)
                    if b is None:
                        continue
                    agree[q][1] += 1
                    agree[q][0] += int(a == b)
                    conf[q].append(m["confidence"][q])
    lat.sort()
    return {
        "records": n, "skipped": skipped, "autoctl_enabled": auto_on, "errors": dict(errs),
        "latency_ms": {"p50": lat[len(lat) // 2] if lat else None,
                       "p95": lat[max(0, int(len(lat) * 0.95) - 1)] if lat else None},
        "questions": {q: {"n": t, "agree_with_baseline": round(a / t, 4) if t else None,
                          "mean_confidence": round(sum(conf[q]) / len(conf[q]), 4) if conf[q] else None}
                      for q, (a, t) in sorted(agree.items())},
    }
