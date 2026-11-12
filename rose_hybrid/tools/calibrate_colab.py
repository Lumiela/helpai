"""Colab/Jupyter 용 구멍 보정 도구 (노트북 셀 안에서 클릭).

    from calibrate_colab import calibrate
    calibrate("CL")

R1C1 → R1C2 → ... 순서로 구멍 중심을 클릭하고 [저장] 을 누르면 configs/holes.yaml 에 저장된다.
Colab 이 아니면 저장 대신 좌표 YAML 을 화면에 보여준다 (holes.yaml 에 붙여넣기).
"""

import base64
import json
import sys
import uuid
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rose_pipeline.config import load_config, load_yaml, resolve, save_yaml  # noqa: E402
from rose_pipeline.layout import HOLES_HEADER, all_cells  # noqa: E402
from rose_pipeline.sources import build_source  # noqa: E402

_CONFIG = {"path": "configs/pipeline.yaml"}


def _save_holes(camera, points_json):
    from IPython.display import JSON

    cfg = load_config(_CONFIG["path"])
    order = all_cells(cfg["layout"])
    pts = json.loads(points_json)
    path = resolve(cfg["holes_file"])
    holes = load_yaml(path) if path.exists() else {}
    holes[camera] = {cid: [round(pts[cid][0], 4), round(pts[cid][1], 4)] for cid in order if cid in pts}
    save_yaml(holes, path, HOLES_HEADER)
    return JSON({"saved": len(holes[camera]), "path": str(path)})


def calibrate(camera, config="configs/pipeline.yaml", width=960, pad=90):
    from IPython.display import HTML, display

    _CONFIG["path"] = config
    cfg = load_config(config)
    image = build_source(cfg["cameras"][camera]["source"]).read()
    h, w = image.shape[:2]
    scale = width / w
    small = cv2.resize(image, (width, round(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 85])
    data_url = "data:image/jpeg;base64," + base64.b64encode(buf).decode()

    in_colab = False
    try:
        from google.colab import output
        output.register_callback("rose.save_holes", _save_holes)
        in_colab = True
    except ImportError:
        pass

    params = {
        "uid": "cal" + uuid.uuid4().hex[:8], "camera": camera, "img": data_url,
        "w": small.shape[1], "h": small.shape[0], "pad": pad,
        "cells": all_cells(cfg["layout"]), "initial": cfg["holes"].get(camera) or {},
        "radiusRatio": cfg.get("radius_ratio", 0.3), "inColab": in_colab,
    }
    display(HTML(_TEMPLATE.replace("__PARAMS__", json.dumps(params)).replace("__UID__", params["uid"])))


_TEMPLATE = r"""
<div id="__UID__" style="font-family:sans-serif">
  <div class="msg" style="font-size:15px;margin:4px 0;font-weight:bold"></div>
  <div style="margin:4px 0">
    <button class="skip">건너뛰기 (안 보이는 구멍)</button>
    <button class="undo">되돌리기</button>
    <button class="reset">전체 다시</button>
    <button class="save" style="font-weight:bold">저장</button>
    <span class="status" style="margin-left:8px;color:#0a7"></span>
  </div>
  <canvas style="background:#404040;cursor:crosshair;max-width:100%"></canvas>
  <pre class="out" style="display:none;background:#f4f4f4;padding:6px"></pre>
</div>
<script>
(() => {
  const P = __PARAMS__;
  const root = document.getElementById(P.uid);
  const cv = root.querySelector("canvas"), ctx = cv.getContext("2d");
  const msg = root.querySelector(".msg"), status = root.querySelector(".status"), out = root.querySelector(".out");
  cv.width = P.w + 2 * P.pad; cv.height = P.h + 2 * P.pad;
  const img = new Image();
  // 정규화 좌표 <-> 캔버스 좌표
  const toCanvas = ([x, y]) => [x * P.w + P.pad, y * P.h + P.pad];
  const toNorm = ([x, y]) => [(x - P.pad) / P.w, (y - P.pad) / P.h];
  let points = {}, skipped = new Set(), history = [], drag = null;
  for (const [k, v] of Object.entries(P.initial)) points[k] = toCanvas(v);

  const current = () => P.cells.find(c => !(c in points) && !skipped.has(c));

  function draw() {
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.drawImage(img, P.pad, P.pad, P.w, P.h);
    const keys = Object.keys(points);
    for (const k of keys) {
      const [x, y] = points[k];
      let nn = Infinity;
      for (const o of keys) if (o !== k) nn = Math.min(nn, Math.hypot(x - points[o][0], y - points[o][1]));
      const r = isFinite(nn) ? nn * P.radiusRatio : 30;
      ctx.lineWidth = 2; ctx.strokeStyle = "yellow";
      ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.stroke();
      ctx.fillStyle = "red"; ctx.beginPath(); ctx.arc(x, y, 4, 0, 2 * Math.PI); ctx.fill();
      ctx.font = "bold 15px Arial"; ctx.lineWidth = 4; ctx.strokeStyle = "black"; ctx.fillStyle = "yellow";
      ctx.strokeText(k, x - 20, y - r - 6); ctx.fillText(k, x - 20, y - r - 6);
    }
    const cur = current();
    const sk = P.cells.filter(c => skipped.has(c));
    msg.textContent = `[${P.camera}] ` + (cur ? `${cur} 구멍 중심을 클릭하세요 (회색 여백 = 사진 밖)` :
      "모든 칸 완료 · 점을 드래그해 조정 · [저장]") + (sk.length ? `   (건너뜀: ${sk.join(", ")})` : "");
  }

  function pos(e) {
    const r = cv.getBoundingClientRect();
    return [(e.clientX - r.left) * cv.width / r.width, (e.clientY - r.top) * cv.height / r.height];
  }
  cv.addEventListener("mousedown", e => {
    const [x, y] = pos(e);
    for (const [k, [px, py]] of Object.entries(points)) {
      if (Math.hypot(px - x, py - y) < 12) { history.push(["set", k, points[k]]); drag = k; return; }
    }
    const cur = current();
    if (cur) { history.push(["set", cur, null]); points[cur] = [x, y]; draw(); }
  });
  cv.addEventListener("mousemove", e => { if (drag) { points[drag] = pos(e); draw(); } });
  window.addEventListener("mouseup", () => { drag = null; });

  root.querySelector(".skip").onclick = () => { const c = current(); if (c) { history.push(["skip", c, null]); skipped.add(c); draw(); } };
  root.querySelector(".undo").onclick = () => {
    const h = history.pop(); if (!h) return;
    const [kind, k, prev] = h;
    if (kind === "skip") skipped.delete(k); else if (prev === null) delete points[k]; else points[k] = prev;
    draw();
  };
  root.querySelector(".reset").onclick = () => { points = {}; skipped = new Set(); history = []; draw(); };
  root.querySelector(".save").onclick = async () => {
    const norm = {};
    for (const c of P.cells) if (c in points) norm[c] = toNorm(points[c]);
    if (P.inColab) {
      status.textContent = "저장 중...";
      try {
        const res = await google.colab.kernel.invokeFunction("rose.save_holes", [P.camera, JSON.stringify(norm)], {});
        const d = res.data["application/json"];
        status.textContent = `저장 완료: ${d.saved}개 구멍 → ${d.path}`;
      } catch (err) { status.style.color = "#c00"; status.textContent = "저장 실패: " + err; }
    } else {
      out.style.display = "block";
      out.textContent = `${P.camera}:\n` + Object.entries(norm).map(([k, [x, y]]) =>
        `  ${k}: [${x.toFixed(4)}, ${y.toFixed(4)}]`).join("\n");
      status.textContent = "Colab 이 아니라 자동 저장할 수 없습니다. 아래 내용을 configs/holes.yaml 에 붙여넣으세요.";
    }
  };
  img.onload = draw;
  img.src = P.img;
})();
</script>
"""
