"""Second opinion on the stage-2 results from a vision-language model (VLM).

Each selected leaf is shown to the VLM as two views cut from the original photo, without the classifier's answer
(blind review, so the VLM is not anchored on it):
  1. close-up: 1.4 x the leaf box, everything outside the stage-1 outline darkened, outline in cyan, long side 512 px
  2. context: 3 x the leaf box with the outline in cyan, long side 768 px
The VLM returns one of LABELS, a confidence, the symptoms it saw and a short reason (Korean).
verdict() then compares the VLM label with the classifier result.

Backend: GeminiVerifier (Gemini API key: crop_monitor.keys).
"""
import base64
import io
import json
import random
import re
import threading
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

from crop_monitor.gemini import GeminiClient, GeminiError, GeminiFatal, cost_usd, image_part, text_part

LABELS = ["healthy", "black_spot", "downy_mildew", "insects_infected", "not_a_leaf", "unclear"]
CYAN = (0, 255, 255)

SYSTEM = """You review single rose leaves for a plant-health monitor. The photos come from a growth chamber: miniature roses \
seen from above, lit by pink/purple LED grow lights, with reflective silver (mirror-like) walls.

You get two images of the same leaf, outlined in cyan. Image 1 is a close-up in which everything outside the outline \
is darkened. Image 2 shows the outlined leaf inside its surroundings. Judge only the outlined leaf. Answer with exactly one label:

- healthy: uniform green leaf. These are normal and NOT disease: serrated edges, reddish or bronze margins and young \
red-bronze leaves, pale yellow-green new growth, glare or specular highlights, the pink colour cast of the LED light, \
shadows from other leaves.
- black_spot: round dark brown to black spots, often with feathery or fringed edges, sometimes with yellowing around them.
- downy_mildew: angular purplish-red, brown or yellow blotches limited by the leaf veins; grey fuzzy growth on the underside.
- insects_infected: chewed holes, ragged or eaten edges, leaf-miner trails, fine stippling, visible insects, webbing or eggs.
- not_a_leaf: the outlined region is not one real leaf, e.g. a reflection in the mirror wall, a stem, the pot, \
background, or several leaves merged into one outline.
- unclear: the leaf is too small, blurred, dark or hidden to judge.

Pick a disease label only when you can point to the symptom in the image. Write "symptoms" and "reason" in Korean, \
one short sentence each."""

USER = "Image 1: close-up of the outlined leaf. Image 2: the same leaf in its surroundings. Classify the outlined leaf."

SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": LABELS},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "symptoms": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["label", "confidence", "symptoms", "reason"],
    "additionalProperties": False,
}

def _square(box, scale, W, H):
    x0, y0, x1, y1 = box
    cx, cy, s = (x0 + x1) / 2, (y0 + y1) / 2, max(x1 - x0, y1 - y0) * scale / 2
    return int(max(0, cx - s)), int(max(0, cy - s)), int(min(W, cx + s)), int(min(H, cy + s))


def _fit(img, side):
    s = side / max(img.size)
    return img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS if s < 1 else Image.BICUBIC)


def _outlined(rgb, leaf, scale, side, dim):
    """Square crop around the leaf, everything outside its outline darkened to `dim`, outline in cyan, long side `side`."""
    H, W = rgb.shape[:2]
    a, b, c, d = _square(leaf["box"], scale, W, H)
    crop = rgb[b:d, a:c].astype(np.float32)
    poly = np.array(leaf["polygon"], np.float32) - [a, b]
    inside = np.zeros(crop.shape[:2], np.uint8)
    cv2.fillPoly(inside, [poly.astype(np.int32).reshape(-1, 1, 2)], 1)
    crop[inside == 0] *= dim
    img = _fit(Image.fromarray(crop.astype(np.uint8)), side)
    out = np.array(img)
    pts = np.round(poly * (img.width / (c - a))).astype(np.int32).reshape(-1, 1, 2)
    cv2.polylines(out, [pts], True, CYAN, max(2, side // 256))
    return Image.fromarray(out)


def views(rgb, leaf):
    """(close-up, context) PIL images of one leaf; leaf comes from the result JSON (box, polygon in photo pixels)."""
    return _outlined(rgb, leaf, 1.4, 512, 0.35), _outlined(rgb, leaf, 3.0, 768, 1.0)


def _jpeg_b64(img):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def parse_answer(text):
    """First JSON object in text -> dict with the SCHEMA keys (label 'unclear' + error when unusable)."""
    m = re.search(r"\{.*\}", text, re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        d = {}
    if d.get("label") not in LABELS:
        return dict(label="unclear", confidence="low", symptoms="", reason="", error=f"unparsed answer: {text[:200]!r}")
    return dict(label=d["label"], confidence=str(d.get("confidence", "low")), symptoms=str(d.get("symptoms", "")),
                reason=str(d.get("reason", "")))


class GeminiVerifier:
    workers = 4
    fatal = (GeminiFatal,)

    def __init__(self, api_key, model="gemini-4-hydrogen", thinking="low", max_tokens=8000, client=None):
        self.client = client or GeminiClient(api_key)
        self.model, self.thinking, self.max_tokens = model, thinking, max_tokens
        self.tokens = [0, 0]          # 입력, 출력(생각 포함)
        self._lock = threading.Lock()

    def __call__(self, images):
        parts = [text_part(USER)] + [image_part(_jpeg_b64(im)) for im in images]
        try:
            answer, usage = self.client.generate_json(self.model, SYSTEM, parts, SCHEMA, self.thinking, self.max_tokens)
        except GeminiFatal:
            raise
        except GeminiError as e:
            return dict(label="unclear", confidence="low", symptoms="", reason="", error=str(e)[:300])
        with self._lock:
            self.tokens[0] += usage["input_tokens"]
            self.tokens[1] += usage["output_tokens"] + usage["thought_tokens"]
        return parse_answer(json.dumps(answer, ensure_ascii=False))

    def cost(self):
        return cost_usd(self.model, self.tokens[0], self.tokens[1])


def model_label(leaf):
    """The classifier's answer in VLM label terms (None when it gave none: UNKNOWN, HOLD, HOLD_SHAPE)."""
    return leaf["anomaly_class"] if leaf["state"] == "ANOMALY" else "healthy" if leaf["state"] == "FRESH" else None


def verdict(leaf, label):
    """AGREE | FALSE_ALARM | MISSED | OTHER_CLASS | VLM_HEALTHY | VLM_ANOMALY | NOT_A_LEAF | UNCLEAR"""
    if label in ("unclear", "not_a_leaf"):
        return label.upper()
    m = model_label(leaf)
    if m is None:
        return "VLM_HEALTHY" if label == "healthy" else "VLM_ANOMALY"
    if label == m:
        return "AGREE"
    if m == "healthy":
        return "MISSED"       # classifier said healthy, VLM sees a disease
    if label == "healthy":
        return "FALSE_ALARM"  # classifier flagged it, VLM sees a healthy leaf
    return "OTHER_CLASS"


def select(leaves, states=("ANOMALY", "UNKNOWN"), n_fresh=5, max_n=40, seed=0):
    """Leaves in `states` (most suspicious first) + a random sample of n_fresh FRESH leaves as a spot check."""
    flagged = sorted((l for l in leaves if l["state"] in states), key=lambda l: -max(v for k, v in l["probs"].items() if k != "healthy"))
    fresh = [l for l in leaves if l["state"] == "FRESH" and "FRESH" not in states]
    sample = random.Random(seed).sample(fresh, min(n_fresh, len(fresh)))
    return (flagged + sample)[:max_n]


COLORS = {"AGREE": (60, 200, 60), "FALSE_ALARM": (255, 140, 0), "MISSED": (255, 40, 40), "OTHER_CLASS": (255, 220, 0),
          "VLM_HEALTHY": (60, 200, 60), "VLM_ANOMALY": (255, 40, 40), "NOT_A_LEAF": (150, 150, 150), "UNCLEAR": (150, 150, 150)}


def _font(s):
    try:
        return ImageFont.load_default(size=s)
    except TypeError:
        return ImageFont.load_default()


def contact_sheet(tiles, cols=6, side=256):
    """tiles: [(close-up PIL, row dict)] -> one image with model / VLM labels under each leaf (ASCII only)."""
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * side, max(1, rows) * (side + 66)), (20, 20, 20))
    d, F = ImageDraw.Draw(sheet), _font(15)
    for i, (img, r) in enumerate(tiles):
        x, y = (i % cols) * side, (i // cols) * (side + 66)
        sheet.paste(_fit(img, side - 4), (x + 2, y + 2))
        d.text((x + 4, y + side + 2), f"{r['id']}  model: {r['model']}", font=F, fill=(230, 230, 230))
        d.text((x + 4, y + side + 21), f"VLM: {r['vlm_label']} ({r['confidence']})", font=F, fill=(230, 230, 230))
        d.text((x + 4, y + side + 40), r["verdict"], font=F, fill=COLORS[r["verdict"]])
    return sheet
