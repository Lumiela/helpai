"""네이버 쪽 시험 (젯슨·Gemini 없이): python -m unittest discover -s tests -v"""

import base64
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from crop_monitor import gemini, keys, remote  # noqa: E402
from crop_monitor.modules.rose_disease.leafdx import vlm as leaf_vlm  # noqa: E402
from crop_monitor.modules.rose_growth import adapter as growth_adapter  # noqa: E402
from crop_monitor.modules.rose_growth import remote_pipeline as rp  # noqa: E402
from crop_monitor.modules.rose_growth.rose_pipeline.config import load_config  # noqa: E402
from crop_monitor.modules.rose_growth.rose_pipeline.detection import BudDetection  # noqa: E402
from crop_monitor.modules.rose_growth.rose_pipeline.plant_vlm import VlmMissedFinder, fraction_box  # noqa: E402
from crop_monitor.modules.rose_growth.rose_pipeline.vlm import VlmGradeClassifier  # noqa: E402
from crop_monitor.registry import height_config, remote_config, stereo_pair  # noqa: E402

KEY = "test-key-1234567890"
USAGE = dict(total_input_tokens=100, total_output_tokens=10, total_thought_tokens=5, total_cached_tokens=0)


def completed(obj, status="completed"):
    return 200, {"status": status, "usage": USAGE, "steps": [
        {"type": "thought", "signature": "x"},
        {"type": "model_output", "content": [{"type": "text", "text": obj if isinstance(obj, str) else json.dumps(obj)}]}]}


class FakeGemini:
    """정해 둔 응답을 차례로 돌려주는 가짜 서버."""

    def __init__(self, answers):
        self.answers, self.seen = list(answers), []
        fake = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.seen.append((self.path, dict(self.headers), body))
                code, obj = fake.answers.pop(0) if len(fake.answers) > 1 else fake.answers[0]
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.client = gemini.GeminiClient(KEY, base_url="http://127.0.0.1:%d/v1beta" % self.srv.server_port, retries=2, backoff_s=0.01)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}


class GeminiTests(unittest.TestCase):
    def test_request_and_answer(self):
        with FakeGemini([completed({"a": 1})]) as g:
            ans, usage = g.client.generate_json("gemini-4-hydrogen", "sys", [gemini.text_part("hi"), gemini.image_part("QUJD")], SCHEMA)
        self.assertEqual(ans, {"a": 1})
        self.assertEqual(usage, dict(input_tokens=100, output_tokens=10, thought_tokens=5, cached_tokens=0))
        path, headers, body = g.seen[0]
        self.assertEqual(path, "/v1beta/interactions")
        h = {k.lower(): v for k, v in headers.items()}
        self.assertEqual((h["x-goog-api-key"], h["api-revision"]), (KEY, "2026-05-20"))
        self.assertIs(body["store"], False)
        self.assertEqual(body["system_instruction"], "sys")
        self.assertEqual(body["input"][1], {"type": "image", "data": "QUJD", "mime_type": "image/jpeg"})
        self.assertEqual(body["response_format"], {"type": "text", "mime_type": "application/json", "schema": SCHEMA})
        self.assertEqual(body["generation_config"]["thinking_level"], "low")
        self.assertNotIn(KEY, json.dumps(body))

    def test_retry_then_ok(self):
        with FakeGemini([(429, {"error": {"message": "quota"}}), (503, {"error": {"message": "busy"}}), completed({"a": 2})]) as g:
            self.assertEqual(g.client.generate_json("m", "s", [], SCHEMA)[0], {"a": 2})
            self.assertEqual(len(g.seen), 3)

    def test_gives_up_after_retries(self):
        with FakeGemini([(500, {"error": {"message": "boom"}})]) as g:
            with self.assertRaises(gemini.GeminiError) as c:
                g.client.generate_json("m", "s", [], SCHEMA)
            self.assertNotIsInstance(c.exception, gemini.GeminiFatal)
            self.assertEqual(len(g.seen), 3)

    def test_bad_key_is_fatal_and_hidden(self):
        with FakeGemini([(400, {"error": {"message": "API key not valid: " + KEY}})]) as g:
            with self.assertRaises(gemini.GeminiFatal) as c:
                g.client.generate_json("m", "s", [], SCHEMA)
            self.assertEqual(len(g.seen), 1)
            self.assertNotIn(KEY, str(c.exception))

    def test_error_message_in_list(self):
        with FakeGemini([(400, [{"error": {"message": "API key not valid. Please pass a valid API key."}}])]) as g:
            with self.assertRaisesRegex(gemini.GeminiFatal, "API key not valid"):
                g.client.generate_json("m", "s", [], SCHEMA)

    def test_incomplete_and_bad_json(self):
        for ans in (completed({"a": 1}, "incomplete"), completed("not json"), completed("")):
            with FakeGemini([ans]) as g, self.assertRaises(gemini.GeminiError):
                g.client.generate_json("m", "s", [], SCHEMA)

    def test_cost(self):
        self.assertAlmostEqual(gemini.cost_usd("gemini-4-hydrogen", 1_000_000, 100_000, 100_000), 3.0)
        self.assertIsNone(gemini.cost_usd("unknown", 1, 1))


class VlmTests(unittest.TestCase):
    def crops(self, n):
        return [np.full((40, 40, 3), 30 * i, np.uint8) for i in range(n)]

    def test_grade_batches_and_missing_answer(self):
        with FakeGemini([completed({"results": [{"crop": 1, "grade": "grade_two", "confidence": 0.9}, {"crop": 2, "grade": "not_bud", "confidence": 1.4}]}),
                         completed({"results": []})]) as g:
            c = VlmGradeClassifier(g.client, batch_size=2)
            with self.assertWarns(UserWarning):
                out = c.classify(self.crops(3))
        self.assertEqual(out, [("grade_two", 0.9), ("not_bud", 1.0), ("not_bud", 0.0)])
        self.assertEqual(c.usage["requests"], 2)
        self.assertEqual(c.usage["input_tokens"], 200)
        images = [p for p in g.seen[0][2]["input"] if p["type"] == "image"]
        self.assertEqual(len(images), 2)
        self.assertEqual(cv2.imdecode(np.frombuffer(base64.b64decode(images[0]["data"]), np.uint8), 1).shape, (256, 256, 3))

    def test_box_order(self):
        self.assertEqual(fraction_box([100, 200, 300, 400]), {"x1": 0.2, "y1": 0.1, "x2": 0.4, "y2": 0.3})
        self.assertEqual(fraction_box([-5, 0, 2000, 10])["y2"], 1.0)
        self.assertIsNone(fraction_box([1, 2, 3]))
        self.assertIsNone(fraction_box(None))

    def test_missed_finder_position(self):
        class Plant:
            cell_id, offset, hole = "R1C2", (100, 50), None
            mask = np.ones((100, 200), bool)

            def bbox(self):
                return 100, 50, 300, 150

        answer = {"summary": "added 1 bud", "buds": [{"grade": "grade_one", "confidence": 0.8, "box_2d": [500, 250, 750, 500]},
                                                     {"grade": "grade_one", "confidence": 0.2, "box_2d": [0, 0, 10, 10]}]}
        with FakeGemini([completed(answer)]) as g:
            f = VlmMissedFinder(g.client, crop_padding=0.0, max_workers=1)
            added = f.find_missed(np.zeros((400, 600, 3), np.uint8), [Plant()], [])
        self.assertEqual(len(added), 1)                       # 확신이 낮은 답은 버림
        np.testing.assert_allclose(added[0].xyxy, [150, 100, 200, 125])
        self.assertTrue(added[0].added)
        self.assertEqual(added[0].cell_id, "R1C2")

    def test_leaf_verifier(self):
        ans = {"label": "black_spot", "confidence": "high", "symptoms": "검은 반점", "reason": "반점"}
        rgb = np.zeros((200, 200, 3), np.uint8)
        leaf = dict(box=[50, 50, 120, 120], polygon=[[50, 50], [120, 50], [120, 120]], state="FRESH", anomaly_class=None)
        with FakeGemini([completed(ans)]) as g:
            v = leaf_vlm.GeminiVerifier(KEY, client=g.client)
            out = v(leaf_vlm.views(rgb, leaf))
        self.assertEqual(out["label"], "black_spot")
        self.assertEqual(leaf_vlm.verdict(leaf, out["label"]), "MISSED")
        self.assertEqual(v.tokens, [100, 15])
        self.assertEqual([p["type"] for p in g.seen[0][2]["input"]], ["text", "image", "image"])

    def test_leaf_verifier_soft_and_fatal(self):
        with FakeGemini([completed({"label": "healthy"}, "incomplete")]) as g:
            self.assertEqual(leaf_vlm.GeminiVerifier(KEY, client=g.client)([])["label"], "unclear")
        with FakeGemini([(403, {"error": {"message": "denied"}})]) as g, self.assertRaises(gemini.GeminiFatal):
            leaf_vlm.GeminiVerifier(KEY, client=g.client)([])


def png(mask):
    return base64.b64encode(cv2.imencode(".png", mask.astype(np.uint8) * 255)[1].tobytes()).decode()


class RemoteGrowthTests(unittest.TestCase):
    """실제 칸 좌표와 11-24 배치를 흉내 낸 사진으로, 젯슨 응답을 가짜로 넣어 등급이 올바른 칸에 붙는지 본다."""

    def setUp(self):
        self.cfg = load_config("config/rose/growth.yaml")
        self.images = {}
        for cam in ("CL", "CR"):
            im = np.full((1500, 2000, 3), 90, np.uint8)
            for cid, (x, y) in self.cfg["holes"][cam].items():
                present = cid in PLANTS
                cv2.circle(im, (int(x * 2000), int(y * 1500)), 150, (60, 200, 60) if present else (20, 20, 20), -1)
            self.images[cam] = im
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = {}
        for cam, im in self.images.items():
            p = Path(self.tmp.name) / (cam + ".png")
            cv2.imwrite(str(p), im)
            self.paths[cam] = str(p)

    def tearDown(self):
        self.tmp.cleanup()

    def fake_growth(self, cfg, images, rects, detector):
        self.asked = rects
        cams = {}
        for cam, rs in rects.items():
            x, y = self.cfg["holes"][cam]["R1C2"]
            cx, cy, groups = x * 2000, y * 1500, []
            for x1, y1, x2, y2 in rs:
                g = []
                if x1 <= cx <= x2 and y1 <= cy <= y2:
                    bx, by = cx - x1, cy - y1
                    g.append(dict(cls="grade_three", conf=0.9, det_conf=0.7, xyxy=[bx - 20, by - 20, bx + 20, by + 20],
                                  crop_xyxy=[bx - 26, by - 26, bx + 26, by + 26], mask_xy=[int(bx - 20), int(by - 20)], mask_png=png(np.ones((40, 40), bool))))
                groups.append(g)
            cams[cam] = dict(size=[2000, 1500], groups=groups)
        return dict(ok=True, elapsed_s=1.0, result=dict(cameras=cams, stats=dict(candidates=2, classified=2), gpu=dict(cuda=True), timing={}))

    def run_adapter(self, use_vlm, key):
        with mock.patch.object(remote, "growth", self.fake_growth), mock.patch.object(growth_adapter, "read_key", lambda: key), \
                mock.patch("crop_monitor.modules.rose_growth.rose_pipeline.api_key.read_key", lambda: key):
            return growth_adapter.run(self.images, {"use_vlm": use_vlm}, None, self.paths)

    def test_grade_goes_to_right_cell(self):
        out, warns = self.run_adapter(False, None)
        cells = {c["cell"]: c for c in out["cells"]}
        self.assertEqual({c for c, v in cells.items() if v["present"]}, PLANTS)
        self.assertEqual(cells["R1C2"]["grade"], "grade_three")
        self.assertEqual({c: cells[c]["grade"] for c in PLANTS - {"R1C2"}}, {c: "grade_zero" for c in PLANTS - {"R1C2"}})
        self.assertEqual([b["camera"] for b in cells["R1C2"]["buds"]], ["CL", "CR"])
        self.assertEqual(cells["R1C2"]["buds"][0]["mask_px"], 1600)
        self.assertEqual({k: len(v) for k, v in self.asked.items()}, {"CL": 6, "CR": 6})
        self.assertEqual(warns, [])

    def test_no_key_uses_yolo_only(self):
        out, warns = self.run_adapter(True, None)
        self.assertEqual(len(warns), 1)
        self.assertIn("API 키가 없어", warns[0])
        self.assertEqual({c["cell"]: c["grade"] for c in out["cells"]}["R1C2"], "grade_three")

    def test_gemini_review_and_missed(self):
        def fake(self_, model, system, parts, schema, thinking, max_tokens):
            u = dict(input_tokens=1, output_tokens=1, thought_tokens=0, cached_tokens=0)
            if "results" in schema["properties"]:
                n = sum(p["type"] == "image" for p in parts)
                return {"results": [{"crop": i + 1, "grade": "grade_four", "confidence": 0.95} for i in range(n)]}, u
            return {"summary": "nothing missed", "buds": []}, u

        with mock.patch.object(gemini.GeminiClient, "generate_json", fake):
            out, warns = self.run_adapter(True, KEY)
        cell = {c["cell"]: c for c in out["cells"]}["R1C2"]
        self.assertEqual(cell["grade"], "grade_four")
        self.assertEqual((cell["buds"][0]["cls_class"], cell["buds"][0]["class"]), ("grade_three", "grade_four"))
        self.assertEqual(out["usage"]["review_changed"], 2)
        self.assertEqual(out["usage"]["requests"], 2 + 12)      # 검수 2번(카메라별) + 누락 확인 12번(식물 6 × 카메라 2)
        self.assertEqual(warns, [])

    def test_gemini_failure_falls_back(self):
        def boom(*a, **k):
            raise gemini.GeminiFatal("Gemini 요청 거절 (HTTP 403): denied")

        calls = []
        orig = self.fake_growth
        self.fake_growth = lambda *a: (calls.append(1), orig(*a))[1]
        with mock.patch.object(gemini.GeminiClient, "generate_json", boom):
            out, warns = self.run_adapter(True, KEY)
        self.assertEqual({c["cell"]: c["grade"] for c in out["cells"]}["R1C2"], "grade_three")
        self.assertIn("YOLO 결과만 사용", warns[0])
        self.assertEqual(len(calls), 1)                         # 젯슨에 다시 묻지 않음


PLANTS = {"R1C2", "R1C4", "R2C1", "R2C3", "R3C2", "R3C4"}


class ConfigTests(unittest.TestCase):
    def test_settings(self):
        self.assertEqual(stereo_pair(), ("CL", "CR"))
        self.assertIsNone(height_config().calib)
        self.assertEqual(remote_config().on_bad_calib, "skip")

    def test_key_file(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(keys, "SECRETS_DIR", Path(d)), mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch.object(keys, "ENV_FILE", Path(d) / "none.env"):
            self.assertIsNone(keys.read_key())
            (Path(d) / "GEMINI_API_KEY").write_text("# 주석\nGEMINI_API_KEY='abc'\n", encoding="utf-8")
            self.assertEqual(keys.read_key(), "abc")

    def test_env_file(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(keys, "SECRETS_DIR", Path(d)), mock.patch.dict("os.environ", {}, clear=True):
            env = Path(d) / ".env"
            with mock.patch.object(keys, "ENV_FILE", env):
                for text, want in (("A=1\nGEMINI_API_KEY=abc\nB=2\n", "abc"), ("GEMINI_API_KEY='a b'\n", "a b"), ('export GEMINI_API_KEY="x#y"\n', "x#y"),
                                   ("GEMINI_API_KEY=abc # 메모\n", "abc"), ("GEMINI_API_KEY = abc\n", "abc"), ("\ufeffGEMINI_API_KEY=abc\r\n", "abc"),
                                   ("GEMINI_API_KEY=\n", None), ("#GEMINI_API_KEY=abc\n", None), ("MY_GEMINI_API_KEY=abc\n", None),
                                   ("GEMINI_API_KEY=old\nGEMINI_API_KEY=new\n", "new")):
                    env.write_text(text, encoding="utf-8", newline="")
                    self.assertEqual(keys.read_key(), want, text)
                env.write_text("GEMINI_API_KEY=from_env_file\n", encoding="utf-8")
                (Path(d) / "GEMINI_API_KEY").write_text("", encoding="utf-8")             # 빈 파일은 무시
                self.assertEqual(keys.read_key(), "from_env_file")
                with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "from_environ"}):
                    self.assertEqual(keys.read_key(), "from_environ")
                (Path(d) / "GEMINI_API_KEY").write_text("from_secrets\n", encoding="utf-8")
                self.assertEqual(keys.read_key(), "from_secrets")

    def test_bud_shift(self):
        d = rp.to_detection(dict(cls="grade_one", conf=0.5, det_conf=0.4, xyxy=[1, 2, 11, 12], crop_xyxy=[0, 0, 14, 14], mask_xy=[1, 2],
                                 mask_png=png(np.eye(10, dtype=bool)))).shifted(100, 200)
        self.assertIsInstance(d, BudDetection)
        np.testing.assert_allclose(d.xyxy, [101, 202, 111, 212])
        self.assertEqual((d.mask_xy, int(d.mask.sum())), ((101, 202), 10))


class PreprocessAndMergeTests(unittest.TestCase):
    def test_resize_to_team_width(self):
        from crop_monitor.nodes.preprocess import preprocess

        with tempfile.TemporaryDirectory() as d:
            rng = np.random.default_rng(0)
            for cam, size in (("CL", (1920, 2560)), ("CR", (1500, 2000))):
                cv2.imwrite(str(Path(d) / (cam + ".jpg")), rng.integers(0, 255, (*size, 3), np.uint8))
            r = preprocess({"images": {c: str(Path(d) / (c + ".jpg")) for c in ("CL", "CR")}, "out_dir": d})
            self.assertEqual({k: v.shape for k, v in r["frames"].items()}, {"CL": (1500, 2000, 3), "CR": (1500, 2000, 3)})
            np.testing.assert_array_equal(cv2.imread(r["images"]["CL"]), r["frames"]["CL"])     # 젯슨도 같은 픽셀을 봄
            self.assertTrue(r["images"]["CL"].endswith("CL.png") and r["images"]["CR"].endswith("CR.jpg"))

    def graph(self, growth, disease):
        from crop_monitor import nodes, build_graph
        from crop_monitor.nodes import modules

        def adapter(module):
            m = mock.Mock()
            m.run = growth if module == "rose_growth" else disease
            return m

        with tempfile.TemporaryDirectory() as d:
            for c in ("CL", "CR"):
                cv2.imwrite(str(Path(d) / (c + ".jpg")), np.zeros((150, 200, 3), np.uint8))
            with mock.patch.object(modules, "_adapter", adapter), mock.patch.object(remote, "height", mock.Mock(side_effect=remote.RemoteError("x"))), \
                    mock.patch.object(modules.traceback, "print_exc", lambda: None):
                return build_graph().invoke({"crop_id": "A", "images": {c: str(Path(d) / (c + ".jpg")) for c in ("CL", "CR")}, "use_vlm": False},
                                            config={"max_concurrency": 1})

    GROWTH = ({"grades": [], "segments": {}, "table": "t", "usage": None, "cells": [dict(
        cell="R1C2", row=1, col=2, present=True, grade="grade_one", camera_grades={}, buds=[])]}, [])
    DISEASE = ({"cls_model": "m", "cameras": {}, "vlm": None}, [])

    def test_all_failed_is_not_normal(self):
        boom = mock.Mock(side_effect=remote.RemoteError("젯슨 응답 500"))
        out = self.graph(boom, boom)
        self.assertEqual(out["crop_data"]["status"], "incomplete")
        self.assertIn("종합 상태: **판정 불가**", out["report"])

    def test_disease_failed(self):
        out = self.graph(mock.Mock(return_value=self.GROWTH), mock.Mock(side_effect=remote.RemoteError("x")))
        self.assertIn("종합 상태: **판정 불가**", out["report"])
        self.assertIn("판정 못 함", out["report"])

    def test_all_ok(self):
        out = self.graph(mock.Mock(return_value=self.GROWTH), mock.Mock(return_value=self.DISEASE))
        self.assertIn("종합 상태: **정상**", out["report"])
        self.assertNotIn("판정 못 함", out["report"])

    def test_error_shows_last_line(self):
        self.assertIn("OutOfMemoryError", remote._short({"ok": False, "error": "worker failed", "log": "a\n" * 400 + "torch.cuda.OutOfMemoryError: CUDA out of memory\n"}))


class HeightNodeTests(unittest.TestCase):
    def call(self, check, on_bad="skip"):
        from crop_monitor.nodes import modules

        import io
        buf = io.BytesIO()
        np.savez(buf, height_cm=np.full((480, 640), 5, np.float32), depth_cm=np.full((480, 640), 50, np.float32), plane=np.array([0, 0, -1, 55.0]),
                 calib_K1=np.eye(3), calib_D1=np.zeros(5), calib_R1=np.eye(3), calib_P1=np.eye(3, 4), calib_size=np.array([640, 480]))
        resp = dict(ok=True, elapsed_s=1.0, result=dict(info=dict(align=dict(mode="calib"), map_size=[640, 480]), src_size=[2560, 1920], check=check, timing={}))
        rc = remote.RemoteConfig(on_bad_calib=on_bad)
        with mock.patch.object(remote, "height", lambda *a: (resp, buf.getvalue())), mock.patch.object(modules, "remote_config", lambda: rc):
            return modules.height({"images": {"CL": "a", "CR": "b"}})

    def test_good(self):
        r = self.call(dict(mode="calib", matches=300, dy_px=0.6, disparity_px=45.0))
        self.assertEqual((r["warnings"], r["height"].height_cm.shape), ([], (480, 640)))
        self.assertTrue(Path(r["height"].info["calib"]).is_file())

    def test_bad_calibration_is_not_reported(self):
        for chk in (dict(mode="calib", matches=300, dy_px=16.1, disparity_px=-61.0), dict(mode="calib", matches=5, dy_px=None, disparity_px=None),
                    dict(mode="y-align", matches=300, dy_px=0.5, disparity_px=40.0)):
            r = self.call(chk)
            self.assertIsNone(r["height"])
            self.assertIn("다시 보정", r["warnings"][0])
        self.assertIsNotNone(self.call(dict(mode="calib", matches=300, dy_px=3.0, disparity_px=45.0), "warn")["height"])


if __name__ == "__main__":
    unittest.main()
