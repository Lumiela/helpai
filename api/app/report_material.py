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


def build(*, period_start: float, period_end: float, every: str, fields: List[str], series: Dict[str, list],
          crop, records: Optional[list], records_error: Optional[str]) -> dict:
    """재료 텍스트와 크기 정보. {"text", "rows", "records", "bytes"}"""
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
    text = "\n\n".join([head, desc, "[센서]\n" + table, "[작물]\n" + crop_text, "[자동 제어 기록]\n" + rec_text])
    return {"text": text, "rows": nrows, "records": len(records or []), "bytes": len(text.encode("utf-8"))}
