# -*- coding: utf-8 -*-
"""
자정을 넘는 재급수 대기 — autoctl 은 `now - pump_last_at < 60 분` 을 날짜와 상관없이 본다(backend autoctl.py).
하루 횟수는 KST 자정에 0 이 되지만, 어제 23:50 급수 뒤 00:10 에는 아직 대기 중이어야 한다.
"""
import csv
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from helpai_decide import collector as co
from helpai_decide import labeler
from helpai_decide.features import PUMP_WAIT_MIN, Snapshot, build_state, minutes_since_pulse
from helpai_decide.profile import load_profile
from helpai_decide.replay import read_wide_csv, replay

P = load_profile()
UTC = timezone.utc
KST = timezone(timedelta(hours=9))
ZONE = {"sensors": {"temperature": "C1.temperature", "humidity": "C1.humidity", "lux": "C1.lux",
                    "soil_moisture": "C1.moisture_pct"},
        "actuators": {"light": "C1.led1", "pump": "C1.pump", "fan": "C1.fan1"},
        "soil_calibration": {"air": "C1.air", "water": "C1.water"}}


class HelperTest(unittest.TestCase):
    def test_minutes_since_pulse(self):
        now = datetime(2026, 11, 28, 0, 10, tzinfo=KST)
        self.assertIsNone(minutes_since_pulse(now, None, 0))
        self.assertAlmostEqual(minutes_since_pulse(now, now - timedelta(minutes=20), 0), 20.0)   # 어제, 대기 중
        self.assertIsNone(minutes_since_pulse(now, now - timedelta(minutes=PUMP_WAIT_MIN + 1), 0))  # 어제, 대기 끝
        self.assertAlmostEqual(minutes_since_pulse(now, now - timedelta(minutes=5), 1), 5.0)      # 오늘

    def test_state_says_before_midnight(self):
        s = Snapshot(local_time=datetime(2026, 11, 28, 0, 10), temperature=25.0, humidity=65.0, lux=0,
                     soil_moisture=28.0, irrigations_today=0, minutes_since_irrigation=20.0)
        st = build_state(s, P)
        self.assertIn("before midnight", st["irrigation"])
        self.assertIn("wait NOT passed", st["irrigation"])
        self.assertEqual(labeler.irrigate_of(s, P), "false")


class ReplayMidnightTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_wait_carries_over_midnight(self):
        p = self.tmp / "m.csv"
        cols = ["C1.temperature", "C1.humidity", "C1.lux", "C1.moisture_pct", "C1.pump", "C1.fan1", "C1.led1",
                "C1.air", "C1.water"]
        t0 = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)          # KST 23:00
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["time"] + cols)
            for m in range(130):
                st = (21000, 11100) if m % 10 == 0 else ("", "")
                w.writerow([(t0 + timedelta(minutes=m)).isoformat(), 25.0, 65.0, 0, 28.0,
                            1 if m == 50 else 0, 33, 0, *st])          # 급수 KST 23:50
        snaps = {(s.local_time.hour, s.local_time.minute): s for s, _ in replay(read_wide_csv(p), ZONE)}
        s = snaps[(0, 10)]
        self.assertEqual(s.irrigations_today, 0)                       # 하루 횟수는 자정에 0
        self.assertAlmostEqual(s.minutes_since_irrigation, 20.0)       # 대기는 이어진다
        self.assertEqual(labeler.irrigate_of(s, P), "false")
        s = snaps[(0, 51)]                                             # 61 분 뒤
        self.assertIsNone(s.minutes_since_irrigation)
        self.assertEqual(labeler.irrigate_of(s, P), "true")


class CollectorMidnightTest(unittest.TestCase):
    NOW = datetime(2026, 11, 27, 15, 10, tzinfo=UTC)                 # KST 11-28 00:10
    VALUES = {"sn": "CUBE001", "seen": NOW.timestamp() - 5,
              "values": {"temperature": 25.0, "humidity": 65.0, "lux": 0, "moisture_pct": 28.0,
                         "fan1": 33, "led1": 0, "pump": False, "ads1115_ok": True}}
    STATUS = {"status": {"air": 21000, "water": 11100}}

    def test_from_autoctl_state(self):
        last = (self.NOW - timedelta(minutes=20)).timestamp()          # KST 23:50 어제
        auto = {"enabled": True, "state": {"pump_last_at": last, "pump_count": 3}}
        s = co.build_snapshot(self.NOW, self.VALUES, self.STATUS, auto, [])
        self.assertEqual(s.irrigations_today, 0)
        self.assertAlmostEqual(s.minutes_since_irrigation, 20.0, places=1)
        self.assertEqual(labeler.irrigate_of(s, P), "false")

    def test_from_pump_history(self):
        rows = [{"time": (self.NOW - timedelta(minutes=m)).isoformat(), "field": "pump",
                 "value": 1.0 if m == 20 else 0.0} for m in range(60, 0, -1)]
        s = co.build_snapshot(self.NOW, self.VALUES, self.STATUS, {"enabled": True, "state": None}, [], rows)
        self.assertEqual(s.irrigations_today, 0)
        self.assertAlmostEqual(s.minutes_since_irrigation, 20.0, places=1)
        self.assertEqual(labeler.irrigate_of(s, P), "false")

    def test_pump_history_window_covers_wait(self):
        self.assertGreaterEqual(co.pump_history_minutes(self.NOW), PUMP_WAIT_MIN + 1)   # 00:10 → 61 분 이상
        noon = datetime(2026, 11, 27, 3, 0, tzinfo=UTC)                                # KST 12:00
        self.assertEqual(co.pump_history_minutes(noon), 12 * 60 + 1)


if __name__ == "__main__":
    unittest.main()
