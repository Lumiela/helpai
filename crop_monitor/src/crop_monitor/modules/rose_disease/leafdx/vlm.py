"""Second opinion on the stage-2 results from a vision-language model (VLM).

Each selected leaf is shown to the VLM as two views cut from the original photo, without the classifier's answer
(blind review, so the VLM is not anchored on it):
  1. close-up: 1.4 x the leaf box, everything outside the stage-1 outline darkened, outline in cyan, long side 512 px
  2. context: 3 x the leaf box with the outline in cyan, long side 768 px
The VLM returns one of LABELS, a confidence, the symptoms it saw and a short reason (Korean).
verdict() then compares the VLM label with the classifier result.

Backends: ClaudeVerifier (Anthropic API, needs ANTHROPIC_API_KEY) and QwenVerifier (open model on the local GPU, transformers).
"""
from . import _compat  # noqa: F401
import base64
import io
import json
import random
import re
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

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

JSON_HINT = ('\n\nReply with only one JSON object, no other text: {"label": "<one of ' + ", ".join(LABELS) +
             '>", "confidence": "<low|medium|high>", "symptoms": "<Korean>", "reason": "<Korean>"}')


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


class ClaudeVerifier:
    PRICE = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}  # $ / 1M tokens
    workers = 4

    def __init__(self, model="claude-opus-5-5", effort="low", api_key=None):
        import anthropic
        self.anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model, self.effort = model, effort
        self.fatal = (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError)
        self.tokens = [0, 0]

    def __call__(self, images):
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _jpeg_b64(im)}} for im in images]
        r = self.client.beta.messages.create(
            model=self.model,
            max_tokens=8000,
            system=SYSTEM,
            messages=[{"role": "user", "content": content + [{"type": "text", "text": USER}]}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # a declined request is re-run on a fallback model inside the same call
        )
        self.tokens[0] += r.usage.input_tokens; self.tokens[1] += r.usage.output_tokens
        if r.stop_reason == "refusal":
            cat = getattr(getattr(r, "stop_details", None), "category", None)
            return dict(label="unclear", confidence="low", symptoms="", reason="", error=f"refusal ({cat})")
        if r.stop_reason == "max_tokens":
            return dict(label="unclear", confidence="low", symptoms="", reason="", error="max_tokens")
        return parse_answer(next(b.text for b in r.content if b.type == "text"))

    def cost(self):
        p = self.PRICE.get(self.model)
        return None if p is None else (self.tokens[0] * p[0] + self.tokens[1] * p[1]) / 1e6


class QwenVerifier:
    workers = 1

    def __init__(self, model="Qwen/Qwen2.5-VL-3B-Instruct", max_new_tokens=300):
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor
        self.torch = torch
        self.proc = AutoProcessor.from_pretrained(model)
        dtype = torch.float16 if torch.cuda.is_available() else torch.float32
        self.model = AutoModelForImageTextToText.from_pretrained(model, dtype=dtype, device_map="auto").eval()
        self.max_new_tokens, self.fatal, self.tokens = max_new_tokens, (), [0, 0]

    def __call__(self, images):
        msgs = [{"role": "system", "content": [{"type": "text", "text": SYSTEM + JSON_HINT}]},
                {"role": "user", "content": [{"type": "image", "image": im} for im in images] + [{"type": "text", "text": USER}]}]
        inp = self.proc.apply_chat_template(msgs, add_generation_prompt=True, tokenize=True, return_dict=True,
                                            return_tensors="pt").to(self.model.device)
        with self.torch.no_grad():
            out = self.model.generate(**inp, max_new_tokens=self.max_new_tokens, do_sample=False)
        n = inp["input_ids"].shape[1]
        self.tokens[0] += n; self.tokens[1] += out.shape[1] - n
        return parse_answer(self.proc.batch_decode(out[:, n:], skip_special_tokens=True)[0])

    def cost(self):
        return None


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
