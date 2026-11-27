# 작물 모니터링 파이프라인 (LangGraph)

천장 카메라 2대(CL, CR)와 작물 ID를 입력받아 작물별 모듈로 **생장 단계**와 **잎 이상 상태**를 판정하고, 두 카메라 스테레오로 **높이**를 측정해 트레이 칸별 보고서를 만듭니다.

```
preprocess ─┬─(작물 등록됨)─┬─ growth_stage ─┐  생장 단계 (작물별 모듈)
(이미지 읽기) │               ├─ disease ──────┼─ merge ─ report
            │               └─ height ───────┘  잎 이상 (작물별 모듈) / 높이 (공통)
            └─(미등록 작물)── unknown_crop
```

> **2026-11-27 추가**
> - **구멍(화분) 위치 자동 찾기**: 실행마다 사진에서 트레이 격자를 찾아 구멍 12개 위치를 정함 (트레이·카메라가 움직여도 됨)
> - **빈 화분 판정 개선**: 채도 조건 추가 (검은 덮개 = 무채색, 흙 = 갈색) → 사진 3세트 36칸 모두 맞음
> - **스테레오 보정 관리 (A 체커보드 재보정 + B 자동 미세 조정)**: 실행 중 세로 어긋남을 B 로 자동 보정, 이력 · 되돌리기, `calib_manager.py`
>
> 아래 "보정 관리", "구멍 위치 자동 찾기" 참고.

## 모듈

| 모듈 | 원본 | 위치 | 하는 일 |
|---|---|---|---|
| 높이 (공통) | helpai-Height `stereo_pair.py` | `modules/height/`, `calibration/` | 정렬 → SGBM 시차 → 거리(cm) → 기준면(RANSAC) 대비 높이 지도. 세로 어긋남이 크면 자동 미세 조정(B) 후 다시 계산 |
| 장미 생장 단계 | helpai-Rose_Growth `rose_pipeline` | `modules/rose_growth/` | 구멍 위치 자동 찾기 → 빈 화분 판정 → 트레이 칸 나누기 → 봉오리 검출(YOLOv8m) → 등급 분류(YOLO26m-cls) → Claude 검수·누락 추가 |
| 장미 이상 상태 | helpai-Rose_Disease `leafdx` (`rose_leaf_colab_keyfile.ipynb`) | `modules/rose_disease/` | 잎 탐지 → 잎별 상태 분류 → VLM 재확인 (켜져 있음) |

각 모듈의 원본 코드는 거의 그대로 두고, `adapter.py` 가 그래프와 연결합니다. 원본에서 바꾼 곳은 경로 기준(프로젝트 루트)과 API 키 위치(`secrets/`)뿐입니다.

**병합 기준은 트레이 칸(R1C1 ~ R3C4)** 입니다.
- 생장 단계: 칸별 최종 등급 (두 카메라 중 높은 등급)
- 높이: 스테레오 왼쪽 카메라에서 그 칸 식물 마스크 영역의 상위 5% 높이
- 잎 이상: 카메라별 잎 박스 중심이 그 카메라의 칸 식물 마스크 안에 있으면 그 칸의 잎

**판정** (`config/rules/A.yaml`): 칸마다 정상 · 주의 · 위험을 정하고, 종합 상태는 가장 나쁜 칸 (빈 칸 제외).
- 주의: 이상 잎 1장 이상, UNKNOWN(판단 보류) 잎이 있음, 또는 높이가 단계별 정상 범위 밖
- 위험: 한 칸의 이상 잎이 `critical_leaf_count`(3) 이상. 두 카메라가 같은 잎을 찍을 수 있어 **카메라별로 센 값 중 큰 값**을 씀
- VLM 재확인 판정을 잎 상태에 반영한 뒤 판정합니다 (`alerts.vlm_apply`, `[]` 이면 참고용으로만 저장)
  - `FALSE_ALARM`(모델은 병, VLM 은 정상) → 정상(FRESH)
  - `MISSED`(모델은 정상, VLM 은 병) · `OTHER_CLASS`(둘 다 병, 종류가 다름) → VLM 이 본 병
  - `NOT_A_LEAF`(잎이 아님) → 판정에서 뺌

## 설치

```powershell
uv sync
```

## API 키 (Claude)

`secrets/ANTHROPIC_API_KEY` 파일에 키 한 줄(`sk-ant-...`)을 넣으세요. 메모장에서 `.txt` 가 붙어도 됩니다. 이 폴더는 git 에 올라가지 않습니다.

- 생장 단계: 키가 있으면 원본 설정대로 Claude 검수·누락 추가를 씁니다. 키가 없으면 그 단계를 끄고 YOLO 결과만 씁니다 (보고서 경고에 표시). Claude 에게는 `weights/rose/growth/vlm_examples/<클래스>/` 의 예시 사진(not_bud, grade_one ~ grade_four 각 3장)을 함께 보냅니다 (`vlm.examples_per_class: 3`).
- 잎 이상 VLM 재확인: **켜져 있습니다** (`config/crops.yaml` 의 `disease.vlm.enabled`). 이상 · UNKNOWN 잎 전부와 정상 잎 5장을 사진마다 최대 40장까지 Claude 에게 다시 묻습니다. 장미 샘플 1회 약 $0.28. 키가 없으면 이 모듈이 실패하니 `enabled: false` 로 끄세요. VLM 의 판정 · 증상 · 이유는 `crop_data.json` 에 저장되고, VLM 판정으로 상태가 바뀐 잎은 보고서의 "VLM 재확인으로 바뀐 잎" 표에 나옵니다 (모델 판정과 함께). 보고서의 "잎 상태" 칸은 VLM 반영 후 개수입니다 (VLM 적용 전 개수는 `crop_data.json` 의 `model_counts`).

## 실행

```powershell
uv run python scripts/run.py --crop A --CL data/samples/rose_CL.jpg --CR data/samples/rose_CR.jpg --out out/run
```

`--out` 을 주면 `out/run/<시각>/` 에 `report.md`, `crop_data.json`, 모듈별 시각화(`growth/`, `disease/`, `height/`)가 저장됩니다.
구멍 자동 찾기 확인 그림은 `growth/holes_<카메라>.jpg` 입니다.

## 설정 파일

| 파일 | 내용 |
|---|---|
| `config/crops.yaml` | 카메라 이름·스테레오 좌우, 높이 모듈 설정, 보정 관리(`height.autocalib`), 카메라 API(`camera_api`), 작물 레지스트리 |
| `config/rules/A.yaml` | 장미 등급 라벨·정상 높이 범위, 잎 이상 유형별 심각도, 경고 기준, VLM 판정 반영 (`vlm_apply`) |
| `config/rose/growth.yaml`, `holes.yaml` | 생장 단계 원본 설정 (트레이 배치, 구멍 자동 찾기 `holes_auto`, 빈 화분 판정, 검출·등급·Claude 검수). `holes.yaml` 은 고정 구멍 좌표 (자동 찾기 실패 때 사용) |
| `config/rose/disease.json` | 잎 이상 원본 설정 (잎 탐지·분류 임계값) |
| `config/stereo/stereo_calib.npz` | 지금 쓰는 스테레오 보정. 처음 보정은 2026-11-11 체커보드 33쌍 (간격 33.3mm) |
| `config/stereo/history/` | 보정 이력 (`current.json` = 지금 버전). 지금은 11/27 실행 중 B 가 만든 `20261127T111348_B`, `calib_manager.py rollback` 으로 처음 보정으로 되돌릴 수 있음 |

자주 바꾸는 값

| 하고 싶은 것 | 파일 | 항목 |
|---|---|---|
| 잎 분류 모델 바꾸기 | `config/crops.yaml` | `disease.cls_weights` (지금 `leaf_cls_yolov8m_du.pt`) |
| 잎 이상 VLM 켜기 · 끄기 | `config/crops.yaml` | `disease.vlm.enabled` |
| 빈 화분 판정 기준 | `config/rose/growth.yaml` | `presence.dark_v_max` (110), `presence.dark_s_max` (100), `presence.empty_ratio` (0.25) — 구멍 원 안에서 명도 110 이하·채도 100 이하인 픽셀 비율의 두 카메라 평균이 이 값 이상이면 빈 칸 |
| 구멍 위치 자동 · 고정 | `config/rose/growth.yaml` | `holes_auto.mode` (`auto` / `fixed`), 원 크기 `radius_ratio` (0.25) |
| 실행 중 자동 보정(B) 켜기 · 끄기 | `config/crops.yaml` | `height.autocalib.in_pipeline` |
| 식물 있는 칸 고정 | `config/rose/growth.yaml` | `presence.method: fixed` + `layout.plants` (O / X) |
| 위험 기준 · 심각도 | `config/rules/A.yaml` | `alerts.critical_leaf_count`, `anomalies.<유형>.severity` |

### 스테레오 좌우 주의

`stereo_calib.npz` 의 왼쪽 = 천장 C2 입니다. 생장 단계 샘플 사진으로 확인해 보니 **생장 단계 모듈의 CR 이 스테레오 왼쪽**이어서 `cameras.stereo_left: CR` 로 두었습니다.
보고서에 "정렬 후 세로 어긋남" 경고가 나오면 카메라가 움직였거나 좌우가 바뀐 것이니 다시 보정하세요.

## 보정 관리 (A 체커보드 재보정 + B 자동 미세 조정)

```
실행마다 상태 확인 (세로 어긋남)
   정상 ≤ 2px ─────────────────────────────▶ 그대로
   어긋남 ──▶ B 자동 미세 조정 ── 검증 통과 ──▶ 새 버전 저장·교체, 높이 다시 계산
                                 └ 실패 ─────▶ 보고서에 "체커보드 재보정(A) 필요" 안내
   좌우 바뀜 (시차 음수) ─────────────────────▶ cameras.stereo_left/right 바꾸라고 안내
```

| | A. 체커보드 재보정 | B. 자동 미세 조정 |
|---|---|---|
| 고치는 것 | 렌즈 특성(K, D) + 두 카메라 자세 전부 + 간격 | 오른쪽 카메라가 **제자리에서 돈 양**만 (렌즈 특성·간격은 A 값 유지) |
| 필요한 것 | 체커보드를 여러 위치·각도로 동시 촬영 (6쌍 이상) | 평소 작물 사진 쌍 (사람 손 필요 없음) |
| 언제 | 설치, 해상도·렌즈 변경, B 실패 | 카메라가 살짝 밀렸을 때 (파이프라인이 자동으로) |

**B 가 맞추는 회전과 못 맞추는 회전**
- 끄덕임(간격 축 둘레)·기울어짐(광축 둘레)은 세로 어긋남으로 바로 보여서 사진만으로 맞춥니다.
- 좌우 돌아감(세로 축 둘레, yaw)은 세로 어긋남에 거의 안 보이고 **거리만** 바꿉니다 (1° ≈ 시차 11px).
  세 회전을 한꺼번에 풀면 세로 어긋남은 0 이 되지만 큐브 거리가 56.7cm → 82cm 로 틀어지는 것을 확인했습니다.
  그래서 좌우 돌아감은 **거리 기준**(보정이 맞을 때 잰 트레이 거리, 또는 줄자 실측)이 있을 때만 그 거리에 맞춥니다.
  → A 를 한 직후 `set-reference` 로 기준을 기록해 두세요.

**보정 이력**: `config/stereo/history/` 에 버전(id, 출처 A/B/initial, 세로 어긋남, 거리 기준)이 쌓이고,
지금 쓰는 파일은 그대로 `config/stereo/stereo_calib.npz` 입니다. 언제든 `rollback` 으로 되돌립니다.

```powershell
uv run python scripts/height/calib_manager.py status
uv run python scripts/height/calib_manager.py check --pair data/samples/rose_CL.jpg data/samples/rose_CR.jpg
uv run python scripts/height/calib_manager.py auto --pair data/samples/rose_CL.jpg data/samples/rose_CR.jpg --dry-run
uv run python scripts/height/calib_manager.py auto --from-api --n 3
uv run python scripts/height/calib_manager.py checkerboard --since 2026-11-27T10:00 --square 24.2
uv run python scripts/height/calib_manager.py set-reference --from-api --n 1
uv run python scripts/height/calib_manager.py rollback
```

- `--pair CL.jpg CR.jpg` 는 파이프라인 카메라 이름 순서입니다. 스테레오 좌우는 `cameras.stereo_left/right` 로 자동 배치됩니다.
- `--from-api` 는 스마트큐브 서버에서 두 ESP32-CAM 의 동시 촬영(pair) 사진을 받습니다 (`config/crops.yaml` 의 `camera_api`).
  토큰은 환경 변수 `LEAFDX_API_TOKEN` 또는 `secrets/SMARTCUBE_API_TOKEN` 파일 (git 에 안 올라감).
- 확인 그림(평행화 전후 + 가로선)은 `out/calib/<시각>/` 에 저장됩니다.
- 테스트는 운영 중 바뀌는 보정 대신 처음 보정의 복사본(`tests/data/stereo_calib_ref.npz`)을 씁니다.
- 설정: `config/crops.yaml` 의 `height.autocalib` (파이프라인 안 자동 실행 `in_pipeline`, 기준값, B 검증 기준).
- A 는 원본 도구(`collect_pairs.py`, `stereo_calibrate.py`)를 그대로 실행하고, 결과를 검증(세로 어긋남 ≤ 1.5px,
  쌍 6개 이상, 간격 변화 10% 이내)한 뒤 이력에 넣습니다.

## 구멍 위치 자동 찾기

트레이 직사각형 + 3×4 격자를 사진에 통째로 맞춥니다: 트레이 안은 어둡고, 바깥 띠는 밝은(은색 반사판) 쪽으로.
잎·꽃·전선(채도 높은 픽셀)은 점수에서 빼므로 화분이 잎에 가려도 찾습니다. 설정은 `config/rose/growth.yaml` 의 `holes_auto`.

- `mode: auto` 이면 실행마다 찾아서 쓰고, 실패하면 `holes.yaml` 로 대신하며 보고서에 경고합니다.
- `holes.yaml` 과 간격의 25% 넘게 다르면 "트레이·카메라가 움직였음" 경고 (자동 위치는 계속 사용).
- 확인 그림: `out/run/<시각>/growth/holes_<카메라>.jpg` (빨강 = 트레이 테두리, 청록 = 구멍).
- 가정: R1C1 = 사진 왼쪽 위. 트레이를 180° 돌려 넣으면 칸 번호가 바뀌니 트레이에 방향 표시를 해 두세요.
- 간격 잡기: 트레이 한쪽 바깥이 검은 전선·벽이면 밝기만으로는 그 변 위치가 정해지지 않아 간격이 작게 잡힐 수 있습니다
  (CL2/CR2 에서 가로 간격 5% 작음 → 4열 원이 화분 왼쪽으로 빠짐). 그래서 `holes.yaml` 간격과 다를수록 점수를 깎습니다
  (`pitch_prior_weight: 3.0`, 세로/가로 비 1 에서 벗어남은 `aspect_weight: 0.5`).
  **카메라 높이를 바꾸면** 간격 자체가 바뀌므로 `pitch_prior_weight: 0` 으로 `find_holes.py --write` 를 실행해 `holes.yaml` 을 새로 만든 뒤 3.0 으로 되돌리세요.

```powershell
uv run python scripts/rose_growth/find_holes.py --CL data/samples/CL1.jpg --CR data/samples/CR1.jpg
uv run python scripts/rose_growth/find_holes.py --CL ... --CR ... --write     # holes.yaml 갱신 (이전 파일 백업)
```

### 2026-11-27 사진(CL1, CR1)로 확인한 것
- 구멍: 두 카메라 모두 찾음. `holes.yaml` 보다 간격의 약 40% 어긋나 있었음 (트레이·카메라 이동).
  자동 위치로 바꾸자 빈 화분 판정 오류가 6칸 → 3칸. 남은 3칸은 흙이 많이 보이는 식물 칸을 '어두움 = 빈 칸' 으로 본 것.
- 빈 화분 판정: 검은 덮개는 무채색, 흙은 갈색 기운이 있어 `presence.dark_s_max`(채도 조건)를 추가.
  `dark_v_max 110 / dark_s_max 100 / empty_ratio 0.25`, 원 반지름 `radius_ratio 0.25` 로 사진 3세트 36칸 모두 맞음
  (식물 칸 최고 0.22, 빈 칸 최저 0.28. 예전 100 / 채도 조건 없음 / 0.30 은 0.44 와 0.40 으로 겹침.
  간격 보정 후 원이 화분에 정확히 맞자 반지름 0.3 은 흙·화분 벽까지 덮어 0.25 / 0.26 으로 거의 붙어서 0.25 로 줄임).
- 보정: 지금 좌우(CR = 왼쪽)로는 시차가 음수, 바꾸면 세로 어긋남 36px → **좌우가 바뀌었고 카메라도 크게 움직임**.
  `calib_manager.py check` 가 이것을 "좌우 바뀜" 으로 알려 줍니다. 좌우 설정을 확인한 뒤 체커보드 재보정(A)이 필요합니다.

## 작물 추가 (작물 N)

1. `src/crop_monitor/modules/<모듈>/adapter.py` 작성 — `run(images, spec, out_dir) -> (결과, 경고)` (장미 어댑터와 같은 출력 형식)
2. `config/crops.yaml` 에 작물 항목 추가 (`growth.module`, `disease.module`)
3. `config/rules/N.yaml` 작성

그래프 코드는 수정하지 않습니다.

## 테스트

```powershell
uv run pytest                  # 전체 33개 (실제 모델로 그래프 실행 포함, 약 4분. 키가 있으면 Claude 요금 발생)
uv run pytest -m "not slow"    # 빠른 테스트만
```
