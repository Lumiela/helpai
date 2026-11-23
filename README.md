# 작물 모니터링 파이프라인 (LangGraph)

천장 카메라 2대(CL, CR)와 작물 ID를 입력받아 작물별 모듈로 **생장 단계**와 **잎 이상 상태**를 판정하고, 두 카메라 스테레오로 **높이**를 측정해 트레이 칸별 보고서를 만듭니다.

```
preprocess ─┬─(작물 등록됨)─┬─ growth_stage ─┐  생장 단계 (작물별 모듈)
(이미지 읽기) │               ├─ disease ──────┼─ merge ─ report
            │               └─ height ───────┘  잎 이상 (작물별 모듈) / 높이 (공통)
            └─(미등록 작물)── unknown_crop
```

## 모듈

| 모듈 | 원본 | 위치 | 하는 일 |
|---|---|---|---|
| 높이 (공통) | helpai-Height `stereo_pair.py` | `modules/height/` | 정렬 → SGBM 시차 → 거리(cm) → 기준면(RANSAC) 대비 높이 지도 |
| 장미 생장 단계 | helpai-Rose_Growth `rose_pipeline` | `modules/rose_growth/` | 트레이 칸 나누기 → 봉오리 검출(YOLOv8m) → 등급 분류(YOLO26m-cls) → Claude 검수·누락 추가 |
| 장미 이상 상태 | helpai-Rose_Disease `leafdx` (`rose_leaf_colab_keyfile.ipynb`) | `modules/rose_disease/` | 잎 탐지 → 잎별 상태 분류 → (선택) VLM 재확인 |

각 모듈의 원본 코드는 거의 그대로 두고, `adapter.py` 가 그래프와 연결합니다. 원본에서 바꾼 곳은 경로 기준(프로젝트 루트)과 API 키 위치(`secrets/`)뿐입니다.

**병합 기준은 트레이 칸(R1C1 ~ R3C4)** 입니다.
- 생장 단계: 칸별 최종 등급 (두 카메라 중 높은 등급)
- 높이: 스테레오 왼쪽 카메라에서 그 칸 식물 마스크 영역의 상위 5% 높이
- 잎 이상: 카메라별 잎 박스 중심이 그 카메라의 칸 식물 마스크 안에 있으면 그 칸의 잎

## 설치

```powershell
uv sync
```

## API 키 (Claude)

`secrets/ANTHROPIC_API_KEY` 파일에 키 한 줄(`sk-ant-...`)을 넣으세요. 메모장에서 `.txt` 가 붙어도 됩니다. 이 폴더는 git 에 올라가지 않습니다.

- 생장 단계: 키가 있으면 원본 설정대로 Claude 검수·누락 추가를 씁니다. 키가 없으면 그 단계를 끄고 YOLO 결과만 씁니다 (보고서 경고에 표시).
- 잎 이상 VLM 재확인: 요금이 들어서 기본으로 꺼 두었습니다. `config/crops.yaml` 의 `disease.vlm.enabled: true` 로 켭니다.

## 실행

```powershell
uv run python scripts/run.py --crop A --CL data/samples/rose_CL.jpg --CR data/samples/rose_CR.jpg --out out/run
```

`--out` 을 주면 `out/run/<시각>/` 에 `report.md`, `crop_data.json`, 모듈별 시각화(`growth/`, `disease/`, `height/`)가 저장됩니다.

## 설정 파일

| 파일 | 내용 |
|---|---|
| `config/crops.yaml` | 카메라 이름·스테레오 좌우, 높이 모듈 설정, 작물 레지스트리 |
| `config/rules/A.yaml` | 장미 등급 라벨·정상 높이 범위, 잎 이상 유형별 심각도, 경고 기준 |
| `config/rose/growth.yaml`, `holes.yaml` | 생장 단계 원본 설정 (트레이 배치, 검출·등급·Claude 검수) |
| `config/rose/disease.json` | 잎 이상 원본 설정 (잎 탐지·분류 임계값) |
| `config/stereo/stereo_calib.npz` | 스테레오 보정 (2026-11-11, 33쌍, 간격 33.3mm) |

### 스테레오 좌우 주의

`stereo_calib.npz` 의 왼쪽 = 천장 C2 입니다. 생장 단계 샘플 사진으로 확인해 보니 **생장 단계 모듈의 CR 이 스테레오 왼쪽**이어서 `cameras.stereo_left: CR` 로 두었습니다.
보고서에 "정렬 후 세로 어긋남" 경고가 나오면 카메라가 움직였거나 좌우가 바뀐 것이니 다시 보정하세요.

## 스테레오 다시 보정하기

```powershell
uv run python scripts/height/collect_pairs.py --since 2026-11-24T10:00 --out calib
uv run python scripts/height/stereo_calibrate.py --dir calib --board 9x6 --square 24.2 --width 640 --out config/stereo/stereo_calib.npz
```

## 작물 추가 (작물 N)

1. `src/crop_monitor/modules/<모듈>/adapter.py` 작성 — `run(images, spec, out_dir) -> (결과, 경고)` (장미 어댑터와 같은 출력 형식)
2. `config/crops.yaml` 에 작물 항목 추가 (`growth.module`, `disease.module`)
3. `config/rules/N.yaml` 작성

그래프 코드는 수정하지 않습니다.

## 테스트

```powershell
uv run pytest                  # 전체 (실제 모델로 그래프 실행 포함, 약 1~2분)
uv run pytest -m "not slow"    # 빠른 테스트만
```
