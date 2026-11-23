"""혼합형 검출기(HybridBudDetector) 검증 — 모델 없이 가짜 det / FastSAM / 분류 모델로 확인.

1) 단위: 박스 → 마스크 매칭, not_bud·낮은 확신 버림, 마스크 기준 식물 배정
2) VLM 분류기: 가짜 Claude 클라이언트로 요청 형식·묶음·응답 처리·cascade 확인 (API 호출 없음)
3) 전체: test_pipeline.py 의 합성 트레이로 3×4 결과가 기대값과 같은지
   (FastSAM 있음/없음 × plant_crop/full_image, VLM 분류기, 화분 단위 VLM(vlm_plant))

    python tests/test_hybrid.py
"""

import base64
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rose_pipeline import RosePipeline  # noqa: E402
from rose_pipeline.assignment import mask_overlap  # noqa: E402
from rose_pipeline.config import load_config  # noqa: E402
from rose_pipeline.detection import NOT_BUD, BudDetection, HybridBudDetector  # noqa: E402
from rose_pipeline.layout import Hole  # noqa: E402
from rose_pipeline.segmentation import PlantSegment  # noqa: E402
from rose_pipeline.plant_vlm import VlmMissedFinder, VlmPlantGrader  # noqa: E402
from rose_pipeline.vlm import CascadeClassifier, EmbedGradeClassifier, VlmGradeClassifier  # noqa: E402
from rose_pipeline.visualize import draw_board  # noqa: E402
from test_pipeline import BUD_HUE, BUDS_ONLY, EXPECTED, VIEWS, draw_tray, make_view  # noqa: E402

CLS_NAMES = {0: "grade_four", 1: "grade_one", 2: "grade_three", 3: "grade_two", 4: NOT_BUD}
CLS_INDEX = {v: k for k, v in CLS_NAMES.items()}


class T:
    """torch 텐서 흉내 (.cpu().numpy())."""

    def __init__(self, a):
        self.a = np.asarray(a)

    def cpu(self):
        return self

    def numpy(self):
        return self.a


class FakeDet:
    def __init__(self, fn):
        self.fn = fn  # image -> [(x1, y1, x2, y2, conf)]

    def predict(self, image, **_):
        b = np.float32(self.fn(image)).reshape(-1, 5)
        return [SimpleNamespace(names={0: "bud"},
                                boxes=SimpleNamespace(xyxy=T(b[:, :4]), conf=T(b[:, 4]), cls=T(np.zeros(len(b)))))]


class FakeSam:
    def __init__(self, fn):
        self.fn = fn  # image -> (N, H, W) bool 또는 None

    def __call__(self, image, **_):
        m = self.fn(image)
        return [SimpleNamespace(masks=None if m is None else SimpleNamespace(data=T(m.astype(np.float32))))]


class FakeCls:
    def __init__(self, fn):
        self.fn = fn  # crop -> (클래스 이름, 확신)
        self.seen = []

    def predict(self, crops, **_):
        out = []
        for c in crops:
            self.seen.append(c.shape[:2])
            name, p = self.fn(c)
            out.append(SimpleNamespace(names=CLS_NAMES, probs=SimpleNamespace(top1=CLS_INDEX[name], top1conf=p)))
        return out


class FakeClaude:
    """anthropic.Anthropic 흉내. 요청의 크롭 이미지를 꺼내 fn(crop) -> (등급, 확신) 으로 답함."""

    def __init__(self, fn, drop=()):
        self.fn, self.drop, self.requests = fn, set(drop), []
        self.messages = SimpleNamespace(create=self.create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.requests.append(kw)
        content = kw["messages"][0]["content"]
        crops, after = [], False
        for b in content:
            if b["type"] == "text" and b["text"].startswith("Now grade"):
                after = True
            elif after and b["type"] == "image":
                data = np.frombuffer(base64.standard_b64decode(b["source"]["data"]), np.uint8)
                crops.append(cv2.imdecode(data, cv2.IMREAD_COLOR))
        results = [{"crop": k, "grade": g, "confidence": p}
                   for k, (g, p) in ((k, self.fn(c)) for k, c in enumerate(crops, 1)) if k not in self.drop]
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(
            {"results": results}))], usage=SimpleNamespace(input_tokens=100, output_tokens=10,
                                                         cache_read_input_tokens=0, cache_creation_input_tokens=0))


class FakePlantClaude:
    """vlm_plant 용 anthropic.Anthropic 흉내. 식물 사진(마지막 이미지)에서 밝은 봉오리 색을 찾아
    {grade, confidence, box(0~1 비율)} 목록으로 답함. 어둡게 처리된 이웃 식물 봉오리는 색 기준(V≥150)에서 빠짐."""

    def __init__(self):
        self.requests = []
        self.messages = SimpleNamespace(create=self.create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.requests.append(kw)
        img_block = [b for b in kw["messages"][0]["content"] if b["type"] == "image"][-1]
        data = np.frombuffer(base64.standard_b64decode(img_block["source"]["data"]), np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        h, w = img.shape[:2]
        buds = []
        for g, hue in BUD_HUE.items():
            n, _, stats, _ = cv2.connectedComponentsWithStats(bud_color_mask(img, hue).astype(np.uint8))
            for x, y, bw, bh, area in stats[1:]:
                if area >= 30:
                    buds.append({"grade": g, "confidence": 0.9,
                                 "box": {"x1": x / w, "y1": y / h, "x2": (x + bw) / w, "y2": (y + bh) / h}})
        buds.append({"grade": "grade_four", "confidence": 0.2,   # 확신 낮음 → min_conf 로 버려져야 함
                     "box": {"x1": 0.0, "y1": 0.0, "x2": 0.1, "y2": 0.1}})
        summary = f"{len(buds) - 1} buds seen; ün-ascii ok"   # 비 ASCII 글자는 결과 이미지용으로 정리돼야 함
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(
            {"summary": summary, "buds": buds}))], usage=SimpleNamespace(input_tokens=1000, output_tokens=50,
                                                    cache_read_input_tokens=0, cache_creation_input_tokens=0))


# ── 합성 트레이용 가짜 모델: 봉오리 색으로 판단 ───────────────────────────
def bud_color_mask(image, hue):
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, (max(hue - 3, 0), 180, 150), (min(hue + 3, 179), 255, 255)) > 0


def color_boxes(image):
    """등급 구분 없이 봉오리 위치만 (박스를 일부러 6px 크게 = 부정확한 검출기)."""
    boxes = []
    for hue in BUD_HUE.values():
        n, _, stats, _ = cv2.connectedComponentsWithStats(bud_color_mask(image, hue).astype(np.uint8))
        for x, y, w, h, area in stats[1:]:
            if area >= 80:
                boxes.append((x - 6, y - 6, x + w + 6, y + h + 6, 0.8))
    boxes.append((5, 5, 35, 35, 0.3))  # 오탐지 (구석 배경) → 분류에서 not_bud 로 버려져야 함
    return boxes


def color_segments(image):
    """FastSAM 흉내: 봉오리 하나하나 + 잎 전체를 조각으로."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    segs = [cv2.inRange(hsv, (30, 80, 60), (90, 255, 255)) > 0]  # 잎 덩어리
    for hue in BUD_HUE.values():
        n, lab = cv2.connectedComponents(bud_color_mask(image, hue).astype(np.uint8))
        segs += [lab == i for i in range(1, n)]
    return np.stack(segs)


def color_grade(crop):
    counts = {g: int(bud_color_mask(crop, hue).sum()) for g, hue in BUD_HUE.items()}
    g = max(counts, key=counts.get)
    return (g, 0.9) if counts[g] >= 30 else (NOT_BUD, 0.95)


# ── 1) 단위 검사 ─────────────────────────────────────────────────────────
def unit_checks(failures):
    img = np.full((200, 300, 3), 120, np.uint8)
    bud = np.zeros((200, 300), bool)
    cv2.circle(bud.view(np.uint8), (100, 100), 20, 1, -1)
    leaf = np.zeros((200, 300), bool)
    leaf[:, 150:] = True
    boxes = [(70, 70, 130, 130, 0.9),     # 봉오리 (박스가 마스크보다 큼) → grade_three, 박스가 마스크에 맞게 줄어야 함
             (160, 20, 190, 50, 0.4),     # 잎 위 오탐지 → not_bud
             (10, 150, 40, 180, 0.5)]     # 조각 없음 → 마스크 없이 박스만, 확신 낮음 → 버림
    answers = iter([("grade_three", 0.8), (NOT_BUD, 0.9), ("grade_one", 0.3)])
    cls = FakeCls(lambda crop: next(answers))
    det = HybridBudDetector(FakeDet(lambda im: boxes), cls, FakeSam(lambda im: np.stack([leaf, bud])))
    dets = det.detect(img)
    if [d.class_name for d in dets] != ["grade_three"]:
        failures.append(f"단위: 남은 봉오리 {[d.class_name for d in dets]} (기대 ['grade_three'])")
    else:
        d = dets[0]
        if d.mask is None or abs(int(d.mask.sum()) - int(bud.sum())) > 5:
            failures.append("단위: 봉오리 마스크가 FastSAM 조각과 다름")
        if not np.allclose(d.xyxy, [80, 80, 121, 121], atol=1):
            failures.append(f"단위: 마스크로 다시 잡은 박스 {d.xyxy} (기대 약 [80, 80, 121, 121])")
        if d.det_conf != np.float32(0.9) or d.conf != 0.8:
            failures.append(f"단위: 신뢰도 det={d.det_conf} cls={d.conf}")
    if any(abs((h - w)) > 1 for h, w in cls.seen):
        failures.append(f"단위: 분류 크롭이 정사각형이 아님 {cls.seen}")

    # 마스크가 없으면(=FastSAM 생략) 박스 그대로
    cls2 = FakeCls(lambda crop: ("grade_two", 0.9))
    dets = HybridBudDetector(FakeDet(lambda im: boxes[:1]), cls2, sam=None).detect(img)
    if len(dets) != 1 or dets[0].mask is not None or not np.allclose(dets[0].xyxy, [70, 70, 130, 130]):
        failures.append("단위: FastSAM 생략 시 박스를 그대로 써야 함")

    # 배정: 박스 중심은 A 식물인데 마스크는 대부분 B 식물 → mask_overlap 은 B
    hole = Hole("R1C1", 1, 1, np.float32([0, 0]), 10.0, 30.0)
    a = PlantSegment("R1C1", "CL", hole, np.ones((100, 50), bool), (0, 0))      # x 0~49
    b = PlantSegment("R1C2", "CL", hole, np.ones((100, 50), bool), (50, 0))     # x 50~99
    m = np.zeros((20, 40), bool)
    m[:, 25:] = True                                                             # 마스크는 x 55~69
    bud_det = BudDetection("grade_one", 0.9, np.float32([30, 10, 70, 30]), mask=m, mask_xy=(30, 10))
    if mask_overlap(bud_det, [a, b]) is not b:
        failures.append("단위: mask_overlap 이 마스크가 더 많이 겹친 식물을 고르지 않음")
    shifted = bud_det.shifted(100, 5)
    if shifted.mask_xy != (130, 15) or shifted.mask is not m:
        failures.append(f"단위: shifted 후 마스크 위치 {shifted.mask_xy}")


# ── 2) VLM 분류기 ────────────────────────────────────────────────────────
def vlm_checks(failures):
    crops = [np.full((20 + i, 20 + i, 3), 10 * i, np.uint8) for i in range(5)]
    answers = {0: ("grade_one", 0.9), 1: ("not_bud", 0.8), 2: ("grade_four", 0.7), 3: ("grade_two", 1.4),
               4: ("grade_three", 0.6)}
    fn = lambda c: answers[int(c[0, 0, 0]) // 10 if c[0, 0, 0] < 45 else 4]  # noqa: E731
    ex = [("grade_one", np.zeros((30, 30, 3), np.uint8)), ("not_bud", np.zeros((30, 30, 3), np.uint8))]
    client = FakeClaude(fn)
    vlm = VlmGradeClassifier(client, examples=ex, batch_size=2, image_size=64,
                             grade_descriptions={"grade_one": "green bud"})
    out = vlm.classify(crops)
    if [g for g, _ in out] != ["grade_one", "not_bud", "grade_four", "grade_two", "grade_three"]:
        failures.append(f"VLM: 등급 순서 {out}")
    if out[3][1] != 1.0:
        failures.append(f"VLM: 확신을 0~1 로 자르지 않음 {out[3]}")
    if len(client.requests) != 3 or vlm.usage["requests"] != 3:
        failures.append(f"VLM: batch_size=2 로 5장이면 요청 3번이어야 함 ({len(client.requests)})")
    r = client.requests[0]
    blocks = r["messages"][0]["content"]
    if r["model"] != "claude-opus-5-5" or r["output_config"]["format"]["type"] != "json_schema":
        failures.append("VLM: 모델 또는 structured output 설정이 다름")
    item = r["output_config"]["format"]["schema"]["properties"]["results"]["items"]
    if item["properties"]["grade"]["enum"][0] != NOT_BUD:
        failures.append("VLM: 등급 enum 에 not_bud 가 없음")
    if "fallbacks" not in r or "green bud" not in r["system"]:
        failures.append("VLM: fallbacks 또는 등급 설명이 요청에 없음")
    cached = [i for i, b in enumerate(blocks) if "cache_control" in b]
    n_prefix = 1 + 2 * len(ex)
    if cached != [n_prefix - 1]:
        failures.append(f"VLM: 캐시 표시 위치 {cached} (기대 [{n_prefix - 1}] = 예시 마지막)")
    if sum(b["type"] == "image" for b in blocks) != len(ex) + 2:
        failures.append("VLM: 요청의 이미지 수가 예시 + 크롭 수와 다름")
    if blocks[:n_prefix] != client.requests[1]["messages"][0]["content"][:n_prefix]:
        failures.append("VLM: 요청마다 앞부분(예시)이 달라 캐시가 안 됨")

    # 응답에서 빠진 크롭(3번)은 not_bud(확신 0) 로
    import warnings
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        out = VlmGradeClassifier(FakeClaude(lambda c: ("grade_one", 0.9), drop={3}), batch_size=8).classify(crops[:3])
    if out[2] != (NOT_BUD, 0.0) or out[0] != ("grade_one", 0.9):
        failures.append(f"VLM: 빠진 크롭 처리 {out}")

    # cascade: YOLO 확신 < below 인 크롭만 VLM 에게
    first = SimpleNamespace(classify=lambda cs: [("grade_one", 0.95), ("grade_two", 0.5), ("not_bud", 0.6)])
    seen = []
    second = SimpleNamespace(classify=lambda cs: seen.append(len(cs)) or [("grade_three", 0.8), ("grade_one", 0.7)])
    out = CascadeClassifier(first, second, below=0.8).classify(crops[:3])
    if seen != [2] or [g for g, _ in out] != ["grade_one", "grade_three", "grade_one"]:
        failures.append(f"cascade: {out} / VLM 에 보낸 수 {seen}")


# ── 3) 전체 파이프라인 ───────────────────────────────────────────────────
def pipeline_checks(failures):
    cfg = copy.deepcopy(load_config())
    # 합성 시점(CL = 1~3열, CR = 2~4열)에 맞춘 범위표. 실제 설정의 범위표와 무관하게 고정
    cfg["layout"] = {**cfg["layout"], "coverage": [["CL,CR", "CL,CR", "CL,CR", "CR"],
                                                   ["CL,CR", "CL,CR", "CL,CR", "CR"],
                                                   ["CL", "CL,CR", "CL,CR", "CR"]]}
    images = {}
    for name, (x_range, corners) in VIEWS.items():
        flat = draw_tray(np.random.default_rng(0), BUDS_ONLY.get(name, {}))
        images[name], cfg["holes"][name] = make_view(flat, x_range, corners)

    cfg["grading"]["assignment"] = "mask_overlap"
    for use_sam in (True, False):
        for detect_on in ("plant_crop", "full_image"):
            tag = f"{'FastSAM' if use_sam else '박스만'}/{detect_on}"
            cfg["grading"]["detector"]["detect_on"] = detect_on
            detector = HybridBudDetector(FakeDet(color_boxes), FakeCls(color_grade),
                                         FakeSam(color_segments) if use_sam else None)
            result = RosePipeline(cfg, detector=detector).run(images)
            print(f"[혼합형 {tag}]\n{result.table()}\n")
            if result.grid() != EXPECTED:
                failures.append(f"{tag}: {result.grid()}")
            buds = [b for f in result.frames.values() for b in f.buds]
            board = draw_board(result)
            if board.std() < 5:
                failures.append(f"{tag}: 최종 결과판이 비어 있음")
            if "R1C3  grade_three" not in result.bud_table() or "grade_one 1개" not in result.bud_table():
                failures.append(f"{tag}: 봉오리 표 {result.bud_table()}")
            if any(b.class_name == NOT_BUD for b in buds):
                failures.append(f"{tag}: not_bud 가 결과에 남음")
            if use_sam and not all(b.mask is not None for b in buds):
                failures.append(f"{tag}: 마스크 없는 봉오리가 있음")

    # ③ 을 VLM 으로 (가짜 Claude 가 크롭 색으로 판단) → 결과가 같아야 함
    cfg["grading"]["detector"]["detect_on"] = "plant_crop"
    vlm = VlmGradeClassifier(FakeClaude(color_grade), batch_size=4)
    detector = HybridBudDetector(FakeDet(color_boxes), vlm, FakeSam(color_segments))
    result = RosePipeline(cfg, detector=detector).run(images)
    print(f"[혼합형 VLM 분류]\n{result.table()}\n")
    if result.grid() != EXPECTED:
        failures.append(f"VLM 분류: {result.grid()}")
    if not vlm.usage["requests"]:
        failures.append("VLM 분류: 요청이 없음")

    # ④ Claude 검수: 분류 모델이 모두 grade_four 로 틀리게 답해도 검수(가짜 Claude = 색으로 판단) 답이 최종 등급
    wrong = FakeCls(lambda c: ("grade_four", 0.9))
    reviewer = VlmGradeClassifier(FakeClaude(color_grade), batch_size=4)
    detector = HybridBudDetector(FakeDet(color_boxes), wrong, FakeSam(color_segments), review=reviewer)
    result = RosePipeline(cfg, detector=detector).run(images)
    print(f"[혼합형 + Claude 검수]\n{result.table()}\n{detector.usage}\n")
    if result.grid() != EXPECTED:
        failures.append(f"검수: {result.grid()}")
    buds = [b for f in result.frames.values() for b in f.buds]
    if not buds or any(b.first_class != "grade_four" for b in buds):
        failures.append("검수: first_class(분류 모델 등급)가 남지 않음")
    u = detector.usage
    if not u["review_changed"] or u["reviewed"] != u["classified"] or not u["requests"]:
        failures.append(f"검수 개수: {u}")
    cell = next(c for c in result.to_dict()["cells"] if c["buds"])
    if "cls_class" not in cell["buds"][0]:
        failures.append("검수: result.json 에 cls_class 없음")

    # ⑤ 누락 추가: 위치 모델이 아무것도 못 찾아도 Claude(가짜: 색으로 봉오리 찾음)가 식물마다 추가 → 결과 같음
    finder_client = FakePlantClaude()
    finder = VlmMissedFinder(finder_client, max_workers=3)
    detector = HybridBudDetector(FakeDet(lambda im: []), FakeCls(color_grade), None, missed=finder)
    result = RosePipeline(cfg, detector=detector).run(images)
    print(f"[혼합형 + 누락 추가 (위치 모델 0개)]\n{result.table()}\n{detector.usage}\n")
    if result.grid() != EXPECTED:
        failures.append(f"누락 추가: {result.grid()}")
    buds = [b for f in result.frames.values() for b in f.buds]
    if not buds or not all(b.added for b in buds) or detector.usage["added"] != len(buds):
        failures.append(f"누락 추가: added 표시 {[b.added for b in buds]}, {detector.usage}")
    if "cyan" not in finder_client.requests[0]["system"] or not any(f.vlm for f in result.frames.values()):
        failures.append("누락 추가: 프롬프트 또는 VLM 메모 없음")
    if not any(c.get("buds") and c["buds"][0].get("added_by") == "claude" for c in result.to_dict()["cells"]):
        failures.append("누락 추가: result.json 에 added_by 없음")

    # 위치 모델이 다 찾았으면 같은 봉오리를 다시 추가하지 않아야 함
    finder = VlmMissedFinder(FakePlantClaude(), max_workers=3)
    detector = HybridBudDetector(FakeDet(color_boxes), FakeCls(color_grade), FakeSam(color_segments), missed=finder)
    result = RosePipeline(cfg, detector=detector).run(images)
    if result.grid() != EXPECTED or detector.usage["added"]:
        failures.append(f"누락 추가 중복: {result.grid()}, 추가 {detector.usage['added']}개")

    # 화분 단위 VLM (vlm_plant): YOLO·FastSAM·분류 없이 식물마다 Claude 가 봉오리 위치·등급을 답함
    client = FakePlantClaude()
    grader = VlmPlantGrader(client, grade_descriptions={"grade_one": "closed bud"}, max_workers=3)
    pipe = RosePipeline(cfg, detector=grader)
    result = pipe.run(images)
    print(f"[화분 단위 VLM]\n{result.table()}\n{result.bud_table()}\n")
    if result.grid() != EXPECTED:
        failures.append(f"vlm_plant: {result.grid()}")
    n_plants = sum(len(f.plants) for f in result.frames.values())
    if len(client.requests) != n_plants or grader.usage["requests"] != n_plants:
        failures.append(f"vlm_plant: 식물 {n_plants}개인데 요청 {len(client.requests)}번")
    if "closed bud" not in client.requests[0]["system"] or "not_bud" in json.dumps(
            client.requests[0]["output_config"]["format"]["schema"]):
        failures.append("vlm_plant: 등급 설명이 없거나 스키마에 not_bud 가 있음")
    buds = [b for f in result.frames.values() for b in f.buds]
    if any(b.conf < 0.5 for b in buds):
        failures.append("vlm_plant: min_conf 미만 봉오리가 남음")
    for name, f in result.frames.items():  # 박스가 실제 봉오리 색 위에 놓였는지 (좌표 되돌리기 확인)
        for b in f.buds:
            x1, y1, x2, y2 = b.xyxy.astype(int)
            patch = f.image[max(y1, 0):y2, max(x1, 0):x2]
            if patch.size == 0 or bud_color_mask(patch, BUD_HUE[b.class_name]).sum() < 20:
                failures.append(f"vlm_plant: {name} {b.cell_id} {b.class_name} 박스가 봉오리 위치와 다름 {b.xyxy}")
    if draw_board(result).std() < 5:
        failures.append("vlm_plant: 최종 결과판이 비어 있음")
    # VLM 판단 내용이 결과에 남는지: 칸별 요약·버린 봉오리, result.json, table.txt, vlm/ 사진
    notes = [n for f in result.frames.values() for n in (f.vlm or {}).values()]
    if len(notes) != n_plants or not all(n["summary"].isascii() and "buds seen" in n["summary"] for n in notes):
        failures.append("vlm_plant: 식물별 VLM 요약이 없거나 ASCII 가 아님")
    if not any(not b["kept"] for n in notes for b in n["buds"]):
        failures.append("vlm_plant: 확신이 낮아 버린 봉오리가 기록되지 않음")
    d = result.to_dict()
    if not all("vlm" in c for c in d["cells"] if c["present"]):
        failures.append("vlm_plant: result.json 에 vlm 항목이 없음")
    if "VLM 판단" not in result.vlm_table():
        failures.append("vlm_plant: VLM 판단 표가 없음")
    import tempfile
    from rose_pipeline.visualize import save_outputs
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = save_outputs(result, tmp)
        n_views = len(list((run_dir / "vlm").glob("*.jpg")))
        table = (run_dir / "table.txt").read_text(encoding="utf-8")
    if n_views != n_plants or "VLM 판단" not in table:
        failures.append(f"vlm_plant: vlm/ 사진 {n_views}장 (기대 {n_plants}), table.txt VLM 판단 {'VLM 판단' in table}")


class FakeEmbedModel:
    """yolo26m-cls 의 embed() 흉내: 크롭 평균 색(BGR)을 특징 벡터로."""

    def __init__(self):
        self.calls = 0

    def embed(self, crops, **_):
        self.calls += 1
        return [T(c.reshape(-1, 3).mean(0)) for c in crops]


def embed_checks(failures):
    """EmbedGradeClassifier: 참고 크롭 최근접 투표 / max_box_frac 로 큰 후보 박스 제거."""
    import tempfile
    colors = {"grade_one": (0, 0, 255), "grade_two": (255, 0, 0), NOT_BUD: (0, 255, 0)}
    with tempfile.TemporaryDirectory() as tmp:
        for name, bgr in colors.items():
            (Path(tmp) / name).mkdir()
            for i in range(4):
                img = np.full((32, 32, 3), bgr, np.uint8)
                img[:4] = 40 * i  # 조금씩 다르게
                cv2.imwrite(str(Path(tmp) / name / f"{i}.jpg"), img)
        model = FakeEmbedModel()
        clf = EmbedGradeClassifier(model, tmp, per_class=4, k=3)
        q = [np.full((20, 20, 3), (10, 20, 240), np.uint8), np.full((20, 20, 3), (230, 10, 30), np.uint8),
             np.full((20, 20, 3), (20, 240, 20), np.uint8)]
        got = clf.classify(q)
        if [n for n, _ in got] != ["grade_one", "grade_two", NOT_BUD] or not all(p > 0.9 for _, p in got):
            failures.append(f"embed 분류: {got}")
        try:
            EmbedGradeClassifier(model, Path(tmp) / "없음")
            failures.append("embed: 참고 크롭이 없는데 오류가 나지 않음")
        except FileNotFoundError:
            pass

    img = np.zeros((200, 200, 3), np.uint8)
    boxes = [(0, 0, 200, 190, 0.9), (50, 50, 80, 80, 0.2)]  # 식물 전체 박스 + 작은 후보
    det = HybridBudDetector(FakeDet(lambda im: boxes), FakeCls(lambda c: ("grade_one", 0.9)), sam=None,
                            max_box_frac=0.3)
    kept = det.detect(img)
    if det.stats["candidates"] != 1 or len(kept) != 1 or det.usage["classified"] != 1:
        failures.append(f"max_box_frac: 후보 {det.stats}, 남은 {len(kept)}")

    # max_box_px: 비율로는 통과하는 긴 박스(30x150)를 픽셀 크기로 버림. 남은 봉오리에 등급 판단 크롭 영역 기록
    boxes = [(10, 10, 40, 160, 0.9), (50, 50, 80, 80, 0.2)]
    det = HybridBudDetector(FakeDet(lambda im: boxes), FakeCls(lambda c: ("grade_one", 0.9)), sam=None,
                            max_box_frac=0.3, max_box_px=100)
    kept = det.detect(img)
    if det.stats["candidates"] != 1 or len(kept) != 1 or kept[0].crop_xyxy is None:
        failures.append(f"max_box_px: 후보 {det.stats}, 남은 {len(kept)}")
    elif not (kept[0].crop_xyxy[0] < 50 and kept[0].crop_xyxy[2] > 80):
        failures.append(f"crop_xyxy 가 박스를 감싸지 않음: {kept[0].crop_xyxy}")

    # 누락 추가 뒤 중복 정리: 검수 봉오리 크롭 안에 같은 등급 추가 봉오리가 있으면 검수 봉오리를 뺌
    def reviewed(grade, crop):
        return BudDetection(grade, 0.5, np.float32([100, 100, 150, 300]), first_class="grade_four", first_conf=0.6,
                            crop_xyxy=np.float32(crop))

    class FakeFinder:
        def __init__(self, found):
            self.found, self.last_notes, self.usage = found, {}, {}

        def find_missed(self, image, plants, buds):
            return list(self.found)

    extra = BudDetection("grade_one", 0.7, np.float32([60, 110, 80, 130]), added=True)
    cases = [("같은 등급, 크롭 안", reviewed("grade_one", [0, 50, 250, 350]), 1),
             ("다른 등급", reviewed("grade_three", [0, 50, 250, 350]), 2),
             ("크롭 밖", reviewed("grade_one", [90, 90, 160, 310]), 2)]
    for name, bud, n_want in cases:
        det = HybridBudDetector(FakeDet(lambda im: []), FakeCls(lambda c: ("grade_one", 0.9)), sam=None,
                                missed=FakeFinder([extra]))
        out = det.add_missed(img, [], [bud])
        if len(out) != n_want or (n_want == 1 and not out[0].added) or det.stats["dup_removed"] != 2 - n_want:
            failures.append(f"중복 정리 {name}: {len(out)}개 (기대 {n_want}), {det.stats}")


def api_key_checks(failures):
    """ANTHROPIC_API_KEY.txt 읽기: 키 한 줄 / KEY=값 / 주석·따옴표 / 빈 파일 / 파일 없음."""
    import os
    import tempfile
    from rose_pipeline.api_key import read_api_key
    cases = {"plain": ("sk-ant-test1\n", "sk-ant-test1"),
             "env_style": ("# 메모\nANTHROPIC_API_KEY=\"sk-ant-test2\"\n", "sk-ant-test2"),
             "bom": ("\ufeff  sk-ant-test3  \r\n", "sk-ant-test3")}
    with tempfile.TemporaryDirectory() as tmp:
        for name, (text, want) in cases.items():
            f = Path(tmp) / f"{name}.txt"
            f.write_text(text, encoding="utf-8")
            got = read_api_key(f)
            if got != want:
                failures.append(f"api_key {name}: {got!r} != {want!r}")
        empty = Path(tmp) / "empty.txt"
        empty.write_text("# 비어 있음\n", encoding="utf-8")
        try:
            read_api_key(empty)
            failures.append("api_key: 빈 파일인데 오류가 나지 않음")
        except ValueError:
            pass
        saved = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            read_api_key(Path(tmp) / "missing.txt")
            failures.append("api_key: 파일·환경 변수가 없는데 오류가 나지 않음")
        except FileNotFoundError:
            pass
        finally:
            if saved is not None:
                os.environ["ANTHROPIC_API_KEY"] = saved


def main():
    failures = []
    api_key_checks(failures)
    embed_checks(failures)
    unit_checks(failures)
    vlm_checks(failures)
    pipeline_checks(failures)
    if failures:
        print("실패:\n  " + "\n  ".join(failures))
        sys.exit(1)
    print("통과: 혼합형 검출기 단위 검사 + VLM 분류기 + 3×4 전체 결과 일치")


if __name__ == "__main__":
    main()
