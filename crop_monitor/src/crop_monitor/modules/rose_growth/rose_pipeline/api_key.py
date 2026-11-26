"""Gemini API 키 읽기 (원본은 Claude 키. 2026-11-24 Gemini 로 바꿈).

키는 crop_monitor.keys 가 읽는다: secrets/GEMINI_API_KEY → 환경 변수 → /data/.env (네이버 서버의 AI 리포트와 같은 키).
무료 등급은 보낸 내용이 구글 제품 개선에 쓰일 수 있다 (유료 등급은 쓰이지 않음 — Gemini API 약관).
"""

from crop_monitor.gemini import GeminiClient, GeminiFatal
from crop_monitor.keys import WHERE, read_key

KEY_FILE = "secrets/GEMINI_API_KEY"


def gemini_client(path=KEY_FILE):
    key = read_key()
    if not key:
        raise GeminiFatal(f"Gemini API 키가 없습니다 → {WHERE} 에 넣으세요")
    return GeminiClient(key)
