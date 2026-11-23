"""Claude API 키 읽기.

[API 사용법]
1. 키 발급: https://console.anthropic.com → API Keys → Create Key. `sk-ant-` 로 시작하는 키를 복사.
   (사용량만큼 요금이 나가므로 콘솔의 Billing 에서 결제 수단·한도를 먼저 확인)
2. 키 파일 만들기: 프로젝트 폴더(rose_hybrid/)에 ANTHROPIC_API_KEY.txt 를 만들고 키 한 줄만 넣음.
       sk-ant-api03-xxxxxxxx...
   `ANTHROPIC_API_KEY=sk-ant-...` 형식, 앞뒤 따옴표, '#' 으로 시작하는 주석 줄도 허용.
   파일 위치를 바꾸려면 configs/pipeline.yaml 의 grading.detector.vlm.api_key_file 을 고침 (프로젝트 폴더 기준 경로).
3. Colab: rose_hybrid 폴더(구글 드라이브)에 ANTHROPIC_API_KEY.txt 를 함께 올림. 노트북 0번 설치 셀이 키를 읽어
   '있음' 만 출력함 (키 내용은 출력하지 않음).
4. 키 파일이 없으면 ANTHROPIC_API_KEY 환경 변수를 씀 (예: PowerShell `$env:ANTHROPIC_API_KEY="sk-ant-..."`).
   둘 다 없으면 FileNotFoundError.

[API 를 쓰는 곳] — 모두 anthropic_client() 로 클라이언트를 만듦. 모델·effort 등은 grading.detector.vlm 설정.
- detector.type: hybrid + review: true      → ④ Claude 검수 (vlm.py VlmGradeClassifier)
- detector.type: hybrid + add_missed: true  → ⑤ Claude 누락 추가 (plant_vlm.py VlmMissedFinder)
- detector.type: hybrid + cls_backend: vlm / cascade → ③ 등급 분류를 Claude 로
- detector.type: vlm_plant                  → 화분 단위 VLM (plant_vlm.py VlmPlantGrader)
review·add_missed 를 false 로 하면 (cls_backend: yolo 일 때) API 를 전혀 쓰지 않음 — 키 파일도 필요 없음.
요청 수는 실행 끝 '사용량' 에 출력됨 (11/19 사진 기준 약 20번/실행).

[주의] ANTHROPIC_API_KEY.txt 는 다른 사람에게 공유하거나 깃에 올리지 마세요 (.gitignore 에 들어 있음).
키가 노출되면 콘솔에서 바로 삭제하고 새로 발급하세요.
"""

import os

from .config import resolve

KEY_FILE = "ANTHROPIC_API_KEY.txt"
ENV_NAME = "ANTHROPIC_API_KEY"


def read_api_key(path=KEY_FILE):
    p = resolve(path or KEY_FILE)
    if p.exists():
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(ENV_NAME) and "=" in line:
                line = line.split("=", 1)[1].strip()
            key = line.strip("'\"")
            if key:
                return key
        raise ValueError(f"API 키 파일이 비어 있습니다: {p}\n  파일에 Claude API 키(sk-ant-...) 한 줄을 넣으세요.")
    key = os.environ.get(ENV_NAME, "").strip()
    if key:
        return key
    raise FileNotFoundError(
        f"Claude API 키 파일이 없습니다: {p}\n"
        f"  프로젝트 폴더에 {KEY_FILE} 를 만들고 키(sk-ant-...) 한 줄을 넣으세요.")


def anthropic_client(path=KEY_FILE):
    try:
        import anthropic
    except ImportError as e:
        raise ImportError("anthropic 이 필요합니다: pip install anthropic") from e
    return anthropic.Anthropic(api_key=read_api_key(path))
