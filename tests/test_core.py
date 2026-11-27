# -*- coding: utf-8 -*-
"""핵심 모듈 시험 (의존성: pyyaml 만). 실행: python -m unittest discover -s tests -t ."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from helpai_decide import calibration as cal
from helpai_decide import labeler, synth
from helpai_decide.features import (FAN_LEVELS, FEATURE_VERSION, Snapshot, build_state, fan_level_of, temp_band,
                                    warm_band_edges)
from helpai_decide.profile import is_light_period, load_profile
from helpai_decide.questions import load_questions, options_of, questions_sha256, validate_question

P = load_profile()
Q = load_questions()
DAY = datetime(2026, 6, 10, 14, 10)        # 6월 낮, 환기 창 밖(10분)
NIGHT = datetime(2026, 6, 10, 23, 30)
WINTER_NIGHT = datetime(2026, 1, 10, 23, 30)


def snap(**kw):
    base = dict(local_time=DAY, temperature=24.0, humidity=60.0, lux=8000, soil_moisture=60.0, soil_calibrated=True,
                led_pct=50, fan_pct=20, irrigations_today=0, minutes_since_irrigation=None,
                temp_trend_30m=0.0, humidity_trend_30m=0.0, max_data_age_s=5)
    base.update(kw)
    return Snapshot(**base)


def top(probs):
    return labeler.argmax(probs)


class ProfileTest(unittest.TestCase):
    def test_photoperiod(self):
        self.assertTrue(is_light_period(DAY, P))
        self.assertFalse(is_light_period(NIGHT, P))
        self.assertTrue(is_light_period(WINTER_NIGHT, P))     # 겨울 = 종일 허용


class FeatureTest(unittest.TestCase):
    def test_state_keys_fixed(self):
        st = build_state(snap(), P)
        self.assertEqual(list(st), ["crop", "time", "temperature", "humidity", "light", "soil", "irrigation", "fan",
                                    "data"])
        self.assertIsInstance(FEATURE_VERSION, int)

    def test_temperature_sentences(self):
        self.assertIn("critical: at or above 35.0 by 1.0", build_state(snap(temperature=36.0), P)["temperature"])
        self.assertIn("hot: above max 30.0 by 1.2", build_state(snap(temperature=31.2), P)["temperature"])
        self.assertIn("cold: below min 22.0 by 2.0", build_state(snap(temperature=20.0), P)["temperature"])
        self.assertIn("missing", build_state(snap(temperature=None), P)["temperature"])
        self.assertIn("not changed at all", build_state(snap(temp_flat_30m=True), P)["temperature"])

    def test_bands_match_fan_edges(self):
        e1, e2 = warm_band_edges(P)
        self.assertAlmostEqual(e1, 27.475, places=3)
        self.assertAlmostEqual(e2, 29.15, places=3)
        self.assertEqual(temp_band(27.4, P), "slightly_warm")
        self.assertEqual(temp_band(27.5, P), "warm")
        self.assertEqual(temp_band(29.2, P), "very_warm")

    def test_fan_level_of(self):
        self.assertEqual(fan_level_of(0), "off")
        self.assertEqual(fan_level_of(20), "low")
        self.assertEqual(fan_level_of(60), "mid")
        self.assertEqual(fan_level_of(100), "high")
        self.assertEqual(set(FAN_LEVELS), set(options_of(Q["fan_level"])))

    def test_irrigation_sentence(self):
        s = build_state(snap(irrigations_today=12, minutes_since_irrigation=10), P)["irrigation"]
        self.assertIn("daily limit reached", s)
        self.assertIn("NOT passed", s)
        s = build_state(snap(irrigations_today=1, minutes_since_irrigation=12, moisture_change_since_irrigation=0.2),
                        P)["irrigation"]
        self.assertIn("did NOT rise", s)
        s = build_state(snap(irrigations_today=1, minutes_since_irrigation=2, moisture_change_since_irrigation=0.2),
                        P)["irrigation"]
        self.assertNotIn("rise", s)          # 스며드는 시간 전에는 판단하지 않음

    def test_snapshot_roundtrip(self):
        s = snap(temperature=None)
        self.assertEqual(Snapshot.from_dict(s.to_dict()), s)


class LabelerTest(unittest.TestCase):
    def test_heat(self):
        g = labeler.label(snap(temperature=36.0, led_pct=80), P, Q)
        self.assertEqual(top(g["fan_level"]["probabilities"]), "high")
        self.assertEqual(top(g["light_action"]["probabilities"]), "decrease")
        self.assertEqual(top(g["plant_stress"]["probabilities"]), "severe")
        g = labeler.label(snap(temperature=36.0, led_pct=30), P, Q)
        self.assertEqual(top(g["light_action"]["probabilities"]), "hold")

    def test_cold(self):
        g = labeler.label(snap(temperature=18.5, local_time=NIGHT, led_pct=0, humidity=90), P, Q)
        self.assertEqual(top(g["fan_level"]["probabilities"]), "off")          # 다습해도 저온이 이긴다
        self.assertEqual(top(g["light_action"]["probabilities"]), "increase")  # 밤이라도 LED 보온
        self.assertEqual(top(g["plant_stress"]["probabilities"]), "severe")    # 22 - 3 미만

    def test_fan_proportional_and_floors(self):
        self.assertEqual(top(labeler.teacher_fan(snap(temperature=24.0), P)), "low")
        self.assertEqual(top(labeler.teacher_fan(snap(temperature=28.3), P)), "mid")
        self.assertEqual(top(labeler.teacher_fan(snap(temperature=29.8), P)), "high")
        self.assertEqual(top(labeler.teacher_fan(snap(temperature=24.0, humidity=90), P)), "mid")      # 습도 바닥 60
        self.assertEqual(top(labeler.teacher_fan(snap(temperature=24.0,
                                                      local_time=datetime(2026, 6, 10, 14, 2)), P)), "mid")  # 환기 창 50

    def test_fan_soft_near_edge(self):
        p = labeler.teacher_fan(snap(temperature=27.4), P)
        self.assertAlmostEqual(p["low"], 0.7)
        self.assertAlmostEqual(p["mid"], 0.3)
        self.assertAlmostEqual(sum(p.values()), 1.0)

    def test_light(self):
        self.assertEqual(top(labeler.teacher_light(snap(local_time=NIGHT, led_pct=60), P)), "off")
        self.assertEqual(top(labeler.teacher_light(snap(local_time=WINTER_NIGHT, lux=3000, led_pct=60), P)), "increase")
        self.assertEqual(top(labeler.teacher_light(snap(lux=3000, led_pct=100), P)), "hold")
        self.assertEqual(top(labeler.teacher_light(snap(lux=14000, led_pct=60), P)), "decrease")
        self.assertEqual(top(labeler.teacher_light(snap(lux=9000), P)), "hold")

    def test_irrigation_gates(self):
        dry = dict(soil_moisture=30.0)
        self.assertEqual(top(labeler.teacher_irrigate(snap(**dry), P)), "true")
        self.assertEqual(top(labeler.teacher_irrigate(snap(soil_calibrated=False, **dry), P)), "false")
        self.assertEqual(top(labeler.teacher_irrigate(snap(irrigations_today=12, **dry), P)), "false")
        self.assertEqual(top(labeler.teacher_irrigate(snap(irrigations_today=2, minutes_since_irrigation=10, **dry),
                                                      P)), "false")
        self.assertEqual(top(labeler.teacher_irrigate(snap(pump_on=True, **dry), P)), "false")
        self.assertEqual(top(labeler.teacher_irrigate(snap(soil_moisture=None), P)), "false")
        p = labeler.teacher_irrigate(snap(soil_moisture=39.5), P)
        self.assertAlmostEqual(p["true"], 0.7)

    def test_stress(self):
        self.assertEqual(top(labeler.teacher_stress(snap(), P)), "none")
        self.assertEqual(top(labeler.teacher_stress(snap(humidity=90), P)), "mild")
        self.assertEqual(top(labeler.teacher_stress(snap(lux=3000, led_pct=100), P)), "mild")
        self.assertEqual(top(labeler.teacher_stress(snap(temperature=31.0), P)), "moderate")
        self.assertEqual(top(labeler.teacher_stress(snap(soil_moisture=30.0, irrigations_today=12), P)), "moderate")
        self.assertEqual(top(labeler.teacher_stress(snap(temperature=33.5), P)), "severe")

    def test_suspect(self):
        self.assertEqual(labeler.suspect_reasons(snap(), P), [])
        self.assertIn("stale", labeler.suspect_reasons(snap(max_data_age_s=120), P))
        self.assertIn("stuck_temperature", labeler.suspect_reasons(snap(temp_flat_30m=True), P))
        self.assertIn("missing_reading", labeler.suspect_reasons(snap(humidity=None), P))
        self.assertIn("led_high_lux_low", labeler.suspect_reasons(snap(led_pct=100, lux=545), P))
        self.assertIn("no_rise_after_watering", labeler.suspect_reasons(
            snap(irrigations_today=1, minutes_since_irrigation=15, moisture_change_since_irrigation=0.3), P))
        self.assertEqual(labeler.suspect_reasons(
            snap(irrigations_today=1, minutes_since_irrigation=15, moisture_change_since_irrigation=4.0), P), [])

    def test_label_matches_questions(self):
        g = labeler.label(snap(), P, Q)
        self.assertEqual(set(g), set(Q))
        for q, v in g.items():
            self.assertEqual(sorted(v["probabilities"]), sorted(options_of(Q[q])))
            self.assertAlmostEqual(sum(v["probabilities"].values()), 1.0)


class QuestionsTest(unittest.TestCase):
    def test_hash_stable(self):
        h = questions_sha256(Q)
        self.assertEqual(h, questions_sha256(json.loads(json.dumps(Q))))
        q2 = json.loads(json.dumps(Q))
        q2["fan_level"]["instructions"] += " x"
        self.assertNotEqual(h, questions_sha256(q2))

    def test_validation(self):
        with self.assertRaises(ValueError):
            validate_question("a", {"type": "choice", "instructions": "x", "criteria": {"one": "1"}})
        with self.assertRaises(ValueError):
            validate_question("a", {"type": "noul", "instructions": "x", "criteria": {True: "y", False: "n"}})
        with self.assertRaises(ValueError):
            validate_question("a", {"type": "bool", "instructions": "x"})


class SynthTest(unittest.TestCase):
    def test_deterministic_and_valid(self):
        a = [s.to_dict() for s, _ in synth.generate(P, {"n": 300, "seed": 7})]
        b = [s.to_dict() for s, _ in synth.generate(P, {"n": 300, "seed": 7})]
        self.assertEqual(a, b)
        scen = {sc for _, sc in synth.generate(P, {"n": 2000, "seed": 7})}
        self.assertTrue(set(synth.ANOMALIES) <= scen)
        for s, _ in synth.generate(P, {"n": 500, "seed": 3}):
            labeler.label(s, P, Q)          # 예외 없이 라벨이 붙어야 한다


class CalibrationTest(unittest.TestCase):
    def test_identity(self):
        p = {"a": 0.7, "b": 0.3}
        self.assertEqual(cal.apply_temperature(p, 1.0), p)
        q = cal.apply_temperature(p, 2.0)
        self.assertLess(q["a"], 0.7)
        self.assertAlmostEqual(sum(q.values()), 1.0)

    def test_overconfident_gets_T_above_1(self):
        import random
        rng = random.Random(0)
        pairs = []
        for _ in range(2000):                 # 확신 0.99 인데 실제 정답률 75 %
            right = rng.random() < 0.75
            gold = {"a": 1.0, "b": 0.0} if right else {"a": 0.0, "b": 1.0}
            pairs.append(({"a": 0.99, "b": 0.01}, gold))
        T = cal.fit_temperature(pairs)
        self.assertGreater(T, 1.5)
        self.assertLess(cal.ece([(cal.apply_temperature(p, T), g) for p, g in pairs]), cal.ece(pairs))
        rep = cal.question_report(pairs, T)
        self.assertAlmostEqual(rep["accuracy"], 0.75, delta=0.03)


if __name__ == "__main__":
    unittest.main()
