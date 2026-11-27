# -*- coding: utf-8 -*-
"""
shadow 수집기 시험 — 가짜 서버(api·Supabase 토큰·laya-edge)를 한 프로세스에 띄워 끝까지 돌린다.
확인: 읽기만 한다(GET 외 요청 0) · 토큰 갱신·회전 저장 · 401 한 번 재시도 · Snapshot·기준 판단·모델 기록 · 보고서 · 데이터셋 연결.
"""
import base64
import json
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from helpai_decide import collector as co
from helpai_decide import dataset
from helpai_decide.profile import load_profile
from helpai_decide.questions import load_questions

P = load_profile()
Q = load_questions()
KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 27, 5, 10, tzinfo=timezone.utc)          # KST 14:10 (주간)


def fake_jwt(exp):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return "%s.%s.sig" % (enc({"alg": "HS256"}), enc({"exp": exp, "sub": "x"}))


class Fake(BaseHTTPRequestHandler):
    calls = []            # (method, path, auth)
    fail_401_once = False
    refresh_seen = []

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        Fake.calls.append(("GET", u.path, self.headers.get("Authorization")))
        if Fake.fail_401_once:
            Fake.fail_401_once = False
            return self._send(401, {"detail": "토큰 만료"})
        if not (self.headers.get("Authorization") or "").startswith("Bearer "):
            return self._send(401, {"detail": "로그인이 필요합니다"})
        leaf = u.path.rsplit("/", 1)[-1]
        q = parse_qs(u.query)
        now = NOW.timestamp()
        if leaf == "values":
            return self._send(200, {"sn": "CUBE001", "seen": now - 4, "session": 1, "values": {
                "temperature": 31.2, "humidity": 71.0, "lux": 4800, "moisture_pct": 28.0, "fan1": 33, "led1": 100,
                "pump": False, "ads1115_ok": True}})
        if leaf == "status":
            return self._send(200, {"sn": "CUBE001", "status": {"air": 21000, "water": 11100}})
        if leaf == "auto":
            return self._send(200, {"sn": "CUBE001", "enabled": True, "crop_id": "rose", "state": {
                "phase": "day", "rules": ["배기팬: 온도 높음"], "alerts": [], "paused": None, "last_eval": now - 5,
                "last_action": {"t": now - 60, "sent": {"fan1": 33}, "result": "ok"},
                "pump_count": 2, "pump_last_at": now - 75 * 60}})
        if leaf == "history":
            rows = []
            for m in range(35, -1, -1):
                t = (NOW - timedelta(minutes=m)).isoformat()
                rows.append({"time": t, "field": "temperature", "value": 31.2 - 0.05 * m})
                rows.append({"time": t, "field": "humidity", "value": 71.0})
                rows.append({"time": t, "field": "moisture_pct", "value": 28.0})
            return self._send(200, {"rows": rows, "start": q.get("start", [""])[0]})
        return self._send(404, {"detail": "없음"})

    def do_POST(self):
        u = urlparse(self.path)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if u.path == "/auth/v1/token":
            Fake.refresh_seen.append(body.get("refresh_token"))
            n = len(Fake.refresh_seen)
            return self._send(200, {"access_token": fake_jwt(time.time() + 3600), "refresh_token": "rt-%d" % (n + 1),
                                    "expires_in": 3600})
        if u.path == "/v1/systemone":
            ans = {}
            for name, qq in body["questions"].items():
                if qq["type"] == "noul":
                    ans[name] = {"type": "noul", "noul": 0.95}
                else:
                    opts = list(qq["criteria"])
                    pick = {"fan_action": "up", "light_action": "hold", "plant_stress": "moderate"}.get(name, opts[0])
                    ans[name] = {"type": "choice", "probabilities": {o: (0.85 if o == pick else 0.05) for o in opts}}
            return self._send(200, {"model": "fake", "revision": "r-test", "answers": ans})
        Fake.calls.append(("POST", u.path, self.headers.get("Authorization")))
        return self._send(405, {"detail": "no"})

    def log_message(self, *a):
        pass


class CollectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Fake)
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_port

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        Fake.calls.clear()
        Fake.refresh_seen.clear()
        Fake.fail_401_once = False
        self.env = {"SUPABASE_URL": self.base, "SUPABASE_ANON_KEY": "anon", "SUPABASE_REFRESH_TOKEN": "rt-1",
                    "LAYA_API_KEY": "k"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _collector(self, laya=True):
        cfg = {"api": {"base_url": self.base + "/api", "instance": "inst-1", "timeout_s": 5},
               "auth": {"mode": "supabase_refresh", "refresh_token_file": str(self.tmp / "state" / "rt")},
               "cubes": ["CUBE001"], "log_dir": str(self.tmp / "logs"),
               "laya": {"enabled": laya, "url": self.base, "models_dir": str(self.tmp / "models")}}
        c = co.make(cfg, P, env=self.env, log=lambda *_: None)
        c.clock = lambda: NOW
        return c

    def test_end_to_end_read_only(self):
        c = self._collector()
        rec = c.tick()[0]
        # 읽기만 했다
        self.assertTrue(Fake.calls)
        self.assertTrue(all(m == "GET" for m, _, _ in Fake.calls), Fake.calls)
        self.assertTrue(all(p.startswith("/api/i/inst-1/cubes/CUBE001/") for _, p, _ in Fake.calls))
        # 토큰 회전: rt-1 로 갱신 → 새 refresh token 이 파일에 저장
        self.assertEqual(Fake.refresh_seen, ["rt-1"])
        self.assertEqual((self.tmp / "state" / "rt").read_text(), "rt-2")
        # Snapshot·state·기준 판단
        s = rec["snapshot"]
        self.assertEqual(s["irrigations_today"], 2)
        self.assertAlmostEqual(s["minutes_since_irrigation"], 75.0, places=3)
        self.assertIs(s["soil_calibrated"], True)
        self.assertAlmostEqual(s["max_data_age_s"], 4.0)
        self.assertAlmostEqual(s["temp_trend_30m"], 1.5, places=2)
        self.assertEqual(rec["baseline"], {"fan_action": "up", "light_action": "hold", "irrigate_now": "true",
                                           "plant_stress": "moderate", "data_suspect": "false"})
        self.assertIn("above max 30.0", rec["state"]["temperature"])
        self.assertTrue(rec["autoctl"]["enabled"])
        self.assertEqual(rec["autoctl"]["rules"], ["배기팬: 온도 높음"])
        # 모델 답 (묶음이 없으니 mismatch 에 manifest 없음)
        m = rec["model"]
        self.assertEqual(m["revision"], "r-test")
        self.assertEqual(m["top"]["fan_action"], "up")
        self.assertIn("manifest 없음", m["mismatch"])
        # 파일 기록 (KST 날짜)
        f = self.tmp / "logs" / "shadow-2026-11-27.jsonl"
        self.assertTrue(f.exists())
        self.assertEqual(json.loads(f.read_text(encoding="utf-8").splitlines()[0])["sn"], "CUBE001")

    def test_second_tick_reuses_token_and_401_retries_once(self):
        c = self._collector(laya=False)
        c.tick()
        self.assertEqual(len(Fake.refresh_seen), 1)
        c.tick()
        self.assertEqual(len(Fake.refresh_seen), 1)          # 아직 유효 → 갱신 안 함
        Fake.fail_401_once = True
        rec = c.tick()[0]
        self.assertEqual(len(Fake.refresh_seen), 2)          # 401 → 한 번 갱신 후 재시도
        self.assertEqual(Fake.refresh_seen[-1], "rt-2")      # 저장된 새 토큰으로
        self.assertNotIn("values", rec.get("errors", {}))
        self.assertEqual(rec["model"], {"skipped": "laya 꺼짐"})

    def test_bundle_calibration_and_mismatch(self):
        cur = self.tmp / "models" / "current"
        cur.mkdir(parents=True)
        from helpai_decide.features import FEATURE_VERSION
        from helpai_decide.questions import questions_sha256
        (cur / "manifest.json").write_text(json.dumps({"revision": "r9", "questions_sha256": questions_sha256(Q),
                                                       "feature_version": FEATURE_VERSION}))
        (cur / "calibration.yaml").write_text("temperatures: {fan_action: 3.0}\n")
        rec = self._collector().tick()[0]
        self.assertEqual(rec["model"]["mismatch"], [])
        self.assertLess(rec["model"]["calibrated"]["fan_action"]["up"], rec["model"]["raw"]["fan_action"]["up"])

    def test_no_values_is_recorded_not_raised(self):
        c = self._collector(laya=False)
        c.api.base = "http://127.0.0.1:1/api"                 # 닿지 않는 주소
        rec = c.tick()[0]
        self.assertEqual(rec["skipped"], "values 없음")
        self.assertIn("values", rec["errors"])

    def test_api_client_refuses_control(self):
        api = co.ApiClient(self.base + "/api", "inst-1", co.TokenProvider({"mode": "none"}))
        for leaf in ("control", "auto/../control", "config", "refresh"):
            with self.assertRaises(ValueError):
                api.url("CUBE001", leaf)
        self.assertFalse(hasattr(api, "post"))

    def test_report_and_dataset(self):
        c = self._collector()
        for _ in range(3):
            c.tick()
        logs = str(self.tmp / "logs" / "shadow-*.jsonl")
        import glob
        rep = co.report(glob.glob(logs))
        self.assertEqual(rep["records"], 3)
        self.assertEqual(rep["autoctl_enabled"], 3)
        self.assertEqual(rep["questions"]["fan_action"]["agree_with_baseline"], 1.0)
        self.assertEqual(rep["questions"]["irrigate_now"]["agree_with_baseline"], 1.0)   # 0.95 true = 기준 true
        info = dataset.build({"shadow": {"logs": [logs], "subsample": 1}}, P, Q, self.tmp / "ds", log=lambda *_: None)
        self.assertEqual(info["stats"]["rows_shadow"], 1)                 # 같은 state 3 개 → 중복 제거
        self.assertEqual(info["stats"]["dup_skipped"], 2)

    def test_static_and_missing_tokens(self):
        t = co.TokenProvider({"mode": "static"}, env={"HELPAI_TOKEN": "abc"})
        self.assertEqual(t.header(), "Bearer abc")
        with self.assertRaises(co.AuthError):
            co.TokenProvider({"mode": "static"}, env={}).header()
        with self.assertRaises(co.AuthError):
            co.TokenProvider({"mode": "supabase_refresh"}, env={"SUPABASE_URL": self.base}).header()


if __name__ == "__main__":
    unittest.main()
