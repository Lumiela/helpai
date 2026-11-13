# -*- coding: utf-8 -*-
"""
AI 리포트 재료 텍스트 (2026-10-07, 계획서 edgeserver-JS/2026-10-07_AI리포트_젯슨LangGraph_계획서v4_랩탑ClaudeCode.md
§1.2·§5·§15). LLM 에 넣는 하루치 재료를 CSV 텍스트로 만든다.

  지금 JSON 재료는 하루치가 컨텍스트 한도(16384 토큰)를 넘었다(10-07 실측 17,719~27,891 토큰). 같은 값을 CSV 로 쓰면
  약 1/3 로 들어간다 → 사용자 결정(10-07):
    센서 값       CSV(시각별로 한 줄), 시각 = 날짜 + 시각(`10-06 02:10`), 소수 둘째 자리(끝의 0 포함), 빈 값 `-`
    항목 설명     센서 표 위에 항목마다 한 줄(단위·뜻)
    자동 제어 기록 필요한 칸만: 시각·동작·보낸 값·판단 이유·단계·결과. 그때의 센서 값·목표 범위·요청한 값·이메일은 뺀다.
                  사람 조작은 동작 뒤에 "(사람)" 으로만 표시. 묶음 값은 `키=값` 으로 펼치고 판단 이유는 `;` 로 잇는다

구성: 머리말(기간·집계 방식) → 항목 설명 → [센서] → [작물] → [자동 제어 기록]
"""
import csv
import io
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

KST = timezone(timedelta(hours=9))

# 항목 설명(사용자 결정 10-07: 계획서 v4 §5 예시 그대로). 나중에 항목을 더하면 여기에 한 줄 더한다.
FIELD_DESC = {
    "temperature": "온도(°C)",
    "humidity": "습도(%)",
    "lux": "조도(lx)",
    "moisture_pct": "토양 수분(%)",
    "fan1": "배기팬 단계 0·33·66·100",
    "led1": "LED 밝기(%)",
    "pump": "10분 중 급수가 켜져 있던 비율(0~1)",
}

EMPTY = "-"
AUTO_EMAIL = "auto"            # 자동 제어가 남기는 조작 기록의 email 값(audit.py)


def _kst(dt: datetime) -> datetime:
    return dt.astimezone(KST)


def tlabel(dt: datetime) -> str:
    """날짜 + 시각, 한국 시간. 예: 10-06 02:10"""
    return _kst(dt).strftime("%m-%d %H:%M")


def num(v) -> str:
    """소수 둘째 자리, 끝의 0 포함. 숫자가 아니면 빈 값."""
    if isinstance(v, bool):
        return "1.00" if v else "0.00"
    if isinstance(v, (int, float)):
        return "%.2f" % v
    return EMPTY


def _parse(t) -> Optional[datetime]:
    if isinstance(t, datetime):
        return t if t.tzinfo else t.replace(tzinfo=KST)
    if isinstance(t, str) and t:
        try:
            d = datetime.fromisoformat(t.replace("Z", "+00:00"))
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=KST)
    return None


def _rows(text_rows: List[List[str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerows(text_rows)
    return buf.getvalue().rstrip("\n")


def series_csv(series: Dict[str, list], fields: List[str]) -> (str, int):
    """{항목: [[시각, 값], …]} → CSV. 시각은 모든 항목의 합집합(정렬), 없는 칸은 `-`. (텍스트, 행 수)"""
    rows: Dict[datetime, Dict[str, object]] = {}
    for f in fields:
        for pair in series.get(f) or []:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            t = _parse(pair[0])
            if t is None:
                continue
            rows.setdefault(t, {})[f] = pair[1]
    out = [["time"] + list(fields)]
    for t in sorted(rows):
        out.append([tlabel(t)] + [num(rows[t].get(f)) if f in rows[t] else EMPTY for f in fields])
    return _rows(out), len(rows)


def _val(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return "%.2f" % v
    if v is None:
        return EMPTY
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    return str(v)


def kv(d) -> str:
    """{fan1: 66, led1: 50, pump: False} → fan1=66 led1=50 pump=0"""
    if not isinstance(d, dict) or not d:
        return EMPTY
    return " ".join("%s=%s" % (k, _val(v)) for k, v in d.items())


def record_row(r: dict) -> List[str]:
    """조작 기록 한 건 → [time, action, sent, rules, phase, result]."""
    payload = r.get("payload") if isinstance(r.get("payload"), dict) else {}
    at = _parse(r.get("at"))
    action = str(r.get("action") or EMPTY)
    if (r.get("email") or "") != AUTO_EMAIL:
        action += "(사람)"
    if action.startswith("auto_control"):
        sent = kv(payload.get("sent"))
    else:
        # 사람 설정 변경(crop_set·auto_set 등)은 보낸 값 대신 바꾼 값을 같은 칸에 적는다
        sent = kv({k: v for k, v in payload.items() if k not in ("requested", "readings", "ranges", "rules", "phase", "crop")})
    rules = payload.get("rules")
    rules = "; ".join(str(x) for x in rules) if isinstance(rules, list) and rules else EMPTY
    phase = payload.get("phase") or EMPTY
    result = r.get("result")
    return [tlabel(at) if at else EMPTY, action, sent, rules, str(phase), _val(result) if result not in (None, "") else EMPTY]


def records_csv(records: Optional[list]) -> str:
    out = [["time", "action", "sent", "rules", "phase", "result"]]
    for r in records or []:
        if isinstance(r, dict):
            out.append(record_row(r))
    return _rows(out)


# ──────────────────────────────────────────────── 이미지 처리 결과 (2026-11-13)
# /data/leafdx 의 잎 판정·높이 결과가 리포트 기간 안에 있으면 재료에 넣는다(사용자 지시 11-13: 잎 판정 + 높이,
# 사진마다 한 줄). 잎마다의 값(한 장에 500개 안팎)은 컨텍스트 한도를 넘으므로 상태별 개수만 쓴다.
#   LEAFDX_DIR/<iid>/<cube>/<cam>/<YYYY-MM-DD>/<HHMMSS>_<trigger>[-n].json          잎 판정(tools/leafdx_request.py)
#   LEAFDX_DIR/<iid>/<cube>/<cam>/<YYYY-MM-DD>/<HHMMSS>_<trigger>[-n]_height.json   스테레오 높이
# 사진 시각 = 날짜 폴더 + 파일 이름 앞 6자리(한국 시간, camera.store 와 같은 규칙).

IMAGE_DESC = "\n".join([
    "leaves: 판정한 잎 수",
    "anomaly: 병해·이상 의심 잎 수(ANOMALY:* 합)",
    "counts: 상태별 잎 수(FRESH 정상, HOLD_SHAPE 모양 보류, UNKNOWN 판정 불가, ANOMALY:<병해> 이상 의심)",
    "top_cm: 식물 높이(cm, 중앙 영역 상위 지점, 잠정)",
    "median_cm: 식물 높이 중앙값(cm, 잠정)",
])


def _int_counts(leaf) -> Dict[str, int]:
    counts = leaf.get("counts") if isinstance(leaf, dict) else None
    if not isinstance(counts, dict):
        return {}
    return {str(k): v for k, v in counts.items() if isinstance(v, int) and not isinstance(v, bool)}


def _cm(d: dict, key: str) -> str:
    v = d.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return EMPTY
    return "%.2f" % v


def image_row(time_label: str, cam: str, leaf, height) -> List[str]:
    """사진 한 장 → [time, cam, leaves, anomaly, counts, top_cm, median_cm]. (초안: 맥북 9b, 검토·수정: 랩탑 ClaudeCode)"""
    counts = _int_counts(leaf)
    if counts:
        items = sorted(counts.items(), key=lambda kv_: (-kv_[1], kv_[0]))
        leaves = str(sum(counts.values()))
        anomaly = str(sum(v for k, v in counts.items() if k.startswith("ANOMALY:")))
        counts_text = " ".join("%s=%d" % (k, v) for k, v in items)
    else:
        leaves = anomaly = counts_text = EMPTY
    h = height.get("height") if isinstance(height, dict) else None
    if isinstance(h, dict):
        top, median = _cm(h, "top_cm"), _cm(h, "median_cm")
    else:
        top = median = EMPTY
    return [str(time_label), str(cam), leaves, anomaly, counts_text, top, median]


# 트리거 이름에는 밑줄이 없다(camera.TRIGGERS). _vis·_depth 같은 다른 산출물은 고르지 않는다.
_IMG_NAME = re.compile(r"^(\d{6})_[a-z]+(?:-\d+)?(_height)?\.json$")


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:   # noqa: BLE001 — 깨진 파일 하나 때문에 리포트를 멈추지 않는다
        return None


def load_images(root: str, iid: str, cube: str, start_ts: float, end_ts: float) -> List[dict]:
    """기간 [start, end) 안의 사진별 결과. [{"at", "cam", "leaf", "height"}] 시각순. 폴더가 없으면 빈 목록."""
    base = os.path.join(root, iid, cube)
    if not os.path.isdir(base):
        return []
    d0 = datetime.fromtimestamp(start_ts, KST).date()
    d1 = datetime.fromtimestamp(end_ts, KST).date()
    found: Dict[tuple, dict] = {}
    for cam in sorted(os.listdir(base)):
        cam_dir = os.path.join(base, cam)
        if not os.path.isdir(cam_dir):
            continue
        for day in os.listdir(cam_dir):
            try:
                dd = datetime.strptime(day, "%Y-%m-%d").date()
            except ValueError:
                continue
            if dd < d0 or dd > d1:
                continue
            day_dir = os.path.join(cam_dir, day)
            for name in os.listdir(day_dir):
                m = _IMG_NAME.match(name)
                if not m:
                    continue
                at = datetime.strptime(day + m.group(1), "%Y-%m-%d%H%M%S").replace(tzinfo=KST)
                if not (start_ts <= at.timestamp() < end_ts):
                    continue
                stem = name[:-len("_height.json")] if m.group(2) else name[:-len(".json")]
                e = found.setdefault((cam, day, stem), {"at": at, "cam": cam, "leaf": None, "height": None})
                e["height" if m.group(2) else "leaf"] = _read_json(os.path.join(day_dir, name))
    return sorted(found.values(), key=lambda e: (e["at"], e["cam"]))


def images_csv(images: List[dict]) -> str:
    out = [["time", "cam", "leaves", "anomaly", "counts", "top_cm", "median_cm"]]
    for e in images:
        out.append(image_row(tlabel(e["at"]), e["cam"], e.get("leaf"), e.get("height")))
    return _rows(out)


def build(*, period_start: float, period_end: float, every: str, fields: List[str], series: Dict[str, list],
          crop, records: Optional[list], records_error: Optional[str], images: Optional[list] = None) -> dict:
    """재료 텍스트와 크기 정보. {"text", "rows", "records", "images", "bytes"}
    images(load_images 결과)가 있으면 끝에 [이미지 판정] 을 붙인다. 없으면 이전과 같은 재료."""
    head = "기간 %s ~ %s (KST), %s 평균, 시각 = 집계 창 끝" % (
        datetime.fromtimestamp(period_start, KST).strftime("%Y-%m-%d %H:%M"),
        datetime.fromtimestamp(period_end, KST).strftime("%Y-%m-%d %H:%M"), every)
    desc = "\n".join("%s: %s" % (f, FIELD_DESC[f]) if f in FIELD_DESC else f for f in fields)
    table, nrows = series_csv(series, fields)
    crop_text = json.dumps(crop, ensure_ascii=False, separators=(",", ":")) if crop is not None else EMPTY
    if records_error:
        rec_text = "자동 제어 기록을 가져오지 못함: %s" % records_error
    else:
        rec_text = records_csv(records)
    parts = [head, desc, "[센서]\n" + table, "[작물]\n" + crop_text, "[자동 제어 기록]\n" + rec_text]
    if images:
        parts.append("[이미지 판정]\n" + IMAGE_DESC + "\n" + images_csv(images))
    text = "\n\n".join(parts)
    return {"text": text, "rows": nrows, "records": len(records or []), "images": len(images or []),
            "bytes": len(text.encode("utf-8"))}
