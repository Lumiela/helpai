# 결정 모델(Laya) × InfluxDB → 생육 제어 아키텍처

작성 2026-11-27 (v4 — 저장소 Lumiela/helpai `backend` 브랜치의 네이버 클라우드판 반영).

바뀐 전제
- **서버 = 네이버 클라우드** `smartcubens.duckdns.org` (backend 브랜치 2026-11-12 "네이버 클라우드 서버 코드·설정으로 교체").
  mosquitto·api·InfluxDB 2.7(`smartcube/sensors`)·Grafana·nginx 가 여기서 돈다. 장치(ESP32)는 8443(MQTT TLS)으로 붙는다.
- **InfluxDB 읽기 창구** `https://smartcubens.duckdns.org/influx` — nginx 가 조회 엔드포인트(`/api/v2/query` 등)만 열어 둔다(2026-10-16 결정).
- **젯슨 Xavier NX 8 GB = 현장(내부) 엣지.** 서버 InfluxDB 에서 데이터를 받아 Laya 로 판단하고, 서버 api 를 거쳐 큐브를 제어한다.
- **GPU 서버 반납.** 학습은 집 PC(RTX 5060 Ti 16 GB), 젯슨은 추론만.

수치는 공개 자료(공급자·제3자 발표)이며 직접 측정한 것이 아니다. 구현은 이 브랜치의 `helpai_decide/`·`edge/`.

---

## 0. 전제

### 0-1. Laya (웹 조사, 2026-11-27)

Convai Innovations 의 **Jev 호환 오픈 웨이트 결정 모델**. 글을 쓰지 않고, 미리 정한 질문에 **타입이 정해진 답 + 확률**만 돌려준다.

| 항목 | 내용 |
|---|---|
| 라이선스 | 가중치·`laya` 패키지 Apache 2.0. 지속학습 부가 패키지 `vegaml-learn` 은 **비상업** → 쓰지 않는다 |
| 체크포인트 | 영어 ModernBERT-large 421M, **문맥 512 토큰** · 다국어 mmBERT-base 322M, 1024 · typed-decisions(미세조정판) 421M, 1024 |
| 질문 타입 | `noul`(예/아니오 확률) · `choice`(선택지 + 선택지별 확률) · `score`(단계 점수) |
| 요청·응답 | `{state, questions:{이름:{type, instructions, criteria}}}` → `{answers:{…}}` — Jev 의 `/v1/systemone` 과 같은 모양 |
| 실행 방법 | Python `laya`(3.10+, PyTorch) · **ONNX 묶음**(`laya.onnx`·`laya_config.json`·`tokenizer/`, `receptron/laya` 의 `export/export_onnx.py`) · Node `@receptron/laya`(ONNX Runtime) |
| 미세조정 | 공개 노트북(Kaggle 2×T4). 공개 예: 300행 약 89 s · GPU 9 GB |
| **약점** | 영샷 정확도 낮음(업무형 시험 기본판 0.36, 미세조정판 0.77) → **미세조정 필수**. 확률 과신 → **온도 보정 필수**. `score` 가 가장 약함. `act_probability` 신호 없음. 한국어 명시 없음. 산수·개수 세기 약함 |

### 0-2. 젯슨 Xavier NX 8 GB 의 제약

| 항목 | 상태 | 결과 |
|---|---|---|
| JetPack | 5.1.x 가 마지막 (CUDA 11.4 · TensorRT 8.5 · Ubuntu 20.04) | — |
| Python | JetPack 5 의 NVIDIA 휠(PyTorch·onnxruntime-gpu)은 **Python 3.8** | `laya` 패키지(3.10+)는 못 쓴다 → **ONNX 로만 돌린다** |
| 메모리 | 8 GB 를 CPU·GPU 가 나눠 씀. 서버 서비스가 클라우드로 옮겨 가 여유가 늘었을 것 — **지금 젯슨에 무엇이 도는지 확인 필요** | 그래도 fp32(약 1.7 GB) 대신 **INT8(약 0.45 GB)** 기본, 필요하면 FP16(약 0.85 GB) |
| GPU | Volta 384 코어 | Jetson Zoo 의 onnxruntime-gpu(py3.8)로 CUDA·TensorRT 실행 **가능성 있음 — 실측 필요** |
| CPU | Carmel ARM 6 코어 | ONNX Runtime CPU 는 확실히 돈다. 질문당 수백 ms~수 초 예상 — **실측 필요** |

### 0-3. 설계 원칙

1. **학습은 밖에서, 젯슨은 추론만.** 젯슨에는 PyTorch 도, 학습 코드도 올리지 않는다. 올라가는 것은 **검증된 ONNX 묶음** 하나.
2. **모델은 "판단(라벨)"만, 명령은 코드가.** 유한 선택지 → 구동기 값 변환·제한·안전은 결정론 코드.
3. **숫자 계산은 모델에 넘기기 전에.** "상한보다 1.2 높음, 30분간 빠르게 상승" 같은 짧은 영어 판정 문장으로.
4. **학습 때의 질문 = 추론 때의 질문.** 묶음에 질문 정의를 같이 넣고, 다르면 쓰지 않는다(§6).
5. **모델이 없어도 제어는 돈다.** 모델 장애·낮은 확신 → 규칙 엔진(v2).

---

## 1. 전체 구조

```
┌──────────── 서버: 네이버 클라우드  smartcubens.duckdns.org  (backend 브랜치, 수정 없음) ─────────────┐
│  ESP32 ─MQTT TLS 8443─▶ nginx(stream) ─▶ mosquitto ⇄ api ──writer──▶ InfluxDB (smartcube/sensors)  │
│                                          ▲                              │                          │
│                     nginx 443  /api/ · /i/<iid>/api/ (인증)              │  /influx/ 조회 전용 창구  │
└──────────────────────────────────────────│──────────────────────────────│──────────────────────────┘
          HTTPS GET values · POST control  │                              │ HTTPS Flux (읽기 토큰)
                                           │                              │
┌──────────────────── 현장(내부): 젯슨 Xavier NX 8 GB — 추론·판단만 ───────────────────────────────────┐
│            ┌──────────────────────────────┴────── rules (v2 + 결정 계층) ─┴──────────┐              │
│            │ ① InfluxReader(/influx)  ② FeatureBuilder  ③ RuleEngine(v2)            │              │
│            │ ④ DecisionModel ──HTTP──▶ laya-edge  POST /v1/systemone (젯슨 안)      │              │
│            │ ⑤ Calibrator(묶음 calibration.yaml)  ⑥ PolicyGate                      │              │
│            │ ⑦ Arbiter/Safety(v2)  ⑧ Gateway(서버 api)  ⑨ Sink(decisions-*.jsonl)   │              │
│            └────────────────────────────────────────────────────────────────────────┘              │
│            ┌─ laya-edge ─ ONNX Runtime(CPU) · 토크나이저·후처리 ◀── /data/laya/models/current      │
└──────────────────────────────────────────────────────────▲─────────────────────────────────────────┘
                                                           │ 묶음 bundles/<rev>.tar (scp / USB)
┌──────────────── 학습: 집 PC RTX 5060 Ti 16 GB ────────────┴─────────────────────────────────────────┐
│ export-influx(https://…/influx, 읽기 토큰) → replay + synth → 교사 라벨 → laya 미세조정              │
│ → ONNX → INT8 → edge 서버로 예측·비교 → 보정 → 평가 → 묶음(manifest + SHA256SUMS)                    │
└────────────────────────────────────────────────────────────────────────────────────────────────────┘
   ESP32 펌웨어 안전장치: 펌프 30 s 자동 정지 (연결이 끊겨도 다른 출력은 유지 — LEASE_ENFORCED=false, docs/05 §3)
```

| 결정 | 이유 |
|---|---|
| 판단 계층은 **rules v2 안에** (젯슨) | 구역·신선도·안전·사람 우선·화면·시험 55개를 그대로 쓴다 |
| 추론은 **젯슨의 별도 `laya-edge` 컨테이너** | ONNX Runtime·가중치를 rules 에서 떼어 낸다. 메모리 상한을 따로 걸어 OOM 이 나도 rules 는 산다. 추론에 외부 인터넷이 필요 없다 |
| 인터페이스는 **`/v1/systemone`** | Laya·Jev 와 같은 모양. 나중에 Jev 클라우드나 다른 추론 장비로 바꿔도 rules 는 주소만 바꾼다 |
| 데이터는 **서버 InfluxDB 읽기 창구** `/influx/` | 이미 열려 있는 조회 전용 경로(허용 목록 밖 404). 쓰기 불가 → 젯슨·집 PC 가 서버 DB 를 망가뜨릴 수 없다. 토큰은 읽기 전용만 |
| 지금 값은 **서버 api `values`** | 10 초 주기의 최신값·신선도(`seen`)는 api 메모리가 가장 빠르다. InfluxDB 는 추세·이력용 |
| 명령은 **서버 api 제어 라우트로만** | seq 는 api 메모리에 있다. MQTT 로 직접 쏘면 `duplicate_or_old_seq` 로 거절된다(v2 문서 §1). api 인증(`AUTH_MODE=required`)용 토큰이 필요 |
| 학습 데이터는 **rules 의 판단 기록 + Influx 이력** | 기록에 특징·질문·답을 더 적으면 그대로 데이터셋이 된다 |

**서버(backend 브랜치: api·DB 스키마·nginx) 수정 0건.** 바뀌는 것은 젯슨 쪽(rules 코드, laya-edge 컨테이너)과 환경변수뿐이다.

---

## 2. 한 주기의 흐름 (구역마다)

규칙 엔진은 10 초마다, 모델은 **60 초마다**(Xavier CPU 를 기준으로 잡은 값. GPU 실행이 되면 30 초로). 답은 TTL 90 초.

```
tick (10 s)
 ├─ 펌프 펄스 끄기 (읽기 전에, v2 그대로)
 ├─ 읽기: api values (신선도) + InfluxReader 캐시(60 s 갱신)
 ├─ 기준 판단: RuleEngine → demands_rules
 ├─ (60 s 마다) FeatureBuilder → state → laya-edge.ask()   ※ 비동기, 시간 초과 8 s(CPU) / 2 s(GPU)
 ├─ Calibrator → PolicyGate → demands_final
 ├─ Arbiter · Safety (v2) → 명령
 └─ Sink: 특징 · 규칙 판단 · 모델 원확률/보정확률 · 모델 revision · 채택 여부 · 명령
```

---

## 3. 젯슨 쪽 — `laya-edge`

### 3-1. 실행 방식

| 순서 | 방식 | 장점 | 위험 |
|---|---|---|---|
| **A (먼저)** | Node 20 (arm64) + `@receptron/laya` + INT8 ONNX, **CPU** | 이미 만들어진 토크나이저·프롬프트 조립·후처리를 그대로 씀. 가장 빨리 돌려 볼 수 있다 | 느릴 수 있음. 제3자 포팅판 |
| **B (A 가 느리면)** | Python 3.8 (L4T r35 기반 이미지) + Jetson Zoo `onnxruntime-gpu` + FP16 ONNX, **GPU** (실행 제공자 TensorRT → CUDA → CPU) | GPU 사용, 빠름 | 전·후처리를 직접 옮겨야 함(A 의 TS 코드를 참고, MIT). py3.8 용 `tokenizers` 등 판 고정 필요. ModernBERT 연산의 TensorRT 8.5 호환 미확인 |

두 방식 모두 **같은 HTTP 계약**(`POST /v1/systemone`, `GET /health`, `GET /model`)을 연다 → rules 는 어느 쪽인지 모른다.

### 3-2. HTTP 계약

| 요청 | 내용 |
|---|---|
| `POST /v1/systemone` | Laya·Jev 와 같은 요청·응답. `Authorization: Bearer $LAYA_API_KEY` |
| `GET /health` | 적재 여부, 실행 제공자(cpu/cuda/tensorrt), 최근 지연 p50·p95, 메모리 |
| `GET /model` | 지금 묶음의 `manifest.json` (revision, 질문 세트 해시, 정밀도, 검증 결과) |
| `POST /admin/reload` | `current` 링크가 가리키는 묶음 다시 적재(검사 실패 시 이전 묶음 유지) |

### 3-3. compose (젯슨 전용 compose — `edge/docker-compose.yml`)

서버 compose(backend 브랜치)와 섞지 않는다. 젯슨에는 젯슨용 compose 를 따로 둔다(나중에 rules 도 여기에).

```yaml
  laya-edge:
    build: .                       # A: node:20-bookworm-slim(arm64)  /  B: l4t 기반 python3.8 + onnxruntime-gpu
    image: helpai-laya-edge:1
    container_name: laya-edge
    restart: unless-stopped
    # runtime: nvidia              # B 일 때만
    environment:
      LAYA_MODEL_DIR: /models/current
      LAYA_API_KEY: ${LAYA_API_KEY}
      LAYA_THREADS: "3"
    volumes:
      - /data/laya/models:/models:ro       # NVMe. <revision>/ 폴더들 + current 링크
    mem_limit: 1500m                       # 넘치면 이 컨테이너만 죽는다
    cpus: "3"                              # 6 코어 중 절반 — api·InfluxDB 몫을 남긴다
    # ports: 없음 — http://laya-edge:8000 내부망만
```

---

## 4. 학습 쪽 — 젯슨 밖에서 사용자가

구현: 이 저장소의 `helpai_decide` 패키지와 `scripts/pipeline.sh` (명령별 설명은 manual/02).

```
① 데이터 받기         export-influx  — 서버 InfluxDB 읽기 창구(https://smartcubens.duckdns.org/influx) → 1분 평균 CSV
② 데이터셋 만들기     dataset build  — 합성(synth) + 이력 재생(replay) → 교사 라벨 → train / val / test
                       재생 데이터는 날짜로 나눔(같은 날이 양쪽에 섞이지 않게). 합성은 id 해시로 80/10/10
                       정답 = 교사(규칙 기준, labeler.py) · 나중에 사람 정정·shadow 기록을 더한다
③ 미세조정            train          — laya.train.finetune, typed-decisions 판에서 시작 (집 PC RTX 5060 Ti 16 GB)
④ ONNX 내보내기       export-onnx    — receptron/laya 의 export_onnx.py → fp32
⑤ INT8 변환           convert        — onnxruntime 동적 양자화 (젯슨 CPU 용)
⑥ 예측·비교           predict·compare — 집 PC 에서 edge/server.mjs(젯슨과 같은 런타임)로 fp32·INT8 둘 다 → 최상위 답 일치율 ≥ 97 %
⑦ 온도 보정           calibrate      — INT8 val 예측으로 질문별 사후 온도 → calibration.yaml
⑧ 평가                evaluate       — INT8 test, 질문별 정확도 ≥ 0.9 · ECE ≤ 0.1 → eval_report.json
⑨ 묶음                bundle make·verify → bundles/<revision>.tar
⑩ 올리기              scp → 젯슨 /data/laya/models/<revision>/  →  카나리(§7) → current 교체 (manual/03)
```

- 규칙 판단만 정답으로 쓰면 모델은 **규칙을 흉내** 낸다. 그래도 센서 이상 감지·경계 상황 일반화에 쓸모가 있지만,
  규칙보다 나아지려면 **사람 정정**과 결과 지표(범위 안 시간 비율)로 라벨을 고쳐야 한다.
- 데이터는 센서 숫자와 코드가 만든 문장뿐이다(개인정보 없음). Colab·Kaggle 에 올려도 되는 수준이지만, 올릴지는 사용자가 정한다.

---

## 5. InfluxDB 에서 받는 것

저장 형식(api `writer.py`): `measurement=mqtt`, 태그 `cube`, `kind=data|status|ack`, 필드는 전부 float.

```flux
from(bucket: "sensors")
  |> range(start: -30m)
  |> filter(fn: (r) => r._measurement == "mqtt" and r.cube == "CUBE001" and r.kind == "data")
  |> filter(fn: (r) => contains(value: r._field, set: ["temperature","humidity","lux","moisture_pct"]))
  |> aggregateWindow(every: 2m, fn: mean, createEmpty: false)
```

| 조회 | 주기 | 쓰는 곳 |
|---|---|---|
| 30 분 2 분 평균 (온도·습도·조도·토양) | 60 s | 추세, 노이즈 제거 |
| 오늘 0시~지금 `pump` 상승 횟수 | 60 s | 하루 관수 남은 횟수 (재시작해도 안 틀림) |
| 오늘 누적 광량(lux·시간) | 5 min | 일장·광량 부족 |
| 마지막 관수 이후 토양 수분 변화 | 60 s | "물을 줬는데 안 오름" = 센서·배관 이상 |

접근: 젯슨 rules 와 집 PC 모두 `https://smartcubens.duckdns.org/influx` + 읽기 전용 토큰(환경변수 `INFLUX_READ_TOKEN`).
nginx 허용 목록(`/influx/api/v2/query`·`buckets`·`orgs`·`query`·`health`·`ping`) 밖은 404 라 쓰기는 원천적으로 막혀 있다.
조회 시간 초과 5 s. 실패하면 추세 문장만 빼고 진행(현재값은 api `values` 에서 온다). 인터넷이 끊기면 현재값도 못 받으므로 그 동안 rules 는 v2 그대로 "장치 신선도 초과 → 그 장치 제외"로 동작하고, 큐브 펌웨어 안전장치가 남는다.
**학습용으로도 같은 조회를 쓴다** — 과거 구간을 다시 돌려(replay) 특징을 만들면, 기록이 적을 때 데이터셋을 늘릴 수 있다.

---

## 6. 묶음(bundle) — 학습과 추론을 잇는 계약

```
/data/laya/models/
  2026-12-05_r3/
    laya.onnx  laya_config.json  tokenizer/
    calibration.yaml          # 질문별 사후 온도
    questions.yaml            # 학습에 쓴 질문 정의 (이름·타입·instructions·criteria)
    eval_report.json          # test 셋 결과
    manifest.json
    SHA256SUMS                # LF 줄바꿈, `sha256sum -c SHA256SUMS` 로도 검사 가능
  current -> 2026-12-05_r3
```

```json
{
  "revision": "2026-12-05_r3",
  "base": "convaiinnovations/laya (typed-decisions)",
  "precision": "int8",
  "questions_sha256": "…",
  "feature_version": 1,
  "kit_commit": "…", "exporter_commit": "…",
  "dataset": {"rows_synth": 20000, "rows_replay": 4210, "split_test": 2400},
  "eval": {"fan_action": {"accuracy": 0.93, "ece": 0.06, "temperature": 1.4}}
}
```
(`helpai_decide/bundle.py` 가 만든다. 실제 키는 그 파일이 기준이다.)

rules 가 모델 답을 쓰기 전에 검사:
- `questions_sha256` = rules 의 지금 질문 정의 해시, `feature_version` = 지금 특징 판 → 다르면 **shadow 로 강제**(기록만)
- 질문별 `eval` 이 문턱(acc ≥ 0.9, ece ≤ 0.1) 미달이면 그 질문은 shadow
- `SHA256SUMS` 불일치 → laya-edge 가 적재 거부, 이전 묶음 유지

---

## 7. 모델 교체 — 카나리

```
1. 새 묶음을 <revision>/ 에 복사 (current 는 그대로)
2. laya-edge 가 "후보" 로 함께 적재 (메모리가 모자라면 이 단계 생략하고 3 으로)
3. 24 시간: 같은 요청을 current·후보 둘 다에 → 후보 답은 기록만
4. 후보가 질문별로 current 이상 (규칙 일치율·사람 정정 건수) → current 링크 교체 → /admin/reload
5. 문제 시 current 를 이전 revision 으로 되돌림 (명령 1줄)
```

메모리 여유가 없으면 2~3 대신 **밤 시간(제어 변화 적음)에 교체 → 다음 날 지표 비교 → 나쁘면 되돌림**.

---

## 8. state 설계 — 짧게, 고정된 형태로

- 기반 체크포인트 typed-decisions 판은 문맥 1024 토큰(영어판 512). **한 state 를 다섯 질문이 같이 쓴다** — 9개 키, 약 150~250 토큰.
  `dataset build` 가 대략 300 토큰을 넘는 행을 빼고, `token-count` 가 실제 토크나이저로 잰다.
- 문장은 `helpai_decide/features.py` 하나가 만든다(학습·추론 공용). 바꾸면 `FEATURE_VERSION` 을 올린다.
- 장치에서 온 문자열은 넣지 않는다(입력 문구로 점수를 조종할 수 있다는 지적이 있다). **숫자 → 코드가 만든 문장만.**

```json
{
  "crop": "rose",
  "time": "14:10, daytime (light period 06:00 to 22:00)",
  "temperature": "31.2 C; hot: above max 30.0 by 1.2; rising fast (+1.8 C in 30 min)",
  "humidity": "71.0 %; within range (max 85.0); flat over 30 min",
  "light": "LED 100 % (already at max); 4800 lux, below min 6000",
  "soil": "38.0 %; below min 40.0 by 2.0; sensor calibrated",
  "irrigation": "3 of 12 used today; last 45 min ago, interval passed",
  "fan": "33 % (level low)",
  "data": "fresh (oldest reading 4 s old)"
}
```
문장 규칙 전체는 docs/04.

---

## 9. 질문 설계

정답 기준은 **지금 서버에서 실제로 제어하는 api autoctl** 이다 (docs/05, 상세 규칙은 docs/04 §3).

| 이름 | 타입 | 선택지 / 기준 (autoctl) | 코드 변환 |
|---|---|---|---|
| `fan_action` | choice | `off`(온도·습도 최저 미만) · `down`(온도 ≤ 최고−1, 습도 ≤ 최고−5) · `hold` · `up`(온도·습도 최고 초과) | 지금 단계(0·33·66·100)에서 한 칸 / 0 (`labeler.fan_target`) |
| `light_action` | choice | `off`(야간) · `decrease` · `hold` · `increase` (주간 조도 범위) | 현재 ± 10 % |
| `irrigate_now` | noul | 토양 < 30 %, 보정됨, 60 분 대기 지남, 오늘 < 4 회, 펌프 꺼짐 | 보정 P ≥ 0.9 일 때만 5 s 펄스 후보 |
| `plant_stress` | choice | `none`·`mild`·`moderate`·`severe` (autoctl 범위 이탈 기반 확장) | moderate 이상 경보 |
| `data_suspect` | noul | autoctl "센서 점검 필요" + 고착·급수 후 무반응·LED 높은데 조도 낮음 | P ≥ 0.8 → 그 구동기 보류 + "센서 점검" |

- `score` 가 약해서 `plant_stress` 는 choice. 선택지 이름은 뜻이 드러나게(모델이 정의보다 이름을 따라간다는 연구: arXiv 2609.26758).
- 팬은 autoctl 처럼 "한 칸"을 묻는다 — 목표 단계(4 개)를 묻는 것보다 autoctl 과 직접 비교되고, 한 번에 급변하지 않는다.
- 질문 정의는 `questions.yaml` 하나에서 제어기·학습 양쪽이 읽는다. 바꾸면 해시가 바뀌어 **재학습 전까지 자동 shadow**.

---

## 10. PolicyGate

"기준" = autoctl 규칙과 같은 결정론 판단(`labeler.HARD` 의 함수들 — 학습 정답과 같은 코드).

```
1. 답 없음/만료/오류                 → 기준          (model_unavailable)
2. 묶음 검사 실패(§6)                → 기준, 답은 기록만
3. 보정 확신 < 문턱                  → 기준          (low_confidence)   choice 0.75 · noul ≥ 0.9 / ≤ 0.1
4. 안전 봉투 밖                       → 봉투 안으로    (clamped)
      급수 하루 4 회 · 60 분 대기 · 보정 안 된 토양 센서면 급수 안 함 · 야간 LED 0 은 기준이 항상 이긴다
5. 모델 ≠ 기준   shadow → 기준 · advisory → 한 단계 차이까지 model · primary → model
6. → 실행 조건(앞 단계 적용 확인 · 팬 120 s 유지 · 15 s 재전송 방지 · 펄스 끄기)은 autoctl 과 같게 제어기가
```

**지금(autoctl 운영 중)은 1 단계 shadow 만** — 젯슨이 명령을 보내면 사람 조작으로 처리돼 autoctl 이 꺼진다(docs/05 §7).

---

## 11. 장애와 안전

| 상황 | 동작 |
|---|---|
| laya-edge 죽음·시간 초과 | 규칙 엔진만으로 계속. 연속 실패 시 호출 간격 증가(최대 5 분). 화면에 "모델 꺼짐" |
| 젯슨 ↔ 서버 인터넷 끊김 | 추론은 젯슨 안이라 모델은 살아 있지만, **서버 값·명령 경로가 끊겨** rules 가 제어하지 못한다(v2: 신선도 초과 장치 제외). 큐브는 펌웨어 안전장치로 버틴다 — 서버 중앙 제어(03 평가서 S0)의 원래 한계 |
| 서버 InfluxDB 창구만 실패 | 추세 문장 없이 현재값(api)만으로 판단 |
| 젯슨 메모리 부족 | `mem_limit` 1.5 GB → laya-edge 만 죽는다 |
| 젯슨 CPU 과부하 | `cpus: 3`, 주기 60 s. rules 가 api 응답 시간을 보고 늘면 모델 호출을 줄인다 |
| 새 묶음이 나쁨 | 카나리에서 걸러짐. 지나갔어도 `current` 되돌리기 1줄 |
| 답이 흔들림 | 같은 선택이 2 회 연속이어야 바꿈 |
| 사람이 웹에서 조작 | 30 분 보류 (autoctl 은 사람 조작 시 아예 꺼짐 — 같은 원칙) |
| api 내장 autoctl 과 충돌 | 한 큐브에 제어 주체 하나. autoctl 이 켜진 큐브(`GET …/auto` 의 enabled)에는 젯슨이 명령을 보내지 않는다 — shadow 로만 |

---

## 12. 파일

**이 저장소(jev) — 구현 완료**

```
helpai_decide/           학습·추론 공용 패키지 (핵심은 pyyaml 만 필요 → rules 엔진이 그대로 import)
  features.py            Snapshot → state 문장              questions.py   질문 정의·해시
  labeler.py             교사 라벨(규칙 기준 gold)            synth.py       합성 시나리오
  replay.py              Influx 1분 CSV → Snapshot            influx_export.py  Flux → CSV
  dataset.py             train/val/test 조립·검사            calibration.py 사후 온도·ECE·Brier
  predict.py             /v1/systemone 클라이언트·비교        bundle.py      묶음 만들기·검사
  laya_tools.py          학습 PC 전용(check-env·download-base·train·export-onnx·convert)
  cli.py                 python -m helpai_decide <명령>
configs/                 questions.yaml · dataset.yaml · training.yaml · replay.yaml
profiles/crops.json      작물표 (backend 브랜치 api/app/crops.json 복사본)
edge/                    laya-edge: server.mjs · bench.mjs · Dockerfile · docker-compose.yml(젯슨 전용)
scripts/pipeline.sh      집 PC 학습 전 과정
tests/                   시험 28개 (모델 없이)
```

**rules v2 에 더할 것 — 다음 단계(아직 없음)**

```
helpai_rules/
  ports.py               + DecisionModel, SeriesSource
  adapters/
    influx_reader.py     + Flux 조회, 60 s 캐시 → helpai_decide.features.Snapshot 만들기
    systemone_http.py    + helpai_decide.predict.HttpPredictor 감싸기
    decision_fake.py     + 시험·시뮬레이션용
  domain/
    policy_gate.py       + §10
    bundle_check.py      + GET /model 의 manifest 를 helpai_decide.questions·features 와 대조
```

```yaml
# config/zones.yaml 추가분
decision_model:
  enabled: true
  mode: shadow
  endpoint: {kind: systemone_http, base_url: http://laya-edge:8000, key_env: LAYA_API_KEY, timeout_s: 8}
  every_s: 60
  answer_ttl_s: 90
  state_token_budget: 300
  questions_file: ../config/questions.yaml
  thresholds: {choice: 0.75, noul: 0.9, suspect: 0.8, bundle_acc: 0.9, bundle_ece: 0.1}
  per_question_mode: {data_suspect: advisory}
series: {kind: influx, url: https://smartcubens.duckdns.org/influx, org: smartcube, bucket: sensors, token_env: INFLUX_READ_TOKEN, refresh_s: 60}
# gateway 는 서버 api 로: base_url https://smartcubens.duckdns.org/api, token_env HELPAI_TOKEN (AUTH_MODE=required)
```

---

## 13. 도입 단계

| 단계 | 내용 | 통과 기준 |
|---|---|---|
| 0 | `decision_fake` + 시뮬레이터로 게이트·장애·묶음 검사 시험 | 기존 시험 55 + 새 시험 통과 |
| 1 | **기본판 Laya** 를 PC 에서 ONNX INT8 로 → 젯슨 laya-edge(A, CPU) 실측 | 5문항 < 8 s, 젯슨 남은 메모리 ≥ 1 GB, api 응답 시간 변화 없음. 못 넘으면 B(GPU) 시도 |
| 2 shadow | 기본판으로 기록만 + 시뮬레이터 합성 | 1~2 주, 질문별 수백 건 |
| 3 학습 | PC/Colab 에서 미세조정 → 보정 → 묶음 → 카나리 | 질문별 acc ≥ 0.9, ECE ≤ 0.1, ONNX 일치 ≥ 97 % |
| 4 advisory | `data_suspect`·`plant_stress` → `fan_action` → `light_action` | 안전 위반 0, autoctl 과 일치율 기준 충족 |
| 5 primary | 질문별 승격, `irrigate_now` 마지막 | 범위 안 시간 비율 ≥ 규칙만 쓸 때 |

평가 지표: 범위 안 시간 비율, 명령 횟수, 관수량, 규칙 대비 개선량, 모델 실패율·지연, ECE.

---

## 14. 확인이 필요한 것

1. **젯슨의 지금 역할** — 서버가 클라우드로 옮긴 뒤 젯슨에 무엇이 남아 도는지, 메모리 여유 (`cat /etc/nv_tegra_release` · `free -h` · `docker ps` · `docker stats --no-stream`)
1-1. rules 가 서버 api 를 부를 **서비스 토큰**(AUTH_MODE=required)과 InfluxDB **읽기 토큰** — 값은 젯슨 `.env` 에만
2. ~~학습 장소~~ → **개인 PC RTX 5060 Ti 16 GB** (2026-11-27). 공개 예 9 GB 보다 여유가 있고, 우리 입력은 512 토큰 이하라 더 적게 든다.
   주의: Blackwell(sm_120) 이라 **CUDA 12.8 용 PyTorch(2.7 이상, cu128 휠)** 가 필요하다. FlashAttention 대신 PyTorch 기본 SDPA 사용,
   `laya[fast]`(TileLang) 는 쓰지 않는다. Windows 면 WSL2(Ubuntu) 권장. 학습 정밀도는 laya 가 CUDA 에서 fp16 autocast 로 정한다
   (bf16 설정은 laya 0.5.5 에 없음). ONNX 내보내기·INT8 양자화·일치 검사도 같은 PC 에서
3. `@receptron/laya` 가 젯슨 arm64 에서 도는지(onnxruntime-node arm64) — 단계 1 에서 바로 확인
4. 처음엔 경보용(`data_suspect`, `plant_stress`)만 쓸지 — 위험이 가장 낮다

## 참고 자료
- Laya 모델 카드: https://huggingface.co/convaiinnovations/laya
- Laya Node/ONNX 포팅·ONNX 내보내기: https://github.com/receptron/laya
- Runware — Jev, Laya, and decision models: https://runware.ai/blog/jev-laya-and-the-emerging-role-of-decision-models
- Type-Safe Is Not Error-Free (선택지 이름 편향): https://arxiv.org/pdf/2609.26758
- NVIDIA — PyTorch for Jetson (JetPack 5 = Python 3.8): https://forums.developer.nvidia.com/t/pytorch-for-jetson/72048
- NVIDIA Forum — onnxruntime on JetPack 5.1.2: https://forums.developer.nvidia.com/t/cant-install-onnxruntime-on-jetpack-5-1-2/277379
