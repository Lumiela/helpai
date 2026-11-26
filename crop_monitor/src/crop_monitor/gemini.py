"""Gemini API 호출 (Interactions API, 표준 라이브러리만) — 2026-11-24 Claude 호출을 대신함.

generate_json() 하나만 쓴다: 시스템 지시 + 글·사진 조각 + JSON 스키마 → (파싱한 JSON, 토큰 사용량).
- 요청은 store=false 로 보낸다 (기본값 true 는 구글 서버에 요청을 남김).
- 429·5xx·연결 오류는 잠시 쉬었다 다시 시도한다. 키·권한·모델 이름 오류(400/401/403/404)는 바로 실패(GeminiFatal).
- 키는 헤더로만 보내고 로그·예외 문구에 넣지 않는다.
"""

from __future__ import annotations

import json
import random
import socket
import time
import urllib.error
import urllib.request

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
API_REVISION = "2026-05-20"

# $ / 100만 토큰 (입력, 출력). 출력에는 생각 토큰이 포함됨. 2026-11-24 공식 가격표 기준
PRICE = {
    "gemini-4-hydrogen": (1.50, 7.50),
    "gemini-4-neon": (3.00, 15.00),
    "gemini-4-argon": (2.00, 10.00),      # 2026-11-30 까지. 12-01 부터 4.00 / 20.00
    "gemini-4-carbon": (2.00, 10.00),     # 2026-11-30 까지. 12-01 부터 4.00 / 20.00
    "gemini-3.8-flash": (0.75, 3.75),     # 2026-12-31 까지
    "gemini-3.5-flash-lite": (0.30, 2.50),
}


class GeminiError(RuntimeError):
    """이번 요청만 실패 (시간 초과, 응답 잘림, 형식 오류 등)."""


class GeminiFatal(GeminiError):
    """다시 보내도 같은 결과 (키·권한·모델 이름 등)."""


def text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def image_part(jpeg_b64: str) -> dict:
    return {"type": "image", "data": jpeg_b64, "mime_type": "image/jpeg"}


class GeminiClient:
    def __init__(self, api_key: str, base_url: str = BASE_URL, timeout: float = 120.0, retries: int = 4, backoff_s: float = 2.0):
        if not api_key:
            raise GeminiFatal("Gemini API 키가 비어 있습니다")
        self._key, self.base_url = api_key, base_url.rstrip("/")
        self.timeout, self.retries, self.backoff_s = timeout, int(retries), backoff_s

    def generate_json(self, model: str, system: str, parts: list, schema: dict, thinking: str | None = "low",
                      max_tokens: int = 8000) -> tuple[dict, dict]:
        body = {
            "model": model,
            "system_instruction": system,
            "input": parts,
            "response_format": {"type": "text", "mime_type": "application/json", "schema": schema},
            "generation_config": {"max_output_tokens": int(max_tokens)},
            "store": False,
        }
        if thinking:
            body["generation_config"]["thinking_level"] = thinking
        resp = self._post("/interactions", body)
        usage = self._usage(resp)
        status = resp.get("status")
        if status != "completed":
            hint = " (max_tokens 를 늘리거나 한 번에 보내는 크롭 수를 줄이세요)" if status == "incomplete" else ""
            raise GeminiError(f"Gemini 응답이 끝나지 않았습니다: {status}{hint}")
        text = "".join(c.get("text", "") for s in resp.get("steps", []) if s.get("type") == "model_output"
                       for c in s.get("content", []) if c.get("type") == "text")
        if not text.strip():
            raise GeminiError("Gemini 응답에 내용이 없습니다 (안전 필터로 막혔을 수 있음)")
        try:
            return json.loads(text), usage
        except json.JSONDecodeError as e:
            raise GeminiError(f"Gemini 응답이 JSON 이 아닙니다: {text[:120]!r}") from e

    @staticmethod
    def _usage(resp: dict) -> dict:
        u = resp.get("usage") or {}
        g = lambda k: int(u.get(k) or 0)  # noqa: E731
        return dict(input_tokens=g("total_input_tokens"), output_tokens=g("total_output_tokens"),
                    thought_tokens=g("total_thought_tokens"), cached_tokens=g("total_cached_tokens"))

    def _post(self, path: str, body: dict) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        last = ""
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(self.base_url + path, data=data, method="POST", headers={
                "Content-Type": "application/json", "x-goog-api-key": self._key, "Api-Revision": API_REVISION})
            wait = self.backoff_s * (2 ** attempt) * (0.75 + random.random() / 2)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                msg = self._message(e)
                if e.code in (400, 401, 403, 404):
                    raise GeminiFatal(f"Gemini 요청 거절 (HTTP {e.code}): {msg}") from None
                last = f"HTTP {e.code}: {msg}"
                try:
                    wait = max(wait, min(float(e.headers.get("Retry-After") or 0), 60.0))
                except ValueError:
                    pass
                if e.code != 429 and e.code < 500:
                    raise GeminiError(f"Gemini 요청 실패 ({last})") from None
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
                last = f"{type(e).__name__}: {getattr(e, 'reason', e)}"
            except json.JSONDecodeError:
                last = "응답이 JSON 이 아님"
            if attempt < self.retries:
                time.sleep(wait)
        raise GeminiError(f"Gemini 요청이 {self.retries + 1}번 모두 실패했습니다 ({last})")

    def _message(self, e: urllib.error.HTTPError) -> str:
        try:
            text = e.read().decode("utf-8", "replace")
            d = json.loads(text)
            d = d[0] if isinstance(d, list) and d else d       # 오류를 목록으로 감싸 보내기도 함
            msg = d.get("error", {}).get("message", text)
        except Exception:
            msg = str(e.reason)
        return str(msg).replace(self._key, "***")[:300]


def cost_usd(model: str, input_tokens: int, output_tokens: int, thought_tokens: int = 0) -> float | None:
    p = PRICE.get(model)
    return None if p is None else (input_tokens * p[0] + (output_tokens + thought_tokens) * p[1]) / 1e6
