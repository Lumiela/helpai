"""API 키 읽기. 먼저 찾은 값을 쓴다.

    1. 프로젝트 루트의 secrets/GEMINI_API_KEY (따로 쓰고 싶을 때만. 비어 있으면 건너뜀)
    2. 환경 변수 GEMINI_API_KEY
    3. 서버 환경 파일 /data/.env 의 GEMINI_API_KEY 줄 (다른 줄은 읽어도 쓰지 않음. 경로는 CROP_MONITOR_ENV_FILE 로 바꿀 수 있음)

키 값은 로그·보고서에 출력하지 않는다.
"""

from __future__ import annotations

import os
from pathlib import Path

SECRETS_DIR = Path(__file__).resolve().parents[2] / "secrets"
ENV_FILE = Path(os.environ.get("CROP_MONITOR_ENV_FILE", "/data/.env"))
WHERE = "/data/.env 의 GEMINI_API_KEY 줄 또는 secrets/GEMINI_API_KEY 파일"


def key_file(name: str = "GEMINI_API_KEY") -> Path | None:
    for n in (name, f"{name}.txt"):
        p = SECRETS_DIR / n
        if p.is_file():
            return p
    return None


def _unquote(value: str) -> str:
    """docker compose 의 .env 처럼: 따옴표로 감싸면 그 안, 아니면 ' #' 뒤는 주석."""
    value = value.strip()
    if value[:1] in ("'", '"'):
        end = value.find(value[0], 1)
        return value[1:end] if end > 0 else value[1:]
    return value.split(" #", 1)[0].strip()


def env_file_value(name: str, path: Path | None = None) -> str | None:
    """.env 파일에서 name 줄의 값만 꺼낸다 (여러 번 나오면 마지막 값). 파일이 없거나 읽을 수 없으면 None."""
    try:
        lines = Path(path or ENV_FILE).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return None
    found = None
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        left, sep, right = line.partition("=")
        if sep and left.strip() == name:
            found = _unquote(right)
    return found or None


def read_key(name: str = "GEMINI_API_KEY") -> str | None:
    p = key_file(name)
    if p is not None:
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                line = line.split("=", 1)[1].strip()
            key = line.strip("'\"")
            if key:
                return key
    return os.environ.get(name, "").strip() or env_file_value(name)
