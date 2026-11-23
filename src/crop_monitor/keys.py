"""API 키 읽기 — 프로젝트 루트의 secrets/ 폴더.

    secrets/ANTHROPIC_API_KEY      (메모장으로 저장해 .txt 가 붙어도 됨)

파일 내용은 키 한 줄. `ANTHROPIC_API_KEY=sk-ant-...` 형식, 앞뒤 따옴표, '#' 주석 줄도 허용.
파일이 없으면 같은 이름의 환경 변수를 씀. 키 값은 로그·보고서에 출력하지 않는다.
"""

from __future__ import annotations

import os
from pathlib import Path

SECRETS_DIR = Path(__file__).resolve().parents[2] / "secrets"


def key_file(name: str = "ANTHROPIC_API_KEY") -> Path | None:
    for n in (name, f"{name}.txt"):
        p = SECRETS_DIR / n
        if p.is_file():
            return p
    return None


def read_key(name: str = "ANTHROPIC_API_KEY") -> str | None:
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
    return os.environ.get(name, "").strip() or None
