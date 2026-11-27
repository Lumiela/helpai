# -*- coding: utf-8 -*-
"""데이터셋·재생·예측 정규화·묶음 시험 (모델 없이)."""
import csv
import json
import shutil
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from helpai_decide import bundle, dataset, labeler
from helpai_decide import predict as pr
from helpai_decide.profile import load_profile
from helpai_decide.questions import DEFAULT_QUESTIONS, load_questions
from helpai_decide.replay import read_wide_csv, replay

P = load_profile()
Q = load_questions()
ZONE = {"sensors": {"temperature": "C1.temperature", "humidity": "C1.humidity", "lux": "C1.lux",
                    "soil_moisture": {"from": "C1.moisture_pct", "valid_if": "C1.moisture_calibrated"}},
        "actuators": {"light": ["C2.led1"], "pump": "C1.pump", "fan": ["C1.fan1"]}}


class TmpDir(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def write_csv(path, minutes=90, pulse_at=40, rise=0.2, flat=False):
    cols = ["C1.temperature", "C1.humidity", "C1.lux", "C1.moisture_pct", "C1.moisture_calibrated", "C1.pump",
            "C1.fan1", "C2.led1"]
    t0 = datetime(2026, 11, 20, 4, 0, tzinfo=timezone.utc)      # KST 13:00
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time"] + cols)
        for m in range(minutes):
            T = 25.0 if flat else 25.0 + 0.02 * m
            M = 38.0 + (rise if m > pulse_at else 0.0)
            w.writerow([(t0 + timedelta(minutes=m)).isoformat(), T, 50.0, 7000, M, 1, 1 if m == pulse_at else 0,
                        20, 60])


class ReplayTest(TmpDir):
    def test_pulse_count_and_no_rise(self):
        p = self.tmp / "a.csv"
        write_csv(p, rise=0.2)
        snaps = [s for s, _ in replay(read_wide_csv(p), ZONE)]
        self.assertEqual(len(snaps), 90 - 30)
        after = [s for s in snaps if s.minutes_since_irrigation is not None and 6 <= s.minutes_since_irrigation <= 30]
        self.assertTrue(after)
        self.assertEqual(after[0].irrigations_today, 1)
        self.assertIn("no_rise_after_watering", labeler.suspect_reasons(after[0], P))
        self.assertEqual(after[0].local_time.hour, 13 + (40 + 6) // 60)

    def test_rise_ok_and_flat(self):
        p = self.tmp / "b.csv"
        write_csv(p, rise=5.0, flat=True)
        snaps = [s for s, _ in replay(read_wide_csv(p), ZONE)]
        after = [s for s in snaps if s.minutes_since_irrigation is not None and 6 <= s.minutes_since_irrigation <= 30]
        self.assertNotIn("no_rise_after_watering", labeler.suspect_reasons(after[0], P))
        self.assertTrue(snaps[-1].temp_flat_30m)


class DatasetTest(TmpDir):
    def test_build_and_check(self):
        csvp = self.tmp / "r.csv"
        write_csv(csvp, minutes=60 * 24 * 3)                  # 3 일 → 날짜 나누기
        cfg = {"val_frac": 0.1, "test_frac": 0.1, "synth": {"n": 400, "seed": 5},
               "replay": {"enabled": True, "zone": ZONE, "csv": [str(csvp)], "subsample_min": 30}}
        info = dataset.build(cfg, P, Q, self.tmp / "ds", log=lambda *_: None)
        for sp in ("train", "val", "test"):
            rows = list(dataset.read_rows(self.tmp / "ds" / ("%s.jsonl" % sp)))
            meta = list(dataset.read_rows(self.tmp / "ds" / ("%s.meta.jsonl" % sp)))
            self.assertEqual(len(rows), len(meta))
            self.assertTrue(rows, sp)
            self.assertEqual(set(rows[0]), {"state", "questions", "gold"})
            self.assertEqual(dataset.check_rows(self.tmp / "ds" / ("%s.jsonl" % sp), Q), [])
        test_dates = {m["local_date"] for m in dataset.read_rows(self.tmp / "ds" / "test.meta.jsonl")
                      if m["source"] == "replay"}
        train_dates = {m["local_date"] for m in dataset.read_rows(self.tmp / "ds" / "train.meta.jsonl")
                       if m["source"] == "replay"}
        self.assertFalse(test_dates & train_dates)            # 같은 날이 섞이지 않는다
        self.assertGreater(info["stats"]["rows_replay"], 0)


class FakeLaya(BaseHTTPRequestHandler):
    """질문마다 교사 답을 살짝 흐려서 돌려주는 가짜 /v1/systemone."""
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        ans = {}
        for name, q in body["questions"].items():
            if q["type"] == "noul":
                ans[name] = {"type": "noul", "noul": 0.8}
            else:
                opts = list(q["criteria"])
                ans[name] = {"type": "choice", "choice": opts[0], "confidence": 0.5,
                             "probabilities": {o: (0.7 if i == 0 else 0.3 / (len(opts) - 1)) for i, o in enumerate(opts)}}
        out = json.dumps({"model": "fake", "answers": ans}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class PredictTest(TmpDir):
    def test_normalize(self):
        self.assertEqual(pr.normalize_answer(Q["irrigate_now"], {"noul": 0.25}), {"false": 0.75, "true": 0.25})
        d = pr.normalize_answer(Q["fan_level"], {"probabilities": {"off": 2, "low": 2}})
        self.assertEqual(d, {"off": 0.5, "low": 0.5, "mid": 0.0, "high": 0.0})

    def test_http_run_and_compare(self):
        cfg = {"synth": {"n": 30, "seed": 9}}
        dataset.build(cfg, P, Q, self.tmp / "ds", log=lambda *_: None)
        srv = HTTPServer(("127.0.0.1", 0), FakeLaya)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            url = "http://127.0.0.1:%d" % srv.server_port
            n, fails = pr.run(pr.HttpPredictor(url), self.tmp / "ds" / "train.jsonl", self.tmp / "a.jsonl",
                              log=lambda *_: None)
            self.assertGreater(n, 0)
            self.assertEqual(fails, 0)
            res = pr.compare(self.tmp / "a.jsonl", self.tmp / "a.jsonl")
            self.assertEqual(res["questions"]["fan_level"]["top1_agreement"], 1.0)
        finally:
            srv.shutdown()
            srv.server_close()


class BundleTest(TmpDir):
    def _onnx_dir(self):
        d = self.tmp / "onnx"
        (d / "tokenizer").mkdir(parents=True)
        (d / "laya.onnx").write_bytes(b"\x08\x01fake")
        (d / "laya_config.json").write_text('{"max_len": 1024}')
        (d / "tokenizer" / "tokenizer.json").write_text("{}")
        (self.tmp / "cal.yaml").write_text("version: 1\ntemperatures: {fan_level: 1.3}\n")
        (self.tmp / "rep.json").write_text(json.dumps({"questions": {"fan_level": {"n": 5, "accuracy": 0.9}}}))
        return d

    def test_make_verify_tamper(self):
        d = self._onnx_dir()
        out = self.tmp / "bundles" / "r1"
        m, n = bundle.make(d, self.tmp / "cal.yaml", DEFAULT_QUESTIONS, self.tmp / "rep.json", out, "r1", "int8",
                           "test")
        self.assertEqual(bundle.verify(out, DEFAULT_QUESTIONS), [])
        self.assertEqual(m["eval"]["fan_level"]["accuracy"], 0.9)
        (out / "laya.onnx").write_bytes(b"tampered")
        self.assertTrue(any("해시 불일치" in p for p in bundle.verify(out)))

    def test_questions_changed(self):
        d = self._onnx_dir()
        out = self.tmp / "b2"
        bundle.make(d, self.tmp / "cal.yaml", DEFAULT_QUESTIONS, self.tmp / "rep.json", out, "r2", "int8", "test")
        other = self.tmp / "q.yaml"
        txt = Path(DEFAULT_QUESTIONS).read_text(encoding="utf-8").replace("Rate the current stress", "Rate stress")
        other.write_text(txt, encoding="utf-8")
        self.assertTrue(any("shadow" in p for p in bundle.verify(out, other)))


if __name__ == "__main__":
    unittest.main()
