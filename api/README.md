# 스마트큐브 API — 젯슨 실험용 골격

작성: 랩탑 ClaudeCode (Fable 5.1), 2026-09-22. 사용자 지시로 생성.
젯슨 compose 에 `api` 서비스로 들어가는 FastAPI 백엔드 골격. nginx 설정에 주석으로 비워
두었던 `/api/ → api:8000` 자리를 채우는 것이다. **파일만 만들었고 젯슨에 올리지 않았다.**

## 사용자 결정 (2026-09-22)

| 항목 | 결정 |
|---|---|
| 인증 | **없음(개발용).** 그래서 nginx 의 `/api/` 주석은 풀지 않는다 — 풀면 Funnel 443 으로 인터넷에 공개된다 |
| 실시간 | SSE |
| 제어 프레임 | **API 가 직접** 부호화·복호화 (중계기는 손대지 않음) |
| 진행 범위 | 파일만. 배포·공개는 지시 후 |

## 구성

```
브라우저/앱 ──HTTP──▶ web(nginx) /api/ ──▶ api:8000 (이 서비스)
                                            ├─ mosquitto:1883   device/…/{public,status,ack} 구독
                                            │                   device/…/notify/control 발행
                                            └─ influxdb:8086    이력 Flux 조회

같은 compose 네트워크(data_default). ports: 없음 — 밖으로 직접 안 나간다.
```

| 파일 | 내용 |
|---|---|
| `app/main.py` | FastAPI 앱. MQTT 구독 스레드 + 라우트 |
| `app/agframe.py` | 아라온실 VALUE v1 프레임 부호화·복호화(중계기·시험 발행기와 같은 로직) |
| `Dockerfile`, `requirements.txt` | python:3.12-slim + fastapi·uvicorn·paho-mqtt·influxdb-client |
| `compose.snippet.yml` | `/data/docker-compose.yml` 에 붙일 `api:` 블록 |
| `nginx.snippet.conf` | 주석 자리를 대체할 `/api/` 블록(SSE 설정 포함). **인증 전엔 적용 금지** |

## 라우트

`/api` 접두어는 nginx 가 떼고 넘기므로 앱 안에서는 없다.

| 메서드·경로 | 하는 일 | 데이터 출처 |
|---|---|---|
| `GET /health` | 브로커 접속 여부, 큐브 수, 가동 시간 | — |
| `GET /cubes` | 지금까지 텔레메트리를 보낸 큐브 목록(세션·마지막 수신) | MQTT 캐시 |
| `GET /cubes/{sn}/values` | 최신 텔레메트리 전체(세션 포함, 숫자·문자 다) | MQTT 원본 프레임 |
| `GET /cubes/{sn}/status` | 최신 상태 프레임(모델·펌웨어·출력 활성 여부) | MQTT 원본 프레임 |
| `GET /cubes/{sn}/history?start=-1h&field=moisture_raw` | 이력 | InfluxDB Flux |
| `POST /cubes/{sn}/control` | 제어 명령 → 프레임 발행 → 응답(ack) 대기 후 결과 반환 | MQTT |
| `POST /cubes/{sn}/refresh` | 펌웨어에 "지금 텔레메트리 보내라" | MQTT |
| `GET /cubes/{sn}/stream` | SSE. 새 텔레메트리마다 1건, 15초마다 keepalive | MQTT 캐시 |

제어 본문은 `{"pump":true,"fan1":60}` 처럼 **일부만** 보내도 된다. 빠진 항목은 마지막
텔레메트리의 현재값으로 채워 펌웨어가 요구하는 7개 키를 만든다.

## 왜 원본 프레임을 직접 읽나

중계기(bridge)는 숫자 필드만 `smartcube/…` 평문 토픽으로 넘긴다. 그래서 펌웨어가 발급한
**세션 문자열**과 응답의 **결과 문자열(ok / denied / stale_session …)** 은 원본
`device/…` 토픽에서만 얻을 수 있다. 제어 명령은 이 세션과 증가하는 순번(seq)이 맞아야
펌웨어가 받아들이므로, API 가 원본을 구독해 세션을 추적한다. 펌웨어가 재접속하면
세션이 바뀌고 순번은 0부터 다시 센다 — API 도 그에 맞춰 리셋한다.

중계기·수집기·대시보드는 그대로다. 이 서비스는 옆에 붙기만 한다.

## 배포 절차 (지시 후 실행)

1. `api/` 폴더를 젯슨 `/data/api/` 로 복사
2. `compose.snippet.yml` 의 블록을 `/data/docker-compose.yml` 에 추가
3. `cd /data && docker compose up -d --build api`
4. 내부 시험: `docker exec web wget -qO- http://api:8000/health`
5. **인증을 붙인 뒤에만** `nginx.snippet.conf` 를 적용해 `/api/` 를 연다

## 확인한 것 / 못 한 것

- 확인: 파이썬 문법, 프레임 부호화·복호화가 젯슨 중계기(`bridge.py`)와 상호 호환, 제어
  본문이 펌웨어의 7개 키와 일치 (검증 스크립트로)
- **못 한 것**: 실기. 젯슨에 올리지 않았으므로 브로커 구독·InfluxDB 조회·SSE·제어 왕복은
  전부 미검증. 펌웨어 쪽 제어 처리도 아직 시험된 적 없다(출력이 꺼져 있어 `outputs_disabled`
  로 응답할 것으로 예상)

## 이 골격에 없는 것

- **인증.** 공개 전 필수. API 키 헤더가 가장 단순하다
- 재전송. 응답이 없으면 실패로 돌려주기만 한다(계획서의 "게이트웨이가 재전송" 은 후속)
- 여러 큐브 동시 제어, 임계값 설정, 알림 — 계획서의 앱 API 범위이며 여기선 미구현
- 대시보드(`fr/`) 연결. 지금 화면은 모의값이며 이 API 를 부르지 않는다

## 젯슨 배포·내부 시험 결과 (2026-09-22 15:38~15:53 KST, 랩탑 ClaudeCode / Fable 5.1)

위 "배포 절차" 1~4를 사용자 지시로 실행했다. 5(공개)는 하지 않았다. 상세는 작업 일지 §28.

| 항목 | 결과 |
|---|---|
| 배포 | `/data/api/`, compose 서비스 `api`(이미지 smartcube-api:1, `ports:` 없음), 백업 `/data/docker-compose.yml.bak-20260922-1537`, 첫 빌드 42초 |
| 공개 | **안 함.** nginx `/api/` 주석 그대로(인증 없음) |
| 시험 방법 | `docker exec web wget -qO- http://api:8000/...` (nginx 컨테이너에서 내부망으로). SSE는 호스트 `timeout`으로 `docker exec`를 끊으면 출력이 안 보이므로 컨테이너 안 `timeout` 또는 api 컨테이너 안 파이썬 클라이언트로 |
| 실기 | ESP32 SC-01(3초 주기). 가짜 TEST-01은 쓰지 않음 |

### 확인된 것

- 라우트 8개 전부: health · cubes · values(17개 필드 원본) · status · history(-1h 76건, 0.07초) · refresh · stream(3초 간격 푸시) · **control 왕복**
- control: seq 1·2·3·4 모두 펌웨어 ack `outputs_disabled` 1.2~1.6초(출력 비활성이라 거부 = 정상). 부분 본문 채움 정상
- 오류 경로: 422(범위) · 400(start 꼴·일련번호) · 404(미지 큐브) · 409(세션 모름) · 504(장치 부재 5초)
- 기존 중계기·수집기·대시보드 무영향(컨테이너 재시작 없음, 중계 로그 계속)

### 시험에서 드러나 같은 날 고친 것 (사용자 결정 J29·J30)

1. status 프레임으로 세션이 바뀔 때 로그가 없었음 → `_on_message` status 분기에 로그 추가.
2. **api 재기동 뒤 순번이 1부터 다시 시작 → 펌웨어가 `duplicate_or_old_seq`로 거절**(펌웨어는 세션 안에서 마지막 받은 순번을, 거절된 것까지 기억). 수정: `_on_connect`에서 그룹 토픽 `device/<앱>`에 빈 메시지 발행 → 펌웨어가 status 회신 → `last_received_seq`를 순번 하한으로 이어받음(public의 `applied_seq`도 하한). 재기동 1초 뒤 status 200, 이어서 seq 3·4 정상 ack. 그룹 토픽은 2단계라 중계기 구독에 안 걸림.

### 여전히 못 한 것

- 제어 **"ok" 경로**(출력 활성 펌웨어에서 실제 적용·lease·pump_locked). 이번은 전부 거부 경로
- 인증 — 공개 전 필수(변함없음)
- 장시간 안정성, 재전송, 여러 큐브

## 보완 (2026-09-28, 랩탑 ClaudeCode / Fable 5.1) — 토양 원격 보정 라우트 `POST /cubes/{sn}/config`

ESP32-CAM 단독 통합판 펌웨어(`esp32/esp32_cam_node/`, 같은 날 작성)가 `notify/config` 로 원격 보정을 받도록 되면서, 서버에서 그 프레임을 보내는 라우트를 `app/main.py` 에 더했다. **위 라우트 표에는 넣지 않고 여기에 적는다(문서 편집 규칙: 기존 본문 수정 금지).** 젯슨에는 아직 배포하지 않았다 — 배포 절차 3단계(`docker compose up -d --build api`)를 다시 밟아야 동작한다.

| 메서드·경로 | 하는 일 | 데이터 출처 |
|---|---|---|
| `POST /cubes/{sn}/config` | 토양 보정값 지정 / 원격 측정 → `notify/config` 프레임 발행 → 펌웨어가 다시 보내는 status 를 기다려 새 `air`/`water` 반환 | MQTT |

본문(JSON) — 셋 중 하나 이상:

| 키 | 값 | 펌웨어에 가는 것 |
|---|---|---|
| `air`, `water` | 0~32767 (ADS1115 원시값. 한쪽만 보내도 됨) | `{"air":N,"water":N}` |
| `cal` | `"air"` / `"water"` | `{"cal_air":1}` / `{"cal_water":1}` — 큐브가 지금 센서값(10회 평균)을 그 보정값으로 저장 |
| `cal` | `"reset"` | `{"cal_reset":1}` — 펌웨어 config.h 초기값으로 되돌림 |

응답 `{"sn","sent","air","water","cal_pending","ads1115_ok"}`. 설정 프레임에는 순번·ack 가 없어서, 펌웨어가 적용 뒤 다시 발행하는 **status** 를 기다린다(`_pending_status`, `_resolve_status`). 측정 보정(`cal=air/water`)은 측정이 끝난 status(`cal_pending` 거짓)까지 기다린다(약 0.2초). 5초 안에 status 가 없으면 504(큐브 오프라인이거나 설정 프레임을 모르는 구형 펌웨어).

예:

```bash
curl -s -X POST http://api:8000/cubes/CUBE001/config -H 'Content-Type: application/json' -d '{"cal":"air"}'
```

확인한 것: `python -m py_compile` 통과. 젯슨 배포·왕복 시험은 하지 않았다. 인증이 없는 것은 변함없다(제어 라우트와 같은 조건).

### 젯슨 배포 (2026-09-28 12:57~12:58 KST, 사용자 실행 · 랩탑 ClaudeCode 확인)

- 사용자가 먼저 `docker compose up -d --build api` 만 실행 → 전 단계 `CACHED`, `Container api Running`(재시작 없음). 원인: 1단계(랩탑 `api/` → 젯슨 `/data/api/` 복사)가 빠져 소스가 09-22 그대로였음. **빌드 출력이 전부 CACHED 이고 마지막이 Running 이면 배포된 것이 아니다.**
- 랩탑 ClaudeCode 가 `main.py`·`README.md` 를 젯슨 `/tmp` 에 올리고(원격 쓰기는 세션 권한으로 막혀 설치는 사용자가 실행): 기존 파일 백업 `/data/api_backup_20260928/`, `install` 로 교체(md5 `209b74376110acb25aec8fb19901a802`), 같은 빌드 명령 재실행 → `COPY app ./app DONE`, `Container api Recreated → Started`, 이미지 `smartcube-api:1` 12:57:56.
- 확인: openapi 라우트 목록에 `/cubes/{sn}/config` 등장(공개 주소 경유), 기동 로그 브로커 접속·세션 이어받음 정상, `/health` ok, 빈 본문 POST → 400(라우트 살아 있음, 큐브로는 발행 없음).
- 아직 안 한 것: 원격 보정 왕복. 접속 중인 CUBE001 은 우노 연결판 펌웨어(`cube-cam-uno-1.0.0`)라 `cal` 요청은 504 가 정상. 새 펌웨어(`esp32/esp32_cam_node`) 실기 후 시험.

## 보완 (2026-09-28 오후, 랩탑 ClaudeCode / Fable 5.1) — v0.2: 인증(Supabase JWT) · InfluxDB 직접 기록 · 조작 기록

젯슨을 웹 서버로 두는 구조 개편(작업 일지 §30 이후 논의, 사용자 결정 09-28)에 따라 api 가 세 가지 일을 더 맡는다. **위 라우트 표와 본문은 고치지 않고 여기에 적는다.** 젯슨 배포는 아직이며 `deploy/deploy.sh` 로 한다.

### 바뀐 것

| 항목 | 내용 | 파일 |
|---|---|---|
| 인증 | 모든 라우트(`/health` 제외)가 `Authorization: Bearer <Supabase 접근 토큰>` 을 요구. 서명 키(JWKS 또는 HS256 비밀)로 토큰만 검사하고 Supabase 에 접속하지 않는다. 역할·소유 큐브는 토큰의 `app_metadata.role` / `app_metadata.cubes` (Supabase 접근 토큰 훅이 넣음) | `app/auth.py` |
| 권한 | 조회 = viewer, 제어·보정·refresh = operator + 해당 큐브 소유(admin 은 전부), `/admin/status` = admin. `disabled` 는 전부 403 | `app/auth.py`, `app/main.py` |
| 이력 기록 | 원본 토픽 수신 시 InfluxDB 에 직접 기록. measurement `mqtt`, 태그 `cube`·`kind`·`topic`·`host=jetson`, 숫자 필드는 float(참/거짓 → 1.0/0.0, null·문자열 제거) — telegraf 와 동일해 기존 이력·Grafana 가 이어짐. bridge·telegraf 컨테이너는 compose v3 에서 제거 | `app/writer.py` |
| 조작 기록 | control·config·refresh 요청과 결과를 Supabase `audit` 표에 비동기 기록(서비스 키). 실패해도 제어는 진행 | `app/audit.py` |
| 새 라우트 | `GET /me`(토큰의 나), `GET /admin/status`(큐브별 수신·기록기·감사 통계) | `app/main.py` |
| 이력 라우트 | `field=a,b,c` 로 여러 필드(최대 12) | `app/main.py` |
| 토큰 분리 | 조회 `INFLUX_READ_TOKEN`, 기록 `INFLUX_WRITE_TOKEN`(없으면 `INFLUX_TOKEN` 으로 동작) | compose v3 |
| 로그 | 시각 KST | `app/main.py` |
| 기동 조건 | `AUTH_MODE=required`(기본)인데 Supabase 설정이 없으면 **뜨지 않는다**. 개발·병행 시험은 `AUTH_MODE=off` 명시 | `app/main.py` lifespan |

### 환경변수 (추가)

`AUTH_MODE`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_JWT_SECRET`(구 프로젝트만), `INFLUX_WRITE_TOKEN`, `INFLUX_READ_TOKEN`, `WRITER_ENABLED`(기본 1), `TZ`. 목록과 설명: `config/env.v3.example`. Supabase 쪽 설정 절차: `supabase/README.md`, 표·훅 SQL: `supabase/2026-09-28_supabase_setup.sql`.

### 확인한 것

- 단위·라우트 시험 21개 통과(`python -m unittest -v tests.test_api`, 브로커·DB·Supabase 없이): 토큰 만료·서명 불일치·대상 불일치 401, viewer 제어 403, operator 비소유 403, operator 소유·admin 은 인증 통과(브로커 없어 503), disabled 전부 403, `/admin/status` admin 만, 이력 필드 검증, 기록기 변환 규칙과 telegraf 형식 일치.
- 의존성 추가: `PyJWT[crypto]`, `httpx`. Dockerfile 변경 없음.
- 젯슨 배포·왕복·병행 기록 비교는 아직. 순서는 `deploy/deploy.sh` 머리말과 작업 일지 계획대로.

## 보완 (2026-09-29, 랩탑 ClaudeCode / Fable 5.1) — v0.3 소유자 모델 → v0.4 다중 인스턴스(경로 분할)·큐브 등록

**위 라우트 표와 본문은 고치지 않고 여기에 적는다. 충돌 시 이 절이 우선.** 사용자 결정(2026-09-29): 이용자 = 큐브 소유자만, 역할 없음, 고객당 인스턴스 분리(api 하나·경로 분할), 회원가입 = 첫 구글 로그인 + 큐브 시리얼 번호(미리 등록된 큐브만), 운영자는 마스터 키로 인스턴스별 읽기만.

### v0.3.0 (09-29 오전, 09:31~12:54 젯슨에서 동작)

- 역할 3단계·관리 라우트 제거. 환경변수 `INSTANCE_ID` 하나 = 인스턴스 하나. 토큰 `app_metadata.instances` 에 그 ID 가 있으면 소유자(전부), 아니면 403. `app_metadata.operator` 계정은 `POST /operator/access {master_key, reason}` 로 60분 읽기. 시험 14개.

### v0.4.0 (09-29 오후, 12:54~ 젯슨에서 동작)

| 라우트 | 조건 | 하는 일 |
|---|---|---|
| `GET /health` | 공개 | 살아 있는지·브로커·등록부 크기·기록기 |
| `GET /me` | 로그인 | 내 소유 인스턴스(등록부에 있는 것만)·운영자 여부·살아 있는 읽기 접근 |
| `POST /claim {sn}` | 로그인 | SN → 인스턴스 소유 등록(소유자 비어 있을 때만). 404 모르는 SN / 409 이미 소유자 있음 / `already` 본인 |
| `GET /i/{iid}/me` | 로그인 | 이 인스턴스에 대한 접근·큐브 목록 |
| `POST/GET/DELETE /i/{iid}/operator/access` | 운영자 | 읽기 접근 발급/상태/반납 (인스턴스별) |
| `GET /i/{iid}/instance/status`, `/cubes`, `/cubes/{sn}/…` | 소유자·운영자 읽기 | 조회. 다른 인스턴스의 큐브는 404 |
| `POST /i/{iid}/cubes/{sn}/control|refresh|config` | 소유자 | 제어·갱신·보정 |

- 등록부 `app/registry.py`: Supabase `instances(id, owner_id, name)`·`cubes(sn, instance_id)` 를 기동 때 읽고 `REGISTRY_RELOAD_SECONDS`(60)마다 재적재. `claim` 은 서비스 키로 `PATCH instances?id=eq.X&owner_id=is.null`. 시험·off 모드는 `INSTANCES_STATIC="jetson-01:CUBE001;jetson-02:CUBE002"`.
- 환경변수: `INSTANCE_ID` 제거. `OPERATOR_MASTER_KEY`(값에 `$` 금지), `OPERATOR_GRANT_MINUTES`, `REGISTRY_RELOAD_SECONDS`. `SUPABASE_SERVICE_KEY` 는 이제 등록부 읽기에도 필요(required 모드에서 없으면 기동 거부).
- nginx: `/api/` → 전역, `location ~ "^/i/(…)/api/(.*)$"` → `/i/<iid>/…`. 스니펫은 `config/nginx/default.conf`(젯슨 현행).
- audit: 호출마다 `instance_id`, 동작 `claim`·`claim_denied` 추가.
- 시험 18개 통과(`python -m unittest -v tests.test_api`). 젯슨 배포·공개 주소 확인은 작업 일지 §31.12.


## 보완 (2026-09-29 오후 2, 랩탑 ClaudeCode / Fable 5.1) — v0.5 게이트웨이 자동 등록 · 인스턴스당 브로커 계정 · 장치 추가

**충돌 시 이 절 우선.** 사용자 결정 J66~J72(일지 §31.13).

| 라우트 | 조건 | 하는 일 |
|---|---|---|
| `POST /claim {sn}` | 로그인 | SN(CUBE+숫자 3자리) → 없는 SN 이면 인스턴스 `i-xxxxxxxx`·큐브·브로커 계정 자동 생성 후 소유. 있는 SN 은 409(소유자 비어 있는 옛 사전 등록분만 넘겨받음) |
| `POST /i/{iid}/cubes {sn}` | 소유자 | 이 인스턴스에 큐브 추가. 중복 409. 브로커 역할에 그 큐브 토픽 ACL 추가 |
| `GET /i/{iid}/broker` | 소유자 | 장치용 브로커 계정 `{username=iid, password, host, port, tls, app, ready, cubes}` (비밀 없으면 생성) |
| `POST /i/{iid}/broker/rotate` | 소유자 | 비밀번호 재발급(브로커 `setClientPassword` + `instance_secrets`) |

- `app/broker_admin.py`: mosquitto 동적 보안 플러그인 제어(`$CONTROL/dynamic-security/v1`). 역할 `inst-<iid>` ACL = `device/<앱>/<SN>/#`(publishClientSend·publishClientReceive·subscribePattern·unsubscribePattern) + `device/<앱>`(subscribeLiteral·unsubscribeLiteral·publishClientReceive). 응답 수 ≠ 명령 수면 실패(플러그인 없는 브로커 회신 오인 방지). `BROKER_ADMIN=0` 이면 전부 건너뜀(시험).
- 환경변수 추가: `BROKER_ADMIN`(1), `BROKER_PUBLIC_HOST`, `BROKER_PUBLIC_PORT`(8443). 브로커 전환은 `deploy/broker_dynsec_migrate.sh`(1회, 09-29 14:11 실행).
- Supabase: `instance_secrets` (SQL v4). audit 동작 `cube_add`·`cube_add_denied`·`broker_view`·`broker_rotate`.
- 시험 20개 통과. 젯슨 배포·브로커 전환·계정 자동 생성 확인은 일지 §31.13.


## 보완 (2026-09-29 오후 3, 랩탑 ClaudeCode / Fable 5.1) — v0.6 카메라: 스냅샷·즉시 촬영·HD 스트리밍 + 카메라 펌웨어 통신 규약

**충돌 시 이 절 우선.** 사용자 결정 J73~J77(일지 §31.14). 카메라 SN = `<큐브 SN>-C<번호>`(예 `CUBE001-C1`).

### 장치 라우트 (카메라 보드, HTTP Basic: 사용자명 = 인스턴스 ID, 비밀번호 = 인스턴스 브로커 비밀번호)

| 라우트 | 본문 | 응답 |
|---|---|---|
| `POST /dev/cameras/hello` | JSON `{sn, model?, sensor?, fw?}` | `{ok, instance_id, camera, cube, registered, snapshot_times, stream_max_s, stream_on}`. 처음이면 자동 등록(큐브가 이 인스턴스 것일 때만, 아니면 404) |
| `POST /dev/snapshots?cam=<SN>&trigger=periodic|request|device&req=<id>` | `image/jpeg` 원본(≤6MB, FFD8 로 시작) | `{ok, resolved, camera, cube, date, file, path, taken_at, trigger, bytes}`. `req` 가 있으면 기다리던 즉시 촬영 요청을 깨운다 |
| `POST /dev/frames?cam=<SN>` | `image/jpeg`(≤1MB) | `{ok, seq, viewers, stop}`. **`stop:true` 면 스트리밍을 멈춘다** |

### 사람 라우트

| 라우트 | 조건 | 하는 일 |
|---|---|---|
| `GET /i/{iid}/cameras` | 읽기 | 카메라 목록(큐브·번호·마지막 접속·status·최신 파일·스트림 상태) |
| `POST /i/{iid}/cameras {cube, number, name?}` | 소유자 | 카메라 추가(`<cube>-C<number>`) |
| `GET /i/{iid}/cubes/{sn}/snapshots?cam&date&limit` | 읽기 | 사진 목록(최신순) |
| `GET /i/{iid}/cubes/{sn}/snapshots/{cam}/{YYYY-MM-DD}/{file}` | 읽기 | 사진(jpg) 또는 사이드카(json) |
| `POST /i/{iid}/cubes/{sn}/snapshot {cam?}` | 소유자 | 즉시 촬영: `{"snap":1,"req":…}` 발행 → 업로드 대기(`SNAPSHOT_TIMEOUT` 25초, 초과 504). 카메라 한 대면 cam 생략 가능 |
| `POST /i/{iid}/cameras/{cam}/stream/start|stop` | 소유자 | `{"stream":1,"max_s":N}` / `{"stream":0}` 발행 |
| `GET /i/{iid}/cameras/{cam}/stream?frames=N` | 읽기 | MJPEG `multipart/x-mixed-replace; boundary=smartcubeframe`. 파트 헤더 `Content-Type: image/jpeg`, `Content-Length`, `X-Frame-Seq`. 프레임 없으면 10초마다 `text/plain` 길이 0 파트. `frames` 는 시험·curl 용 상한(0 = 무제한) |

### 카메라 펌웨어가 지킬 것

1. **MQTT**(`comm.cpp` 그대로 재사용 가능): 인스턴스 계정으로 8443 TLS, client id `device/helpai.cube/<카메라 SN>`, 구독 `device/helpai.cube/<카메라 SN>/notify/#` 과 그룹 `device/helpai.cube`. 접속 직후·그룹 요청 때 `status` 발행: `{"sn","fw","model","sensor","stream":0|1,"heap":N}`. 텔레메트리(`public`)는 보내지 않아도 된다(보내도 큐브 이력에 섞이지 않는다).
2. **명령**(`notify/config` 본문 JSON): `{"snap":1,"req":"<id>","trigger":"request"}` 또는 `{"snap":1,"trigger":"periodic"}` → 2048×1536 촬영 → `POST /api/dev/snapshots?cam=<SN>&trigger=<trigger>&req=<req>`. `{"stream":1,"max_s":N}` → HD(1280×720) 프레임을 `POST /api/dev/frames?cam=<SN>` 로 반복(연결 재사용 권장), 응답 `stop:true` 또는 `{"stream":0}` 이면 멈추고 스냅샷 해상도로 복귀. 서버 명령이 `max_s` 동안 없어도 스스로 멈춘다.
3. **HTTPS**: 브로커와 같은 루트 인증서(ISRG Root X1/X2), `Authorization: Basic base64(<인스턴스 ID>:<비밀번호>)`, `Content-Type: image/jpeg`. 부팅 때 `POST /api/dev/cameras/hello` 로 등록·설정 확인.
4. 실측할 것(계획서 §6): JPEG 크기(6MB 상한 안인지), 업로드 시간(25초 대기 안인지), TLS 2개 동시 힙, 브라운아웃.

- 모듈: `app/camera.py`. 환경변수 `SNAPSHOT_DIR`(/data/snapshots), `SNAPSHOT_TIMES`(09:00,21:00 KST), `SNAPSHOT_TIMEOUT`(25), `STREAM_MAX_SECONDS`(300), `STREAM_IDLE_SECONDS`(15), `SNAPSHOT_MAX_BYTES`, `FRAME_MAX_BYTES`, `SECRET_CACHE_SECONDS`(300).
- 시험 26개 통과. 젯슨 모의 시험(curl 가짜 JPEG) 통과 — 일지 §31.14.3. 실제 카메라 보드는 미검증.


## 보완 (2026-10-06, 랩탑 ClaudeCode / Opus 5.5) — v0.7 작물 기반 규칙 자동 제어

**충돌 시 이 절 우선.** 사용자 결정 J98~J108(일지 §37), 계획서 `../2026-10-06_작물기반자동제어_계획서_랩탑ClaudeCode.md` §12·§13. 현재 젯슨 = 0.7.1.

### 라우트

| 라우트 | 조건 | 하는 일 |
|---|---|---|
| `GET /i/{iid}/crops` | 읽기 | 작물표 `{crops:[…], invalid:{id: 사유}, file_error}` |
| `GET /i/{iid}/cubes/{sn}/auto` | 읽기 | `{sn, crop_id, crop, crop_invalid, enabled, available, interval_s, state}`. `state` = `{phase, rules, alerts, excursions, events, paused, fails, last_eval, last_action, pump_count, pump_last_at}`(자동이 켜진 큐브만) |
| `PUT /i/{iid}/cubes/{sn}/crop {crop_id}` | 소유자 | 작물 배정. `null` 이면 해제 + 자동 꺼짐. 작물표에 없는 ID 는 400 |
| `PUT /i/{iid}/cubes/{sn}/auto {enabled}` | 소유자 | 자동 제어 켜기/끄기(급수 포함). 작물이 없으면 409. 켜고 끌 때마다 판정 기억 초기화(일시 정지 해제) |
| `POST /i/{iid}/cubes/{sn}/control` | 소유자 | (기존) 그 큐브 자동 제어가 켜져 있으면 **끄고** 명령을 보낸다 |

Supabase 에 자동 제어 칸(SQL v6)이 없으면 `crop`·`auto` 쓰기는 503, 등록부는 예전 두 칸으로 계속 동작. `/cubes` 목록에 `crop_id`·`auto_mode`, `/health` 에 `auto` 가 붙는다.

### 모듈·설정

- `app/crops.json` — 작물표. 형식: `{"version":1,"crops":{"<작물 ID>":{"name","source","temp_day":{"min","max"},"temp_night","humidity_day","humidity_night","soil_moisture","photoperiod":{"on":"HH:MM","off":"HH:MM"},"lux_day"}}}`. 고치면 `deploy/deploy.sh api`.
- `app/crops.py` — 읽기·검사. 환경변수 `CROPS_FILE`(기본 `app/crops.json`).
- `app/autoctl.py` — 판정 함수 `evaluate()`, 루프 `run()`/`tick()`, 급수 펄스 끄기, 실패 정지. 환경변수 `AUTO_INTERVAL_SECONDS`(기본 10). 규칙 값은 파일 맨 위 상수(`FAN_STEPS` 0·33·66·100, `TEMP_BAND` 1.0, `HUM_BAND` 5.0, `FAN_HOLD_S` 120, `PUMP_PULSE_S` 5, `PUMP_WAIT_S` 3600, `PUMP_MAX_PER_DAY` 4, `LED_STEP` 10, `LED_DAY_START` 50, `STALE_S` 30, `RETRY_MIN_S` 30, `FAIL_PAUSE_AFTER` 5, 토양 공장 기본값 22520·11100).
- `main._send_control()` — 사람·자동 공용 송신(같은 순번 잠금). 자동 명령은 펌프를 포함해 `fan1`·`led1`·`pump` 를 직접 채우고, `fan2`·`led2` 는 장치 보고값으로 채운다. 보내기 직전 자동이 꺼졌으면 보내지 않는다(`skipped`).
- audit 동작: `auto_control`(email `auto`, user_id 없음, 내용 = 보낸 값·걸린 규칙·측정값·범위·시간대), `auto_off_manual`, `crop_set`, `auto_set`.

### 시험

- `python -m unittest -v tests.test_autoctl` — 22개 통과(작물표 검사, 주야 판정, 배기팬 단계·폭·유지·온도 우선, LED 단계·시작 밝기, 급수 조건·대기·횟수, 루프 송신·실패 정지·펄스 끄기, 라우트, 실제 송신 경로 프레임).
- `tests.test_api` 와 함께 49개 중 48개 통과. 실패 1개 `test_frames_and_mjpeg` 는 0.6.1 의 스트림 Content-Type 변경(`application/octet-stream`) 뒤 단언이 갱신되지 않은 것(이번 변경 전부터).
- 젯슨 실물(CUBE001, `rose`) 확인은 일지 §37.3.

## 보완 (2026-10-06 저녁, 랩탑 ClaudeCode / Opus 5.5) — v0.8 AI 리포트 창구

**충돌 시 이 절 우선.** 사용자 결정 J109~J115(일지 §38), 계획서 `../2026-10-06_리포트창구_10000경로_계획서v2_랩탑ClaudeCode.md` §1·§14·§15. 현재 젯슨 = 0.8.0.

### 라우트

GPU 서버용 — 10000 의 gpu-relay(`/report/`, Basic 인증 `report`)를 거친 요청만 받는다. 중계 확인 헤더 `X-Report-Relay` 가 `REPORT_RELAY_KEY` 와 다르면 403, 키가 비어 있으면 503. 443 에서는 web nginx 가 `/api/report/` 를 404 로 막는다.

| 라우트 | 하는 일 |
|---|---|
| `POST /report/jobs/{job_id}/result` | 결과 JSON(객체, `REPORT_MAX_BYTES` 기본 5 MB) 저장 → 회차 `done`. 이미 있으면 `.bak-<시각>` 로 백업 후 덮어씀 → `{ok, job_id, status, overwritten}`. 없는 작업 404, JSON 아님·배열 400, 너무 큼 413 |
| `POST /report/jobs/{job_id}/fail {reason}` | GPU 서버 생성 실패 → 회차 `failed`(재요청 가능). 이미 `done` 이면 409 |

대시보드용 — 443 `/i/<iid>/api/…`.

| 라우트 | 조건 | 하는 일 |
|---|---|---|
| `GET /i/{iid}/cubes/{sn}/report-settings` | 읽기 | `{sn, interval_days, min, max, anchor_time:"02:00", windows, last_anchor, next_anchor, next_period_start, llm_url_set}` |
| `PUT /i/{iid}/cubes/{sn}/report-settings {interval_days}` | 소유자 | 1~30 정수(아니면 422). 다음 기준 시각 = 마지막 회차 + 새 간격(회차가 없으면 다음 02:00). audit `report_settings` |
| `GET /i/{iid}/cubes/{sn}/reports?limit=` | 읽기 | 회차 목록(최신순, 시도 기록 제외) `{sn, reports, total}` |
| `GET /i/{iid}/cubes/{sn}/reports/{job_id}` | 읽기 | 회차 + 시도 기록 + `result`(완료 회차의 받은 JSON) |
| `POST /i/{iid}/cubes/{sn}/reports/{job_id}/retry` | 소유자 | `failed`·`no_result` 회차를 곧바로 한 번 보냄(그 회차 범위 그대로). 그 밖의 상태 409. audit `report_retry` |

### 일정·회차

- 기준 시각 02:00(KST). 요청 시간대 02:00~02:10, 다시 02:30~02:40, 1분 간격. 2xx = 접수. 시간대가 끝나도 접수되지 않으면 `failed`. 접수 뒤 6시간 결과가 없으면 `no_result`.
- 작업 ID = `<iid>.<cube>.<YYYYMMDD>T<HHMM>`(기준 시각). 데이터 범위 = 기준 시각 − 간격 ~ 기준 시각.
- 상태: `pending` → `sending` → `accepted` → `done` / `failed` / `no_result`. 재시작하면 `sending` 은 `pending` 으로 되돌린다.
- 여러 큐브·회차는 차례로 보낸다(전송 잠금 하나).
- 간격을 바꿔 다음 기준 시각이 이미 지났으면, 지난 회차들은 시간대가 지난 `failed` 로 만든다(재요청으로 받는다).

### GPU 서버로 보내는 본문

`{job_id, instance, cube, period_start, period_end, interval_days, timezone:"Asia/Seoul", result_url, fail_url, input}` — `input = {every, fields, aggregate:"mean", time_label:"window_stop", series:{항목:[[ISO 시각(KST), 값], …]}, series_error, auto_control:{crop:{crop_id, profile, auto_mode}, records:[{at, action, email, payload, result}], error}, generated_at}`. `REPORT_LLM_AUTH` 가 있으면 `Authorization` 헤더로 붙인다. 시간 초과 `REPORT_LLM_TIMEOUT`(기본 30초).

### 모듈·설정

- `app/report.py` — 일정 루프 `run()`(`REPORT_TICK_SECONDS` 기본 15)/`tick()`, 회차·설정 파일, 재료 `build_input()`(같은 회차는 메모리에 재사용), 송신 `send()`, 라우터 `router`.
- `main._report_series()` — InfluxDB `range(start, stop)` + `aggregateWindow(every, mean)`, `main._report_crop()` — 작물 배정. `report.bind(series=…, crop=…)`.
- 환경변수: `REPORT_DIR`(기본 `/data/reports`), `REPORT_LLM_URL`·`REPORT_LLM_AUTH`(팀원에게 받음, 비면 요청 실패로 기록), `REPORT_RESULT_BASE`(기본 `https://helphyai-jetson.tail3126a6.ts.net:10000/report`), `REPORT_RELAY_KEY`, `REPORT_EVERY`(기본 `10m`), `REPORT_FIELDS`(기본 `temperature,humidity,lux,moisture_pct,fan1,led1,pump`), `REPORT_MAX_BYTES`, `REPORT_LLM_TIMEOUT`, `REPORT_TICK_SECONDS`.
- 저장: `REPORT_DIR/<iid>/<cube>/settings.json`, `…/<YYYY-MM-DD>/<작업 ID>.job.json`, `…/<YYYY-MM-DD>/<작업 ID>.json`.
- 인스턴스 상태(`/i/{iid}/instance/status`)에 `report`(LLM 주소·중계 키 설정 여부, 상태별 회차 수, 전송 통계).

### 시험

- `python -m unittest -v tests.test_report` — 16개 통과(다음 02:00, 시간대별 1분 간격 10+10회 뒤 실패, 접수 뒤 재전송 없음, 6시간 결과 없음, 간격 변경·지난 회차, LLM 주소 없음, 파일 재적재, 중계 헤더, 덮어쓰기·백업, 검사 404/400/413, 생성 실패 → 재요청, 재요청 실패, 설정 라우트, 목록). GPU 서버는 `httpx.MockTransport` 가짜.
- 이 랩탑 기본 Python(3.14)에는 httpx 가 없다. 시험은 `uv run --no-project --python 3.12 --with-requirements requirements.txt python -m unittest tests.test_api tests.test_autoctl tests.test_report` 로 돌렸다 — 65개 중 64개 통과(`test_frames_and_mjpeg` 는 이전부터 실패).

## 보완 (2026-10-06 저녁, 랩탑 ClaudeCode / Opus 5.5) — v0.8.1 즉시 요청

**충돌 시 이 절 우선.** 사용자 결정 J116(일지 §39). 현재 젯슨 = 0.8.1.

| 라우트 | 조건 | 하는 일 |
|---|---|---|
| `POST /i/{iid}/cubes/{sn}/reports/now {start, end}` | 소유자 | 범위 [start, end) 로 즉시 회차를 만들어 곧바로 한 번 보낸다 → 회차(`kind:"now"`). 시작 < 끝이 아니면·끝이 지금 + 60초보다 뒤면·30일을 넘으면 400. 시간대 없는 시각은 한국 시간. 보내기 실패면 `failed`("요청 실패: …", 재요청 가능). audit `report_now` |

- 작업 ID: 정기 `<iid>.<cube>.<YYYYMMDD>T<HHMM>`, 즉시 `<iid>.<cube>.<YYYYMMDD>T<HHMMSS>`(요청 시각, 같은 초면 1초씩 뒤로).
- 회차 기록·목록·GPU 서버로 보내는 본문에 `kind`(`"scheduled"` | `"now"`). 이전에 만든 회차는 `scheduled` 로 본다.
- 즉시 회차는 정기 일정(settings)을 건드리지 않는다. 재요청·결과·실패 알림 라우트는 정기 회차와 같다.
- 시험: `tests.test_report` 18개 통과(즉시 요청 정상·검사·실패 2개 추가). 전체 67개 중 66개.

## 보완 (2026-10-07, 랩탑 ClaudeCode / Opus 5.5) — v0.9 AI 리포트 젯슨 작성(LangGraph)

**충돌 시 이 절 우선.** 위 v0.8·v0.8.1 절의 "GPU 서버가 작성해 10000 `/report/` 로 결과를 보내는 방식"을 대체한다. 사용자 결정 J119~J124(일지 §40), 계획서 `../2026-10-07_AI리포트_젯슨LangGraph_계획서v4_랩탑ClaudeCode.md` §1·§15·§16. 현재 젯슨 = 0.9.0.

- GPU 서버의 llama-server(OpenAI 호환)는 **추론만**. 재료 만들기·LangGraph·보고서 작성은 이 api 안.
- **지운 것**: `POST /report/jobs/{id}/result`·`/fail`(이제 404), 중계 확인 헤더 검사, 환경변수 `REPORT_LLM_AUTH`·`REPORT_RESULT_BASE`·`REPORT_RELAY_KEY`·`REPORT_MAX_BYTES`, 상태 `sending`·`accepted`·`no_result`.

### 라우트 (443 `/i/<iid>/api/…`)

| 라우트 | 조건 | 하는 일 |
|---|---|---|
| `GET /i/{iid}/cubes/{sn}/report-settings` | 읽기 | `{sn, interval_days, min, max, anchor_time:"02:00", windows, last_anchor, next_anchor, next_period_start, now_max_hours:24, llm_ready, graph}` |
| `PUT /i/{iid}/cubes/{sn}/report-settings {interval_days}` | 소유자 | 1~30 정수. 다음 기준 시각 = 마지막 회차 + 새 간격. audit `report_settings` |
| `GET /i/{iid}/cubes/{sn}/reports?limit=` | 읽기 | 회차 목록(기준 시각·범위 끝 최신순) `{sn, reports, total}`. 회차에 `status`·`progress`·`part`·`kind` |
| `GET /i/{iid}/cubes/{sn}/reports/{job_id}` | 읽기 | 회차 + 시도 기록(`attempts[].calls` = 단계별 초·토큰·`finish_reason`) + `result`(완료 회차) |
| `POST /i/{iid}/cubes/{sn}/reports/now {start, end}` | 소유자 | 범위 [start, end)(**최대 24시간**)로 즉시 회차를 만들어 **대기에 넣고 곧바로 깨운다** → 회차(`pending`). 시작 < 끝이 아니면·끝이 지금 + 60초보다 뒤면·24시간을 넘으면 400. audit `report_now`(`queued`) |
| `POST /i/{iid}/cubes/{sn}/reports/{job_id}/retry` | 소유자 | `failed` 회차만 대기에 넣는다(그 범위 그대로) → `pending`. 그 밖의 상태 409. audit `report_retry`(`queued`) |

### 일정·회차

- 기준 시각 02:00(KST). 정기 회차는 02:00~02:10, 다시 02:30~02:40 에 1분 간격으로 시도. **성공 = 그래프가 끝까지 완성.** 시간대가 끝날 때 작성 중이면 중단(`asyncio.timeout`, 시도 `kind:"window_end"`) → 다음 시간대에 처음부터. 두 시간대 안에 완성되지 않으면 `failed`.
- 컨텍스트 초과(llama-server 400 "exceeds the available context size") → 그 회차는 곧바로 `failed`(`kind:"context"`), 시간대 안 반복 없음.
- 간격이 N일이면 기준 시각에 **하루치 회차 N개**(오래된 날부터, 작업 ID = 범위 끝 `<iid>.<cube>.<YYYYMMDD>T0200`, `part:[i, N]`). 못 끝낸 하루치만 다음 시간대에.
- 한 번에 하나씩(잠금 하나). 사람 요청(받기·재요청)을 정기 회차보다 먼저. 일정 루프는 `REPORT_TICK_SECONDS`(15초)마다 + 요청 때 곧바로.
- 상태: `pending` → `writing`(`progress:{step, total, name, label}`) → `done` / `failed`. api 재시작 때 `writing` 이던 정기 회차는 `pending`, 사람 요청은 `failed`("api 재시작으로 작성 중단"). 이전 판 파일의 `sending` → `pending`, `accepted`·`no_result` → `failed`.

### 재료 텍스트 (`app/report_material.py`)

머리말(`기간 … (KST), 10m 평균, 시각 = 집계 창 끝`) → 항목 설명(`temperature: 온도(°C)` … `pump: 10분 중 급수가 켜져 있던 비율(0~1)`) → `[센서]` CSV(`time` = `MM-DD HH:MM` KST, 값 `%.2f`, 빈 값 `-`) → `[작물]`(작물 정보 압축 JSON) → `[자동 제어 기록]` CSV `time,action,sent,rules,phase,result`(사람 조작은 `action(사람)`, 이메일·그때의 센서 값·목표 범위·요청한 값 제외, `sent` = `키=값`, `rules` = `; `). 센서 값 조회 실패 = 그 시도 실패, 기록 조회 실패 = "가져오지 못함" 한 줄. 같은 회차의 재시도에는 메모리에 둔 재료를 다시 쓴다.

### 그래프 (`app/report_graph.py`)

- `STEPS`(단계 목록) → LangGraph `StateGraph` 노드를 순서대로. 단계마다 진행 콜백(회차 `progress`).
- LLM = `langchain-openai` `ChatOpenAI(base_url=REPORT_LLM_URL, api_key=HELPAI_LLM_KEY, model="local", temperature=0.2, max_tokens=REPORT_LLM_MAX_TOKENS, timeout=REPORT_LLM_TIMEOUT, max_retries=0, extra_body={"chat_template_kwargs": {"enable_thinking": False}})`.
- **지금 `STEPS` = 임시 1단계 `draft`**(리포트 내용은 팀 회의 뒤 결정, J123). 시간 초과 300초·`max_tokens` 1500 도 임시(가이드 예시).
- 결과 `<작업 ID>.json` = `{job_id, instance, cube, kind, period_start, period_end, generated_at, graph, steps:{…}, llm:{calls, truncated}}`. 다시 쓰면 `.bak-<시각>` 백업. 보낸 재료 = `<작업 ID>.input.txt`.
- `LANGSMITH_TRACING` 은 코드 기본 `false` + compose 에 명시.

### 모듈·설정

- `app/report.py` — 일정 루프 `run()`/`tick()`, 회차·설정 파일, 재료 `build_input()`, 작성 `attempt()`, 라우터.
- `main._report_series()` — 리포트 전용 InfluxDB 클라이언트(`_influx_report_client`, **시간 초과 60초**). 다른 조회는 기본 10초.
- 환경변수: `REPORT_DIR`(`/data/reports`), `REPORT_LLM_URL`(`http://210.125.70.71:31194/v1`), `HELPAI_LLM_KEY`(값은 젯슨 `/data/.env` 에만), `REPORT_EVERY`(`10m`), `REPORT_FIELDS`, `REPORT_LLM_TIMEOUT`(300), `REPORT_LLM_MAX_TOKENS`(1500), `REPORT_TICK_SECONDS`(15), `LANGSMITH_TRACING`(`false`). 주소·키 중 하나라도 비면 작성하지 않고 시도 실패로 기록.
- 의존성: `langgraph==1.2.14`, `langchain-openai==1.6.7`(aarch64 완성본 있음).
- 인스턴스 상태의 `report` = `{dir, llm_ready, graph, every, fields, jobs(상태별 수), running, loaded, attempts, done, failed_attempts, last_error, last_tick, current}`.

### 시험

- `python -m unittest -v tests.test_report` — 24개 통과(가짜 LLM). 전체 73개 중 72개(`test_frames_and_mjpeg` 는 이전부터 실패). 모듈별로 따로 돌린다(같은 프로세스에서 섞으면 환경변수 기본값이 엇갈림).
- 이 랩탑 기본 Python(3.14)에는 의존성이 없다. Python 3.12 가상환경(`uv venv --python 3.12` + `uv pip install -r requirements.txt`)에서 `PYTHONIOENCODING=utf-8` 로 돌렸다.
- 젯슨 실물: 일지 §40.5·§40.6(받기 1건·재요청 2건 완료, 입력 1,559~4,293·출력 약 1,000토큰, LLM 18~25초).
