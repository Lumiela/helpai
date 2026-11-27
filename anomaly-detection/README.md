# anomaly-detection

스마트큐브 CUBE001 의 센서 데이터 분석과 자동 제어 모델 시험 기록이다. 작성: 랩탑 ClaudeCode (Claude Opus 5.5).

| 문서 | 내용 | 코드·결과 |
|---|---|---|
| [docs/sensor-analysis.md](docs/sensor-analysis.md) | 설비 기준 센서 반응, 24시간 센서 간 상관, 55개 항목 분류, 같은 종류 센서 비교, 토양수분 보정값 (2026-11-27) | [corr-test/](corr-test/) |
| [docs/auto-control-model-tests.md](docs/auto-control-model-tests.md) | Laya(의사결정 모델) 범위 판정 시험, Chronos-2(시계열 예측 모델) 온습도 예측 시험 (2026-11-25 ~ 26) | [laya-test/](laya-test/) · [chronos-test/](chronos-test/) |

- 센서 기록은 네이버 클라우드 서버 InfluxDB 의 CUBE001 값을 1분·10분 평균으로 받은 것이다.
- 모델 파일과 실행 환경은 올리지 않았다.
