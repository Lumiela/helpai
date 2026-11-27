# -*- coding: utf-8 -*-
"""
핵심 모듈 시험 (의존성: pyyaml 만). 실행: python -m unittest discover -s tests -t .
교사 라벨 기준 = 서버 api autoctl (docs/05). 장미 crops.json: 온도 22~30 · 습도 60~70 · 토양 30~60 · 조도 6000~12000 · 01:00~23:00.
"""
import json
import random
import unittest
from datetime import datetime

from helpai_decide import calibration as cal
from helpai_decide import labeler, synth
from helpai_decide.features import FEATURE_VERSION, Snapshot, build_state, nearest_step, soil_calibrated_from_status
from helpai_decide.profile import is_light_period, load_profile
from helpai_decide.questions import load_questions, options_of, questions_sha256, validate_question

P = load_profile()
Q = load_questions()
DAY = datetime(2026, 6, 10, 14, 10)
NIGHT = datetime(2026, 6, 10, 23, 30)


def snap(**kw):
    base = dict(local_time=DAY, temperature=24.0, humidity=66.0, lux=8000, soil_moisture=45.0, soil_calibrated=True,
                led_pct=50, fan_pct=33, irrigations_today=0, minutes_since_irrigation=None,
                temp_trend_30m=0.0, humidity_trend_30m=0.0, max_data_age_s=5)
    base.update(kw)
    return Snapshot(**base)


def top(probs):
    return labeler.argmax(probs)


def hard(name, **kw):
    return labeler.HARD[name][1](snap(**kw), P)


class ProfileTest(unittest.TestCase):
    def test_crops_json(self):
        self.assertEqual(P["temp_day"], {"min": 22.0, "max": 30.0})
        self.assertEqual(P["humidity_day"], {"min": 60.0, "max": 70.0})
        self.assertEqual(P["soil_moisture"], {"min": 30.0, "max": 60.0})
        self.assertEqual((P["photoperiod"]["on_min"], P["photoperiod"]["off_min"]), (60, 1380))

    def test_photoperiod(self):
        self.assertTrue(is_light_period(DAY, P))
        self.assertTrue(is_light_period(datetime(2026, 6, 10, 1, 0), P))
        self.assertFalse(is_light_period(NIGHT, P))
        self.assertFalse(is_light_period(datetime(2026, 6, 10, 0, 59), P))


class FeatureTest(unittest.TestCase):
    def test_state_keys_fixed(self):
        st = build_state(snap(), P)
        self.assertEqual(list(st), ["crop", "time", "temperature", "humidity", "light", "soil", "irrigation", "fan",
                                    "data"])
        self.assertEqual(FEATURE_VERSION, 3)

    def test_sentences(self):
        self.assertIn("above max 30.0 by 1.2", build_state(snap(temperature=31.2), P)["temperature"])
        self.assertIn("close to max (within 1.0 C)", build_state(snap(temperature=29.5), P)["temperature"])
        self.assertIn("comfortably below max", build_state(snap(temperature=24.0), P)["temperature"])
        self.assertIn("below min 60.0 by 13.0", build_state(snap(humidity=47.0), P)["humidity"])
        self.assertIn("close to max (within 5.0 %)", build_state(snap(humidity=66.0), P)["humidity"])
        self.assertIn("NOT calibrated", build_state(snap(soil_calibrated=False), P)["soil"])
        self.assertIn("calibration unknown", build_state(snap(soil_calibrated=None), P)["soil"])
        self.assertIn("not used at night", build_state(snap(local_time=NIGHT), P)["light"])
        self.assertIn("night", build_state(snap(local_time=NIGHT), P)["time"])
        self.assertIn("STALE", build_state(snap(max_data_age_s=31), P)["data"])
        self.assertIn("already at max", build_state(snap(fan_pct=100), P)["fan"])

    def test_irrigation_sentence(self):
        s = build_state(snap(irrigations_today=4, minutes_since_irrigation=10), P)["irrigation"]
        self.assertIn("4 of 4", s)
        self.assertIn("daily limit reached", s)
        self.assertIn("60 min wait NOT passed", s)
        s = build_state(snap(irrigations_today=1, minutes_since_irrigation=12, moisture_change_since_irrigation=0.2),
                        P)["irrigation"]
        self.assertIn("did NOT rise", s)

    def test_helpers(self):
        self.assertEqual([nearest_step(v) for v in (None, 0, 20, 50, 80, 100)], [0, 0, 1, 2, 2, 3])
        self.assertIs(soil_calibrated_from_status(22520, 11100), False)
        self.assertIs(soil_calibrated_from_status(21000, 11100), True)
        self.assertIsNone(soil_calibrated_from_status(None, 11100))
        s = snap(temperature=None, soil_calibrated=None)
        self.assertEqual(Snapshot.from_dict(s.to_dict()), s)


class FanTest(unittest.TestCase):
    """autoctl 배기팬: 0→33→66→100, 위에서부터 처음 맞는 조건."""
    def test_rules(self):
        self.assertEqual(hard("fan_action", temperature=31.0), "up")
        self.assertEqual(hard("fan_action", temperature=31.0, fan_pct=100), "hold")      # 이미 최대
        self.assertEqual(hard("fan_action", temperature=21.0, fan_pct=66), "off")
        self.assertEqual(hard("fan_action", temperature=21.0, fan_pct=0), "hold")        # 이미 꺼짐
        self.assertEqual(hard("fan_action", humidity=72.0), "up")
        self.assertEqual(hard("fan_action", humidity=55.0), "off")
        self.assertEqual(hard("fan_action", humidity=62.0, fan_pct=66), "down")          # 범위 안으로 돌아옴
        self.assertEqual(hard("fan_action", temperature=29.5, humidity=62.0, fan_pct=66), "hold")   # 최고 − 1 안
        self.assertEqual(hard("fan_action", humidity=66.0), "hold")                       # 습도 최고 − 5 안
        self.assertEqual(hard("fan_action", humidity=62.0, fan_pct=0), "hold")            # 0 에서 더 못 내림
        self.assertEqual(hard("fan_action", temperature=31.0, humidity=55.0), "up")       # 온도 우선
        self.assertEqual(hard("fan_action", temperature=None), "hold")                    # 값 없음 → 유지

    def test_target(self):
        s = snap(fan_pct=33)
        self.assertEqual([labeler.fan_target(s, a) for a in ("off", "down", "hold", "up")], [0, 0, 33, 66])

    def test_soft_at_boundary(self):
        p = labeler.soft_label("fan_action", snap(temperature=30.1, humidity=62.0), P)
        self.assertAlmostEqual(p["up"], 0.7)
        self.assertAlmostEqual(p["hold"], 0.3)
        self.assertEqual(labeler.soft_label("fan_action", snap(temperature=24.0, humidity=67.5), P)["hold"], 1.0)


class LightTest(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(hard("light_action", local_time=NIGHT, led_pct=60), "off")
        self.assertEqual(hard("light_action", local_time=NIGHT, led_pct=0), "off")
        self.assertEqual(hard("light_action", lux=3000), "increase")
        self.assertEqual(hard("light_action", lux=3000, led_pct=100), "hold")
        self.assertEqual(hard("light_action", lux=14000), "decrease")
        self.assertEqual(hard("light_action", lux=14000, led_pct=0), "hold")
        self.assertEqual(hard("light_action", lux=None), "hold")
        self.assertEqual(hard("light_action", lux=9000), "hold")


class IrrigationTest(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(hard("irrigate_now", soil_moisture=25.0), "true")
        self.assertEqual(hard("irrigate_now", soil_moisture=25.0, soil_calibrated=False), "false")
        self.assertEqual(hard("irrigate_now", soil_moisture=25.0, soil_calibrated=None), "false")
        self.assertEqual(hard("irrigate_now", soil_moisture=25.0, soil_sensor_ok=False), "false")
        self.assertEqual(hard("irrigate_now", soil_moisture=25.0, irrigations_today=4), "false")
        self.assertEqual(hard("irrigate_now", soil_moisture=25.0, irrigations_today=1, minutes_since_irrigation=30),
                         "false")
        self.assertEqual(hard("irrigate_now", soil_moisture=25.0, irrigations_today=1, minutes_since_irrigation=61),
                         "true")
        self.assertEqual(hard("irrigate_now", soil_moisture=25.0, pump_on=True), "false")
        self.assertEqual(hard("irrigate_now", soil_moisture=45.0), "false")
        self.assertEqual(hard("irrigate_now", soil_moisture=25.0, local_time=NIGHT), "true")   # 하루 종일


class StressSuspectTest(unittest.TestCase):
    def test_stress(self):
        self.assertEqual(hard("plant_stress"), "none")
        self.assertEqual(hard("plant_stress", humidity=50.0), "mild")
        self.assertEqual(hard("plant_stress", soil_moisture=70.0), "mild")
        self.assertEqual(hard("plant_stress", lux=3000, led_pct=100), "mild")
        self.assertEqual(hard("plant_stress", temperature=31.0), "moderate")
        self.assertEqual(hard("plant_stress", soil_moisture=25.0, irrigations_today=4), "moderate")
        self.assertEqual(hard("plant_stress", temperature=33.5), "severe")
        self.assertEqual(hard("plant_stress", temperature=18.5), "severe")

    def test_suspect(self):
        R = lambda **kw: labeler.suspect_reasons(snap(**kw), P)
        self.assertEqual(R(), [])
        self.assertIn("stale", R(max_data_age_s=31))
        self.assertIn("missing_temp_humidity", R(humidity=None))
        self.assertIn("missing_lux", R(lux=None))
        self.assertEqual(R(lux=None, local_time=NIGHT), [])             # 밤에는 조도를 안 본다
        self.assertIn("soil_sensor", R(soil_sensor_ok=False, soil_moisture=None))
        self.assertIn("stuck_temperature", R(temp_flat_30m=True))
        self.assertIn("led_high_lux_low", R(led_pct=100, lux=545))
        self.assertIn("no_rise_after_watering", R(irrigations_today=1, minutes_since_irrigation=15,
                                                  moisture_change_since_irrigation=0.3))

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
        q2["fan_action"]["instructions"] += " x"
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
        scen = {sc for _, sc in synth.generate(P, {"n": 3000, "seed": 7})}
        self.assertTrue(set(synth.ANOMALIES) <= scen)

    def test_every_answer_appears(self):
        seen = {q: set() for q in Q}
        for s, _ in synth.generate(P, {"n": 4000, "seed": 3}):
            for q, g in labeler.label(s, P, Q).items():
                seen[q].add(top(g["probabilities"]))
        for q in Q:
            self.assertEqual(seen[q], set(options_of(Q[q])), q)


class CalibrationTest(unittest.TestCase):
    def test_identity(self):
        p = {"a": 0.7, "b": 0.3}
        self.assertEqual(cal.apply_temperature(p, 1.0), p)
        q = cal.apply_temperature(p, 2.0)
        self.assertLess(q["a"], 0.7)
        self.assertAlmostEqual(sum(q.values()), 1.0)

    def test_overconfident_gets_T_above_1(self):
        rng = random.Random(0)
        pairs = []
        for _ in range(2000):
            right = rng.random() < 0.75
            gold = {"a": 1.0, "b": 0.0} if right else {"a": 0.0, "b": 1.0}
            pairs.append(({"a": 0.99, "b": 0.01}, gold))
        T = cal.fit_temperature(pairs)
        self.assertGreater(T, 1.5)
        self.assertLess(cal.ece([(cal.apply_temperature(p, T), g) for p, g in pairs]), cal.ece(pairs))
        self.assertAlmostEqual(cal.question_report(pairs, T)["accuracy"], 0.75, delta=0.03)


if __name__ == "__main__":
    unittest.main()
