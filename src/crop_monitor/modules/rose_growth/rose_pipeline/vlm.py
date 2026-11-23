"""봉오리 크롭 등급 분류기 (혼합형 ③ 단계).

모두 classify(crops) -> [(클래스 이름, 확신), ...] 를 제공한다. crops 는 BGR 이미지 목록.

YoloGradeClassifier : train_hybrid.py cls 로 학습한 YOLO 분류 모델 (기존)
EmbedGradeClassifier: 학습 없이 사전학습 분류 모델(yolo26m-cls.pt)을 특징 추출기로 쓰고, 등급을 아는 참고 크롭
                      (embed_reference/<클래스>/*.jpg) 중 가장 비슷한 k장의 등급으로 판단 (최근접 이웃)
VlmGradeClassifier  : Claude(VLM)에게 등급 설명 + 등급별 예시 사진을 주고 크롭을 묶어서 판단. 학습 불필요
CascadeClassifier   : YOLO 로 먼저 판단하고, 확신이 낮은 크롭만 VLM 에게 다시 물음 (비용 절약)
"""

import base64
import json
import random
import warnings

import cv2
import numpy as np

from .api_key import KEY_FILE, anthropic_client
from .config import resolve

NOT_BUD = "not_bud"
CLASSES = [NOT_BUD, "grade_one", "grade_two", "grade_three", "grade_four"]
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


class YoloGradeClassifier:
    """ultralytics YOLO 분류 모델을 classify() 인터페이스로 감쌈."""

    def __init__(self, model, imgsz=224, device=None, batch=32):
        self.model, self.imgsz, self.device, self.batch = model, imgsz, device, int(batch)

    def classify(self, crops):
        out = []
        for i in range(0, len(crops), self.batch):  # 한 번에 너무 많이 넣으면 메모리 부족 (eval 의 수천 장)
            for r in self.model.predict(list(crops[i:i + self.batch]), imgsz=self.imgsz, device=self.device,
                                        verbose=False):
                out.append((r.names[int(r.probs.top1)], float(r.probs.top1conf)))
        return out


def _numpy(t):
    """torch 텐서 / 배열 → numpy (float32, 1차원)."""
    for name in ("detach", "cpu", "numpy"):
        if hasattr(t, name):
            t = getattr(t, name)()
    return np.asarray(t, np.float32).ravel()


class EmbedGradeClassifier:
    """학습 없이 등급 판단: 사전학습 분류 모델의 특징(임베딩) + 참고 크롭 최근접 이웃.

    - model: ultralytics YOLO 분류 모델 (yolo26m-cls.pt, ImageNet 사전학습). model.embed() 로 크롭의 특징 벡터를 얻음
      (ImageNet 클래스 이름은 쓰지 않음)
    - 참고 크롭: reference_dir/<클래스>/*.jpg (tools/make_vlm_examples.py --out cls_reference 로 학습 데이터에서 복사).
      클래스마다 per_class 장의 특징을 처음 한 번 계산
    - 판단: 코사인 유사도가 가장 높은 k장의 등급을 유사도로 가중 투표. 확신 = 이긴 등급의 득표 비율
    """

    def __init__(self, model, reference_dir, per_class=200, k=15, imgsz=224, device=None, seed=0,
                 classes=CLASSES, batch=32):
        self.model, self.k, self.imgsz, self.device, self.batch = model, int(k), imgsz, device, int(batch)
        refs = load_examples(reference_dir, per_class, seed, classes)
        if not refs:
            raise FileNotFoundError(
                f"참고 크롭이 없습니다: {resolve(reference_dir)}\n"
                "  python tools/make_vlm_examples.py --data hybrid_data --out cls_reference --per-class 200 --min-side 16")
        self.labels = np.array([c for c, _ in refs])
        self.ref = self.embed([img for _, img in refs])

    def embed(self, crops):
        vecs = []
        for i in range(0, len(crops), self.batch):
            out = self.model.embed(list(crops[i:i + self.batch]), imgsz=self.imgsz, device=self.device, verbose=False)
            vecs += [_numpy(t) for t in out]
        v = np.stack(vecs)
        return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-8)

    def classify(self, crops):
        if not crops:
            return []
        sims = self.embed(crops) @ self.ref.T
        k = min(self.k, sims.shape[1])
        out = []
        for row in sims:
            votes = {}
            for j in np.argsort(-row)[:k]:
                votes[self.labels[j]] = votes.get(self.labels[j], 0.0) + max(float(row[j]), 0.0)
            name = max(votes, key=votes.get)
            out.append((str(name), votes[name] / (sum(votes.values()) + 1e-8)))
        return out


def encode_jpeg(image, size):
    """긴 변을 size 로 맞춰(작은 크롭은 확대) JPEG base64 로."""
    h, w = image.shape[:2]
    s = size / max(h, w)
    if abs(s - 1) > 0.01:
        image = cv2.resize(image, (max(1, round(w * s)), max(1, round(h * s))),
                           interpolation=cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise ValueError("크롭을 JPEG 로 인코딩하지 못했습니다")
    return base64.standard_b64encode(buf.tobytes()).decode("ascii")


def image_block(image, size):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": encode_jpeg(image, size)}}


def imread(path):
    data = np.fromfile(str(path), np.uint8)  # 한글 경로 대응
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


class VlmGradeClassifier:
    """Claude 로 봉오리 크롭 등급 판단.

    - 한 요청에 크롭을 batch_size 장씩 묶어 보내고, 크롭 번호별 {grade, confidence} JSON 으로 받음
      (structured outputs 로 형식 고정)
    - 등급 설명(grade_descriptions)과 예시 사진(examples_dir/<클래스>/*.jpg)은 매 요청 앞부분에 같은 내용으로
      들어가므로 프롬프트 캐시로 재사용됨
    - confidence 는 모델이 스스로 매긴 값이라 YOLO 확률처럼 보정된 값이 아님 → cls_conf 를 따로 맞춰야 함
    """

    def __init__(self, client, model="claude-opus-5-5", grade_descriptions=None, examples=None,
                 batch_size=8, image_size=256, effort="low", max_tokens=8000, fallbacks="default",
                 classes=CLASSES, **_):  # **_: vlm_plant 전용 설정(plant_image_size 등)은 무시
        self.client, self.model = client, model
        self.classes = list(classes)
        self.descriptions = grade_descriptions or {}
        self.examples = examples or []  # [(클래스, BGR 이미지)]
        self.batch_size, self.image_size = int(batch_size), int(image_size)
        self.effort, self.max_tokens, self.fallbacks = effort, int(max_tokens), fallbacks
        self.usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0,
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        self._prefix = None

    @classmethod
    def from_config(cls, model="claude-opus-5-5", examples_dir=None, examples_per_class=2, seed=0,
                    api_key_file=KEY_FILE, **cfg):
        client = anthropic_client(api_key_file)  # 인증: 프로젝트 폴더의 ANTHROPIC_API_KEY.txt
        examples = load_examples(examples_dir, examples_per_class, seed) if examples_dir else []
        return cls(client, model=model, examples=examples, **cfg)

    # ── 프롬프트 ──────────────────────────────────────────────────────
    def system_prompt(self):
        lines = [
            "You grade flower buds of potted miniature roses photographed from above in a growth chamber.",
            "Each image you grade is a square crop centred on a candidate bud found by an object detector. "
            "The detector is tuned to over-detect, so many crops are not buds at all.",
            "The grade is the flower's opening stage - how far the petals have opened - not its colour, "
            "variety or quality. Higher grades are more open. A bud of any colour that is still tightly closed "
            "is grade_one, and so is a young green bud whose petal colour barely shows.",
            "The camera looks down from above, so you mostly see buds and flowers from the top: a closed bud is a "
            "tight point or small knot of petals, an opening flower shows a ring of loosened outer petals around "
            "a tight centre, and an open flower is a flat, layered disc.",
            "Classify each crop into exactly one class:",
        ]
        for c in self.classes:
            desc = self.descriptions.get(c)
            lines.append(f"- {c}: {desc}" if desc else f"- {c}")
        lines += [
            "Judge only the object at the centre of the crop; neighbouring buds or leaves at the edges do not count.",
            "Crops are small and upscaled, so they may be blurry. Pick the most likely class and lower the "
            "confidence when unsure.",
            "confidence is your probability (0 to 1) that the chosen class is correct.",
        ]
        return "\n".join(lines)

    def prefix_blocks(self):
        """요청마다 같은 앞부분 (예시 사진). 마지막 블록에 캐시 표시."""
        if self._prefix is None:
            blocks = []
            if self.examples:
                blocks.append({"type": "text", "text": "Reference examples with their correct class:"})
                for name, img in self.examples:
                    blocks.append({"type": "text", "text": f"Example - {name}:"})
                    blocks.append(image_block(img, self.image_size))
            else:
                blocks.append({"type": "text", "text": "No reference examples are available."})
            blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
            self._prefix = blocks
        return self._prefix

    def schema(self):
        return {
            "type": "object",
            "properties": {"results": {"type": "array", "items": {
                "type": "object",
                "properties": {"crop": {"type": "integer"},
                               "grade": {"type": "string", "enum": self.classes},
                               "confidence": {"type": "number"}},
                "required": ["crop", "grade", "confidence"],
                "additionalProperties": False}}},
            "required": ["results"],
            "additionalProperties": False,
        }

    # ── 호출 ──────────────────────────────────────────────────────────
    def classify(self, crops):
        out = []
        for i in range(0, len(crops), self.batch_size):
            out += self._classify_batch(crops[i:i + self.batch_size])
        return out

    def _classify_batch(self, crops):
        content = list(self.prefix_blocks())
        content.append({"type": "text", "text": f"Now grade these {len(crops)} crops."})
        for k, c in enumerate(crops, 1):
            content.append({"type": "text", "text": f"Crop {k}:"})
            content.append(image_block(c, self.image_size))
        content.append({"type": "text", "text": f"Return one result per crop, crop numbers 1 to {len(crops)}."})

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
            raise RuntimeError(f"VLM 이 요청을 거절했습니다: {getattr(resp, 'stop_details', None)}")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("VLM 응답이 max_tokens 에서 잘렸습니다. vlm.max_tokens 를 늘리거나 batch_size 를 줄이세요.")
        text = next(b.text for b in resp.content if b.type == "text")
        by_crop = {}
        for r in json.loads(text)["results"]:
            if 1 <= r["crop"] <= len(crops) and r["crop"] not in by_crop:
                by_crop[r["crop"]] = (r["grade"], float(min(max(r["confidence"], 0.0), 1.0)))
        missing = [k for k in range(1, len(crops) + 1) if k not in by_crop]
        if missing:
            warnings.warn(f"VLM 응답에 크롭 {missing} 결과가 없어 not_bud 로 처리합니다")
        return [by_crop.get(k, (NOT_BUD, 0.0)) for k in range(1, len(crops) + 1)]

    def _count(self, resp):
        self.usage["requests"] += 1
        u = getattr(resp, "usage", None)
        for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            self.usage[key] += int(getattr(u, key, 0) or 0)


class CascadeClassifier:
    """먼저 YOLO 분류, 확신이 below 미만인 크롭만 VLM 결과로 바꿈."""

    def __init__(self, first, second, below=0.8):
        self.first, self.second, self.below = first, second, below

    def classify(self, crops):
        out = list(self.first.classify(crops))
        redo = [i for i, (_, p) in enumerate(out) if p < self.below]
        if redo:
            for i, r in zip(redo, self.second.classify([crops[i] for i in redo])):
                out[i] = r
        return out


def load_examples(examples_dir, per_class=2, seed=0, classes=CLASSES):
    """examples_dir/<클래스>/*.jpg 에서 클래스마다 per_class 장. 폴더가 없는 클래스는 건너뜀."""
    root = resolve(examples_dir)
    if not root.exists():
        warnings.warn(f"VLM 예시 폴더가 없어 예시 없이 판단합니다: {root}")
        return []
    rng = random.Random(seed)
    out = []
    for c in classes:
        files = sorted(p for p in (root / c).glob("*") if p.suffix.lower() in IMG_EXT) if (root / c).is_dir() else []
        for p in rng.sample(files, min(per_class, len(files))):
            img = imread(p)
            if img is not None:
                out.append((c, img))
    return out


def build_classifier(cls_backend, cls_model=None, vlm_cfg=None, imgsz=224, device=None, cascade_below=0.8,
                     embed_cfg=None):
    """설정의 cls_backend(yolo / embed / vlm / cascade)에 맞는 분류기."""
    if cls_backend == "yolo":
        return YoloGradeClassifier(cls_model, imgsz, device)
    if cls_backend == "embed":
        return EmbedGradeClassifier(cls_model, imgsz=imgsz, device=device, **(embed_cfg or {}))
    vlm = VlmGradeClassifier.from_config(**(vlm_cfg or {}))
    if cls_backend == "vlm":
        return vlm
    if cls_backend == "cascade":
        return CascadeClassifier(YoloGradeClassifier(cls_model, imgsz, device), vlm, cascade_below)
    raise ValueError(f"알 수 없는 cls_backend: {cls_backend} (가능: yolo, embed, vlm, cascade)")
