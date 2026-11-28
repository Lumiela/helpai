"""화분 단위 VLM 등급 판단 (grading.detector.type: vlm_plant).

YOLO 위치 검출·FastSAM·분류 모델 없이, 1단계에서 나눈 식물마다 Claude(VLM)에게 그 식물 사진을 보여 주고
봉오리·꽃의 위치와 등급을 직접 받는다.

  식물 bbox + 여백으로 잘라냄 → 이웃 식물 마스크 픽셀만 어둡게(dim_outside), 이 식물의 화분은 흰 원으로 표시
  → 긴 변 image_size 로 맞춤
  → Claude: {요약(summary), 봉오리·꽃마다 {등급, 확신, 박스(0~1 비율)}}  (봉오리가 없으면 빈 목록)
  → BudDetection(cell_id = 그 식물) 로 바꿔 돌려줌. 판단 내용(요약, 버린 봉오리 포함 전체 목록, 보낸 사진)은
    last_notes 에 남겨 결과판·vis·vlm/ 폴더에 표시함

어둡게 하는 범위: 꽃·봉오리 색은 식물 색 마스크에 안 들어갈 때가 많아서(연분홍·주황 꽃 등), 마스크 밖을 모두
어둡게 하면 정작 이 식물의 꽃이 가려진다. 그래서 "다른 식물로 배정된 픽셀"만 어둡게 한다.

그 뒤는 기존과 같다: 식물 등급 = 기본 grade_zero, 봉오리 중 최고 등급 (grading.decide_grades)
→ 카메라 합치기 → 표·result.json·result_buds.jpg·vis_*.jpg. 봉오리 배정(assignment)은 VLM 이 이미
식물을 정해서 받으므로 쓰지 않는다.
"""

import json
import threading

import cv2
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .api_key import KEY_FILE, anthropic_client
from .detection import BudDetection
from .vlm import CLASSES, NOT_BUD, encode_jpeg, image_block, load_examples

GRADE_CLASSES = [c for c in CLASSES if c != NOT_BUD]


class VlmPlantGrader:
    """식물별로 Claude 에게 봉오리·꽃 목록을 받아 BudDetection 으로 반환.

    pipeline.RosePipeline.grade_plants 는 검출기에 grade_plants 가 있으면 이것을 쓰고 배정을 건너뜀.
    """

    def __init__(self, client, model="claude-opus-5-5", grade_descriptions=None, examples=None,
                 image_size=1024, example_size=256, crop_padding=0.1, dim_outside=0.35, min_conf=0.5,
                 effort="low", max_tokens=8000, fallbacks="default", max_workers=4):
        self.client, self.model = client, model
        self.descriptions = grade_descriptions or {}
        self.examples = examples or []
        self.image_size, self.example_size = int(image_size), int(example_size)
        self.crop_padding, self.dim_outside, self.min_conf = crop_padding, dim_outside, min_conf
        self.effort, self.max_tokens, self.fallbacks = effort, int(max_tokens), fallbacks
        self.max_workers = max(1, int(max_workers))
        self.usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0,
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        self._prefix = None
        self._lock = threading.Lock()
        self.last_notes = {}  # 마지막 grade_plants 호출의 {칸: 판단 내용}

    @classmethod
    def from_config(cls, vlm=None, crop_padding=0.1, **_):
        """설정의 grading.detector 전체를 받음. VLM 설정은 detector.vlm 에서 씀."""
        v = dict(vlm or {})
        examples = load_examples(v["examples_dir"], v.get("examples_per_class", 2), v.get("seed", 0),
                                 GRADE_CLASSES) if v.get("examples_dir") else []
        keys = ("model", "grade_descriptions", "effort", "max_tokens", "fallbacks", "dim_outside",
                "min_conf", "max_workers")
        kw = {k: v[k] for k in keys if k in v}
        if "plant_image_size" in v:
            kw["image_size"] = v["plant_image_size"]
        if "image_size" in v:
            kw["example_size"] = v["image_size"]
        client = anthropic_client(v.get("api_key_file", KEY_FILE))  # 인증: 프로젝트 폴더의 ANTHROPIC_API_KEY.txt
        return cls(client, examples=examples, crop_padding=crop_padding, **kw)

    # BudDetector 처럼 쓰일 때 (plant 정보 없이 호출되면 판단할 수 없음)
    def detect(self, image):
        raise RuntimeError("vlm_plant 는 식물 단위로 판단합니다. RosePipeline.grade_plants 에서 사용하세요.")

    # ── 프롬프트 ──────────────────────────────────────────────────────
    def system_prompt(self):
        lines = [
            "You grade the flowering stage of one potted miniature rose plant, photographed from above in a "
            "growth chamber.",
            "The plant to grade grows from the pot marked with a thin white circle. Leaves of neighbouring plants are "
            "darkened; ignore buds and flowers in darkened areas and buds that clearly belong to another plant.",
            "Find every flower bud and flower of the circled plant and grade each one. Its buds and flowers may sit at "
            "the end of long stems away from the pot. The grade is the opening "
            "stage - how far the petals have opened - not colour, variety or quality. Higher grades are more open. "
            "A tightly closed bud of any colour is grade_one, and so is a young green bud whose petal colour "
            "barely shows.",
            "The camera looks down from above, so you mostly see buds and flowers from the top: a closed bud is a "
            "tight point or small knot of petals, an opening flower shows a ring of loosened outer petals around a "
            "tight centre, and an open flower is a flat, layered disc.",
            "Grades:",
        ]
        for c in GRADE_CLASSES:
            desc = self.descriptions.get(c)
            lines.append(f"- {c}: {desc}" if desc else f"- {c}")
        lines += [
            "Leaves, new shoots, stems, thorns, soil, pots, the tray, reflectors, cables and hands are not buds. "
            "Yellowed or spotted leaves are not buds either.",
            "If the plant has no buds or flowers, return an empty list. Do not guess buds you cannot see.",
            "In summary, describe in one short plain-English sentence (ASCII only, at most 80 characters) what you "
            "see on this plant, e.g. '2 closed red buds at top right, no open flowers'.",
            "For each bud give a tight bounding box as fractions of the image width and height (0 = left/top, "
            "1 = right/bottom), and confidence as your probability (0 to 1) that the bud exists and the grade is "
            "correct.",
        ]
        return "\n".join(lines)

    def prefix_blocks(self):
        if self._prefix is None:
            blocks = []
            if self.examples:
                blocks.append({"type": "text", "text": "Reference close-up crops of single buds with their grade:"})
                for name, img in self.examples:
                    blocks.append({"type": "text", "text": f"Example - {name}:"})
                    blocks.append(image_block(img, self.example_size))
            else:
                blocks.append({"type": "text", "text": "No reference examples are available."})
            blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
            self._prefix = blocks
        return self._prefix

    def schema(self):
        coord = {"type": "number"}
        return {
            "type": "object",
            "properties": {"summary": {"type": "string"}, "buds": {"type": "array", "items": {
                "type": "object",
                "properties": {"grade": {"type": "string", "enum": GRADE_CLASSES},
                               "confidence": {"type": "number"},
                               "box": {"type": "object",
                                       "properties": {"x1": coord, "y1": coord, "x2": coord, "y2": coord},
                                       "required": ["x1", "y1", "x2", "y2"], "additionalProperties": False}},
                "required": ["grade", "confidence", "box"],
                "additionalProperties": False}}},
            "required": ["summary", "buds"],
            "additionalProperties": False,
        }

    # ── 식물 사진 ──────────────────────────────────────────────────────
    def plant_view(self, image, plant, owner=None, index=None):
        """식물 bbox + 여백 크롭. owner(이미지 크기, 픽셀마다 식물 번호, 없음 = -1)가 있으면 다른 식물(번호 ≠ index)
        픽셀만 어둡게. 이 식물의 화분 구멍에 흰 원. 반환: (크롭, (x1, y1)) 또는 (None, None)."""
        box = plant.bbox()
        if box is None:
            return None, None
        h, w = image.shape[:2]
        x1, y1, x2, y2 = box
        px, py = int((x2 - x1) * self.crop_padding), int((y2 - y1) * self.crop_padding)
        x1, y1, x2, y2 = max(x1 - px, 0), max(y1 - py, 0), min(x2 + px, w), min(y2 + py, h)
        crop = image[y1:y2, x1:x2].copy()
        if owner is not None and self.dim_outside < 1:
            o = owner[y1:y2, x1:x2]
            other = (o >= 0) & (o != index)
            crop[other] = (crop[other] * self.dim_outside).astype(crop.dtype)
        hole = getattr(plant, "hole", None)
        if hole is not None:
            t = max(2, round(max(crop.shape[:2]) / 400))
            c = (int(hole.center[0] - x1), int(hole.center[1] - y1))
            cv2.circle(crop, c, int(hole.radius), (255, 255, 255), t, cv2.LINE_AA)
        return crop, (x1, y1)

    @staticmethod
    def owner_map(shape, plants):
        owner = np.full(shape[:2], -1, np.int16)
        for i, p in enumerate(plants):
            ox, oy = p.offset
            mh, mw = p.mask.shape
            region = owner[oy:oy + mh, ox:ox + mw]
            region[p.mask[:region.shape[0], :region.shape[1]]] = i
        return owner

    # ── 호출 ──────────────────────────────────────────────────────────
    def grade_plants(self, image, plants):
        owner = self.owner_map(image.shape, plants)
        jobs = []
        for i, p in enumerate(plants):
            crop, xy = self.plant_view(image, p, owner, i)
            if crop is not None:
                jobs.append((p, crop, xy))
        if self.max_workers > 1 and len(jobs) > 1:
            with ThreadPoolExecutor(min(self.max_workers, len(jobs))) as ex:
                answers = list(ex.map(lambda j: self.ask(j[1], j[0].cell_id), jobs))
        else:
            answers = [self.ask(c, p.cell_id) for p, c, _ in jobs]

        dets, notes = [], {}
        for (p, crop, (ox, oy)), ans in zip(jobs, answers):
            ch, cw = crop.shape[:2]
            raw = []
            for b in ans.get("buds", []):
                conf = float(min(max(b["confidence"], 0.0), 1.0))
                bx = b["box"]
                fx1, fx2 = sorted((min(max(bx["x1"], 0.0), 1.0), min(max(bx["x2"], 0.0), 1.0)))
                fy1, fy2 = sorted((min(max(bx["y1"], 0.0), 1.0), min(max(bx["y2"], 0.0), 1.0)))
                xyxy = np.float32([ox + fx1 * cw, oy + fy1 * ch, ox + max(fx2 * cw, fx1 * cw + 1),
                                   oy + max(fy2 * ch, fy1 * ch + 1)])
                kept = b["grade"] in GRADE_CLASSES and conf >= self.min_conf
                raw.append({"grade": b["grade"], "conf": conf, "xyxy": xyxy, "kept": kept})
                if kept:
                    dets.append(BudDetection(b["grade"], conf, xyxy, cell_id=p.cell_id))
            notes[p.cell_id] = {"summary": ascii_text(ans.get("summary", "")), "buds": raw,
                                "view": crop, "view_xy": (ox, oy)}
        self.last_notes = notes
        return dets

    def request_text(self, cell_id):
        return f"Plant {cell_id}: list its buds and flowers."

    def ask(self, crop, cell_id=""):
        content = list(self.prefix_blocks())
        content.append({"type": "text", "text": self.request_text(cell_id)})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": encode_jpeg(crop, self.image_size)}})
        kwargs = dict(model=self.model, max_tokens=self.max_tokens, system=self.system_prompt(),
                      messages=[{"role": "user", "content": content}],
                      output_config={"effort": self.effort,
                                     "format": {"type": "json_schema", "schema": self.schema()}})
        if self.fallbacks:  # 안전 분류기가 거절하면 서버가 다른 모델로 다시 실행
            resp = self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                                    fallbacks=self.fallbacks, **kwargs)
        else:
            resp = self.client.messages.create(**kwargs)
        self._count(resp)
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"{cell_id}: VLM 이 요청을 거절했습니다: {getattr(resp, 'stop_details', None)}")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError(f"{cell_id}: VLM 응답이 max_tokens 에서 잘렸습니다. vlm.max_tokens 를 늘리세요.")
        text = next(b.text for b in resp.content if b.type == "text")
        return json.loads(text)

    def _count(self, resp):
        u = getattr(resp, "usage", None)
        with self._lock:  # 여러 스레드에서 호출됨
            self.usage["requests"] += 1
            for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                self.usage[key] += int(getattr(u, key, 0) or 0)


class VlmMissedFinder(VlmPlantGrader):
    """혼합형 Claude 검수의 '누락 추가': 위치 모델이 놓친 봉오리를 식물마다 Claude 가 찾아 추가.

    식물 사진(vlm_plant 와 같은 방식: 이웃 식물 어둡게 + 화분 흰 원)에 이미 찾은 봉오리를 청록색 번호 박스로
    그려 보내고, 박스 밖에 있는 이 식물의 봉오리·꽃만 받음. 이미 찾은 봉오리와 겹치는 답(중심이 박스 안이거나
    IoU ≥ overlap_iou)과 확신 < min_conf 인 답은 버림. 추가된 봉오리는 added=True, cell_id = 그 식물.
    """

    BOX_COLOR = (255, 255, 0)  # BGR 청록색

    def __init__(self, *args, overlap_iou=0.3, **kw):
        super().__init__(*args, **kw)
        self.overlap_iou = overlap_iou

    def system_prompt(self):
        return super().system_prompt() + "\n" + "\n".join([
            "Buds that were already found are outlined with thin cyan numbered boxes. Do not list them again.",
            "List only buds and flowers of the circled plant that are NOT inside a cyan box. If every visible bud is "
            "already boxed, return an empty list.",
            "In summary, say what you added, e.g. 'added 1 closed bud at left' or 'nothing missed'.",
        ])

    def request_text(self, cell_id):
        return f"Plant {cell_id}: list buds and flowers that are missing a cyan box."

    def draw_found(self, crop, xy, found):
        t = max(1, round(max(crop.shape[:2]) / 500))
        for k, d in enumerate(found, 1):
            x1, y1, x2, y2 = (int(round(v)) for v in (d.xyxy[0] - xy[0], d.xyxy[1] - xy[1],
                                                      d.xyxy[2] - xy[0], d.xyxy[3] - xy[1]))
            cv2.rectangle(crop, (x1, y1), (x2, y2), self.BOX_COLOR, t, cv2.LINE_AA)
            cv2.putText(crop, str(k), (x1 + 2, max(y1 - 3, 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.4 * t,
                        self.BOX_COLOR, t, cv2.LINE_AA)
        return crop

    def overlaps(self, xyxy, found):
        cx, cy = (xyxy[0] + xyxy[2]) / 2, (xyxy[1] + xyxy[3]) / 2
        for d in found:
            if d.xyxy[0] <= cx <= d.xyxy[2] and d.xyxy[1] <= cy <= d.xyxy[3]:
                return True
            if box_iou(xyxy, d.xyxy) >= self.overlap_iou:
                return True
        return False

    def find_missed(self, image, plants, found):
        """found: 이미 찾은 봉오리 (카메라 이미지 좌표). 반환: 추가할 BudDetection 목록."""
        owner = self.owner_map(image.shape, plants)
        jobs = []
        for i, p in enumerate(plants):
            crop, xy = self.plant_view(image, p, owner, i)
            if crop is None:
                continue
            ch, cw = crop.shape[:2]
            near = [d for d in found if d.xyxy[2] > xy[0] and d.xyxy[0] < xy[0] + cw
                    and d.xyxy[3] > xy[1] and d.xyxy[1] < xy[1] + ch]
            jobs.append((p, self.draw_found(crop, xy, near), xy))
        if self.max_workers > 1 and len(jobs) > 1:
            with ThreadPoolExecutor(min(self.max_workers, len(jobs))) as ex:
                answers = list(ex.map(lambda j: self.ask(j[1], j[0].cell_id), jobs))
        else:
            answers = [self.ask(c, p.cell_id) for p, c, _ in jobs]

        added, notes = [], {}
        for (p, crop, (ox, oy)), ans in zip(jobs, answers):
            ch, cw = crop.shape[:2]
            raw = []
            for b in ans.get("buds", []):
                conf = float(min(max(b["confidence"], 0.0), 1.0))
                bx = b["box"]
                fx1, fx2 = sorted((min(max(bx["x1"], 0.0), 1.0), min(max(bx["x2"], 0.0), 1.0)))
                fy1, fy2 = sorted((min(max(bx["y1"], 0.0), 1.0), min(max(bx["y2"], 0.0), 1.0)))
                xyxy = np.float32([ox + fx1 * cw, oy + fy1 * ch, ox + max(fx2 * cw, fx1 * cw + 1),
                                   oy + max(fy2 * ch, fy1 * ch + 1)])
                kept = (b["grade"] in GRADE_CLASSES and conf >= self.min_conf
                        and not self.overlaps(xyxy, found + added))
                raw.append({"grade": b["grade"], "conf": conf, "xyxy": xyxy, "kept": kept})
                if kept:
                    added.append(BudDetection(b["grade"], conf, xyxy, cell_id=p.cell_id, added=True))
            notes[p.cell_id] = {"summary": ascii_text(ans.get("summary", "")), "buds": raw,
                                "view": crop, "view_xy": (ox, oy)}
        self.last_notes = notes
        return added


def box_iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return float(inter / union) if union > 0 else 0.0


def ascii_text(text, limit=120):
    """결과 이미지(cv2.putText)는 영문만 그릴 수 있으므로 ASCII 로 정리."""
    text = " ".join(str(text).split())
    text = text.encode("ascii", "replace").decode("ascii")
    return text[:limit]
