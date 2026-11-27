# helpai — decision_model

**서버 InfluxDB 의 센서 이력으로 결정 모델 Laya 를 학습시키고, 현장 젯슨에서 추론해 큐브 제어를 돕는** 부분입니다.

서버 InfluxDB(네이버 클라우드 `smartcubens.duckdns.org/influx`)의 센서 이력 → 판정 문장 → **Laya**(Jev 호환 오픈 결정 모델) 미세조정·보정 →
ONNX 묶음 → 젯슨 Xavier NX 에서 **추론만** 하는 `laya-edge` → 규칙 엔진(rules v2) 옆의 두 번째 판단 → 서버 api 로 큐브 제어.

- 학습: 집 PC **RTX 5060 Ti 16 GB** (WSL2 Ubuntu, PyTorch cu128)
- 추론: 젯슨 **Xavier NX 8 GB** (JetPack 5, ONNX INT8 · CPU)
- 서버(`backend` 브랜치: api·InfluxDB·nginx) 수정 0 건

### 다른 브랜치와의 관계 ([Lumiela/helpai](https://github.com/Lumiela/helpai))

| 브랜치 | 관계 |
|---|---|
| `backend` | 데이터 출처. InfluxDB 저장 형식(`api/app/writer.py`: measurement `mqtt`, tag `cube`·`kind`)과 조회 창구(`nginx/default.conf` 의 `/influx/`), 제어 라우트(`/i/{iid}/cubes/{sn}/control`)를 그대로 쓴다 |
| `Hardware` | 제어 대상 ESP32 펌웨어(CUBE001 = 센서·펌프·팬·조명 led1 — 조명은 CUBE002 에서 CUBE001 로 다시 옮김). 펌웨어 안전장치(펌프 30 s 정지)는 이 과제와 별개로 항상 동작 |
| `langgraph` · `Rose_Disease` · `Rose_Growth` · `Height` · `Crop_image_processing` | 비전·리포트 쪽. 나중에 잎 병해·생장 단계 결과를 state 에 더할 수 있다(지금은 센서만) |

## 문서

| | 문서 | 내용 |
|---|---|---|
| 개요 | [docs/00_프로젝트_개요.md](docs/00_프로젝트_개요.md) | 목적·용어·범위·환경·진행 상태·위험 |
| 구조 | [docs/01_결정모델_InfluxDB_제어_아키텍처.md](docs/01_결정모델_InfluxDB_제어_아키텍처.md) | 전체 구조·배치·묶음·카나리·PolicyGate·장애·도입 단계 |
| 흐름 | [docs/02_데이터_흐름.md](docs/02_데이터_흐름.md) | 운영(추론) 흐름·학습 흐름·파일 형식·교체 시퀀스 |
| 방법 | [docs/03_학습_방법.md](docs/03_학습_방법.md) | 데이터 전략·교사 라벨·미세조정·INT8·보정·합격 기준 |
| 명세 | [docs/04_질문_특징_라벨_명세.md](docs/04_질문_특징_라벨_명세.md) | state 문장 규칙·질문 5 개·교사 규칙표 (autoctl 기준) |
| 현재 제어 | [docs/05_현재_제어_방식.md](docs/05_현재_제어_방식.md) | 서버 api autoctl·사람 제어·송신 경로·펌웨어 안전장치·결정 모델을 붙이는 단계 |
| **이어 가기** | [docs/06_작업_지침과_방향.md](docs/06_작업_지침과_방향.md) | **다른 환경에서 시작할 때 먼저** — 현재 상태·진행 단계·작업 규칙·열린 질문·결정 기록 |
| **이어 가기** | [docs/06_작업_지침과_방향.md](docs/06_작업_지침과_방향.md) | **다른 환경에서 시작할 때 먼저** — 현재 상태·진행 단계·작업 규칙·열린 질문·결정 기록 |
| 매뉴얼 | [manual/01_집PC_학습환경_설정.md](manual/01_집PC_학습환경_설정.md) | 드라이버·WSL·cu128·laya·Node·check-env |
| | [manual/02_학습_실행_매뉴얼.md](manual/02_학습_실행_매뉴얼.md) | Influx 받기 → 데이터셋 → 학습 → ONNX → 평가 → 묶음 |
| | [manual/03_젯슨_배포_매뉴얼.md](manual/03_젯슨_배포_매뉴얼.md) | laya-edge 설치·묶음 올리기·실측·교체·되돌리기 |
| | [manual/04_문제해결.md](manual/04_문제해결.md) | 증상별 해결 |

## 빠른 시작 (집 PC, 환경 설정 후)

```bash
git clone -b decision_model https://github.com/Lumiela/helpai.git helpai-decision && cd helpai-decision
source .venv/bin/activate                         # 매뉴얼 01 로 만든 가상환경
python -m helpai_decide check-env                 # 결과: 통과
REV=2026-12-05_r1 bash scripts/pipeline.sh        # → bundles/2026-12-05_r1.tar, reports/2026-12-05_r1/
```

## 폴더

```
helpai_decide/     학습·추론 공용 파이썬 패키지 (핵심은 pyyaml 만)   python -m helpai_decide --help
configs/           questions.yaml(질문) · dataset.yaml · training.yaml · replay.yaml(구역 매핑)
profiles/          crops.json (서버 api 작물표 복사본 — autoctl 과 같은 값)
edge/              laya-edge 추론 서버 (Node 20, @receptron/laya 0.2.0) · Dockerfile · 젯슨 전용 docker-compose.yml
scripts/           pipeline.sh (집 PC 전 과정)
tests/             시험 29 개 — python -m unittest discover -s tests -t .
docs/ manual/      문서
reports/<rev>/     회차별 작은 결과(JSON·YAML) — git 으로 공유
data/ models/ runs/ bundles/   큰 파일 — git 에 넣지 않는다(.gitignore). 묶음은 scp/USB
```

## 지켜야 할 것

- **토큰·키를 저장소에 넣지 않는다**: `INFLUX_READ_TOKEN`, `LAYA_API_KEY`, HF 토큰은 환경변수로만. `.env` 는 무시 목록에 있다.
- 질문(`configs/questions.yaml`)·문장(`helpai_decide/features.py`)·교사(`labeler.py`)를 바꾸면 **데이터셋부터 다시** — 이전 묶음은 젯슨에서 자동 shadow.
- 큐브 명령은 서버 api 제어 라우트로만(seq 는 api 가 관리). 이 키트(학습 도구)는 서버·큐브에 명령을 보내지 않는다 — InfluxDB 를 읽기만 한다.

## 상태 (2026-11-27)

- ✅ 키트·문서·매뉴얼, 시험 29 개 통과(모델 없이), laya-edge 를 가짜 모델로 확인(묶음 검사·인증·추론·변조 거부)
- ⏳ 실제 Laya 학습·ONNX 내보내기(집 PC), 젯슨 실측, rules v2 PolicyGate 연결
