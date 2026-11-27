# CLAUDE.md — decision_model 브랜치 작업 지침

이 저장소(Lumiela/helpai)의 **`decision_model` 브랜치**: 서버 InfluxDB 이력으로 결정 모델 Laya 를 학습(집 PC)하고 젯슨에서 추론만 하는 키트.
**먼저 `docs/06_작업_지침과_방향.md` 를 읽는다** — 현재 상태, 진행 단계, 열린 질문, 결정 기록이 거기 있다.

## 시작할 때
```bash
python -m unittest discover -s tests -t .     # 모두 OK 여야 작업 시작 (Windows 는 PYTHONUTF8=1)
git log --oneline -10
```

## 꼭 지킬 것
- **서버(backend 브랜치)·Hardware·main 은 고치지 않는다.** 읽기만: `git show origin/backend:<경로>`.
- **큐브에 명령을 보내는 코드(서버 제어 라우트·MQTT)를 만들지 않는다** — docs/06 §5 의 단계 6 전까지. autoctl 이 켜진 큐브에 명령을 보내면 autoctl 이 꺼진다(docs/05 §7).
- **교사 라벨 기준은 서버 api 의 autoctl** (docs/05). 규칙을 바꾸면 시험 → 코드 → docs/04·05 순서.
- `features.py` 를 바꾸면 `FEATURE_VERSION` + 1, `configs/questions.yaml` 을 바꾸면 `version` + 1. 둘 다 데이터셋부터 다시.
- 토큰·키(`INFLUX_READ_TOKEN`, `LAYA_API_KEY`, HF 토큰, 서버 서비스 토큰)는 환경변수로만. 파일·명령 인자·커밋 금지.
- 모델·데이터·묶음(`data/ models/ runs/ bundles/`)은 git 에 넣지 않는다. 작은 결과만 `reports/<rev>/`.
- 문서는 한국어, 표 위주, "확인한 것 / 못 한 것" 구분. 코드와 문서가 다르면 코드가 맞다.
- 커밋은 시험 통과 상태로, `decision_model` 브랜치에만. 진행하면 docs/06 §2 상태표·§8 결정 기록을 같이 고친다.

## 구조 한 줄씩
- `helpai_decide/` 학습·추론 공용 패키지 (핵심은 pyyaml 만) — `python -m helpai_decide --help`
- `edge/` 젯슨 laya-edge 추론 서버(Node, `/v1/systemone`)
- `scripts/pipeline.sh` 집 PC 학습 전 과정
- `docs/00~06`, `manual/01~04`

## 환경
- 서버: 네이버 클라우드 `smartcubens.duckdns.org` (api·InfluxDB, 조회 창구 `/influx`)
- 학습: 집 PC RTX 5060 Ti 16 GB, WSL2, PyTorch cu128, laya 0.5.5
- 추론: 젯슨 Xavier NX 8 GB, JetPack 5, ONNX INT8 CPU, `@receptron/laya` 0.2.0
