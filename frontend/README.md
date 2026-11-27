<div align="center">
<img width="1200" height="475" alt="GHBanner" src="https://ai.google.dev/static/site-assets/images/share-ais-513315318.png" />
</div>

# Run and deploy your AI Studio app

This contains everything you need to run your app locally.

View your app in AI Studio: https://ai.studio/apps/6777060a-df20-487e-8a18-775db980756b

## Run Locally

**Prerequisites:**  Node.js


1. Install dependencies:
   `npm install`
2. Set the `GEMINI_API_KEY` in [.env.local](.env.local) to your Gemini API key
3. Run the app:
   `npm run dev`

## 1. 현재 실행·검증 안내 (2026-09-14, Codex)

이 절은 상단의 기존 AI Studio 안내를 보완합니다. **충돌 시 하단(최신) 우선**입니다.

현재 앱은 스마트큐브 관제 화면을 시연하는 브라우저 데모입니다. Gemini API 호출과 서버 기능은 없으며 API 키·필수 환경변수 없이 실행합니다. 실제 장치 통신과 생육 카메라는 연동하지 않았습니다.

1. Node.js 설치 후 `npm install`로 의존성을 설치합니다.
2. `npm run dev`로 개발 서버를 실행합니다. 기본 포트는 3000입니다.
3. `npm run lint`로 타입 검사, `npm test`로 회귀 테스트를 실행합니다.
4. `npm run build`로 배포용 `dist`를 생성합니다. 소스 변경 후에는 마지막 수정 기준으로 다시 빌드합니다.

자동 모드는 기기의 현지 시각과 작물별 광주기로 주·야간 목표 온도 및 LED 점등을 결정합니다. 팬·CO₂는 저장된 자동 설정을 복원합니다. 수동 주·야간 선택은 LED를 즉시 점등·소등하며, 수동 밝기 조절은 별도로 가능합니다.

그래프는 모형으로 만든 24시간 데모 이력으로 시작합니다. 화면을 열어둔 동안 15분마다 표본을 보존하고 현재값·시간축을 갱신합니다. 화면을 다시 열거나 24시간 이상 갱신이 중단되면 데모 이력을 새로 생성합니다.

프리셋은 기존 `smartcube-presets-v1` 브라우저 저장소를 유지하며 활성 항목을 고유 ID로 구분합니다. 장치 상태·알림·그래프 표본은 서버에 저장되지 않습니다.

외부 업로드는 별도 작업입니다. `npm run deploy`는 Cloudflare 업로드를 실행하므로 로컬 검증에는 사용하지 않습니다.

## 2. 보완 (2026-10-06 저녁, 랩탑 ClaudeCode / Opus 5.5) — AI 리포트 탭

**충돌 시 하단(최신) 우선.** 사용자 결정 J114(`../edgeserver-JS/2026_09_16 엣지 서버 작업 일지.md` §38). 이 화면은 젯슨 api(0.8.0)에 붙어 동작한다(§1 의 "서버 기능 없음" 안내는 그 뒤 구성이 바뀐 것).

- **개발자 모드가 켜졌을 때만** 메뉴에 "AI 리포트"(`/i/<iid>/reports`)가 나온다. 꺼지면 Home 으로 돌아간다.
- 큐브를 고르면 리포트 간격(1~30일, 소유자만 저장), 다음 리포트 시각·담길 데이터, 회차 목록(작성 시각·데이터 범위·상태·시도·비고)을 보여 준다. 실패·결과 없음 회차는 재요청, 완료 회차는 **열기**로 받은 JSON 을 들여쓰기해 보여 준다. 30초마다 새로 고침.
- 파일: `src/components/ReportsPanel.tsx`, `src/lib/backend.ts`(리포트 함수), `src/lib/routes.ts`(탭 `reports`).

## 3. 보완 (2026-10-06 저녁, 랩탑 ClaudeCode / Opus 5.5) — AI 리포트 "받기"(즉시 요청)

**충돌 시 하단(최신) 우선.** 사용자 결정 J116(`../edgeserver-JS/2026_09_16 엣지 서버 작업 일지.md` §39).

- AI 리포트 탭 맨 위: 시작·끝 시각(한국 시간) + **"받기"** — 그 범위로 GPU 서버 LLM 에 즉시 작성 요청(소유자만). 정기 회차 일정에는 영향 없음.
- 회차 목록에 즉시 회차가 '즉시' 표시와 함께 나오고, 재요청·열기는 정기 회차와 같다.
- 개발 단계 확인용(개발자 모드 전용). 나중에 사용자가 받는 것은 서버가 템플릿으로 변환한 HTML 이다(사용자 설명).

## 4. 보완 (2026-10-07, 랩탑 ClaudeCode / Opus 5.5) — AI 리포트 젯슨 작성 방식

**충돌 시 하단(최신) 우선.** 사용자 결정 J119~J124(`../edgeserver-JS/2026_09_16 엣지 서버 작업 일지.md` §40). 젯슨 api 0.9.0 과 함께 배포(번들 `index-Dl_bVeJq.js`).

- 리포트는 젯슨이 직접 작성한다(GPU 서버 LLM 은 추론만). 그래서 "접수됨"·"결과 없음" 상태가 없어지고 **대기·작성 중·완료·실패** 네 가지다. 작성 중이면 **"작성 중 (1/1 단계)"** 처럼 그래프 진행 단계를 함께 보여 준다.
- 간격이 1일보다 길면 하루치씩 나눠 회차가 따로 생기고, 데이터 범위 옆에 `1/3` 처럼 표시한다.
- "받기" 범위는 **최대 24시간**. 넘거나 시작이 끝보다 늦으면 입력 옆에 안내가 뜨고 버튼이 잠긴다. 받기·재요청은 곧바로 "대기"로 들어가고, 차례가 되면 작성된다.
- 사람이 넣은 요청이 대기·작성 중이면 5초마다, 아니면 30초마다 새로 고침.
- LLM 서버 주소·키가 없으면 "LLM 서버 주소·키가 설정되지 않아 리포트를 작성하지 못합니다." 안내. 재요청은 실패 회차만. 완료 회차 **열기** = 결과 JSON(지금은 임시 1단계 그래프의 출력 — 리포트 내용은 팀 회의 뒤 결정).
- 파일: `src/components/ReportsPanel.tsx`, `src/lib/backend.ts`(리포트 타입·함수). 시험 61개·tsc 통과.

## 5. 보완 (2026-11-25, 랩탑 ClaudeCode / Opus 5.5) — 팀원 시안과 fr 합치기, AWS 배포

**충돌 시 하단(최신) 우선.** 이 폴더 `fr_261125` 는 팀원 시안 `../스마트큐브_시안_261125` 와 `../fr`(11-18 까지의 기능)을 합친 소스다. 두 원본은 그대로 두었고, 네이버 클라우드 화면은 지금도 `../fr` 로 빌드한 것이다.

사용자 결정(2026-11-25):

- 합쳐서 적용, 합친 소스는 새 폴더.
- 주소를 나누지 않고 화면 폭에 따라 바뀐다: 767px 이하 = 모바일 앱, 768px 이상 = 웹 대시보드. 기존 관제 화면(Home·큐브 상세)과 고르는 화면은 뺀다. 예전 주소 `/m`·`/desk`·`/start` 는 첫 화면으로 간다.
- 장치 연결·알림 이력·프리셋(개발자 도구가 켜지면 AI 리포트·알림 API 테스트도)은 지금 모양 그대로 두고 새 화면 메뉴에서 들어간다. 모바일 메뉴에 로그아웃.
- 기록 화면에 EC·CO₂ 그래프와 지금 값, pH 지금 값.
- 웹 화면은 큐브가 2대 이상일 때 큐브를 고른다.

바뀐 파일(시안 기준): `src/App.tsx`, `src/desk/*`, `src/app2/{SmartCubeApp,Screens,shared}.tsx`·`app2.css`, `index.html`, 새 파일 `src/lib/{useWideScreen,appMenu,extraMetrics}.ts`·`src/components/EmptyCubes.tsx`. fr 에서 가져온 것: `EdgeDevicesList`·`QrCode`·`CameraLab`·`Dropdown`·`Header`·`GreenhouseOverview`·`InstancesScreen`·`LoginScreen`·`AuthContext`·`backend`·`deviceMap`(시안의 `controlDiff` 를 더함)·`instancePath`·`routes`·개발용도구. 뺀 것: `src/mobile/EntryScreen.tsx`, `preview-reports.*`, `실행방법.txt`.

확인: `tsc` 통과, 시험 68개 통과, 가짜 API 화면 확인(미리보기 `smartcube-fr261125-mock`).

AWS 배포(`npx vite build --mode aws --outDir dist-aws`, 10:37): 화면 `index-DV5Vqgpi.js`, 이전 화면 백업 `/data/backup/web-20261125-before-design`.

**임시**: AWS 화면의 api 는 네이버 클라우드(`smartcubens.duckdns.org`)를 AWS nginx 가 중계한다(조회·조작 모두, 사용자 결정). 네이버 api 가 로그인 꺼짐이라 AWS 화면도 로그인 없이 열린다. 장치가 쓰는 `/api/dev/`·8443 은 AWS 그대로다. 되돌리기(AWS 에서, 화면 재빌드 필요 없음):

```bash
cp /data/nginx/default.conf.bak-20261125-before-naver-api /data/nginx/default.conf && docker exec web nginx -t && docker exec web nginx -s reload
```

## 6. 보완 (2026-11-25, 랩탑 ClaudeCode / Opus 5.5) — AWS 화면은 조회 전용(조작 명령 차단)

**충돌 시 하단(최신) 우선.** 사용자 지시 "aws 서버의 조작 명령은 실제 큐브에 닿지 않게". §5 의 "조회·조작 모두"를 이 절이 대신한다.

- AWS nginx 의 네이버 중계 두 곳(`/api/`, `/i/<iid>/api/`)에서 GET·HEAD 이외의 요청은 네이버로 보내지 않고 403 으로 끝낸다(10:41). 장치로 명령을 내보내는 api 는 전부 POST·PUT·DELETE 다 — 제어·자동 제어·작물 변경·연결 초기화·장치 제거·장치 추가·촬영·실시간 영상 시작/정지·프리셋 적용 등이 AWS 화면에서 동작하지 않는다. 화면에는 실패 안내가 뜬다.
- 확인: 없는 인스턴스·없는 큐브를 대상으로 POST·PUT·DELETE·PATCH 12가지와 우회 경로(`/api/i/…`, `/api/dev/../`, `%2f`) 전부 403, 조회·실시간 값은 정상. 이전 설정을 쓰던 작업 프로세스 1개도 종료. 네이버 기록상 중계를 연 10:37 이후 AWS 에서 온 요청은 모두 GET.
- 화면 코드는 바꾸지 않았다. 차단 전 설정(조작 가능)은 `/data/nginx/default.conf.bak-20261125-before-readonly`, AWS 자체 api 로 되돌리는 설정은 §5 와 같다.

## 7. 보완 (2026-11-25, 랩탑 ClaudeCode / Opus 5.5) — 이력 그래프 꼬임 수정

**충돌 시 하단(최신) 우선.** 사용자 "그래프가 왜 이렇지?" → 원인 설명 뒤 "고쳐줘."

- 원인: ① CUBE001 기록이 11-19 11시~11-24 21시(130시간)와 11-24 23시~11-25 09시(10시간) 비어 있는데 두 점을 이어 그림 ② 시안의 곡선 식(카디널 스플라인)이 점 간격이 일정하다고 보아 빈 구간 양쪽에서 시간을 거꾸로 감.
- 수정: 새 파일 `src/lib/chartPath.ts`(시험 `chartPath.test.ts`). 점 사이가 집계 간격의 2.5배를 넘으면 선을 끊고, 곡선은 단조 3차(Fritsch–Carlson)로 바꿈. `src/app2/shared.tsx`·`src/desk/DeskDashboard.tsx` 의 그래프가 같이 쓴다.
- 확인: `tsc` 통과, 시험 73개 통과(꼬였던 실제 기록 포함). AWS 배포 10:59, 화면 `index-Dgv8yUAo.js`, 이전 화면 백업 `/data/backup/web-20261125-before-chartfix`. nginx(조회 전용 중계)는 그대로.
