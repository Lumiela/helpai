# 미니 장미 생장 단계 판단 파이프라인 — 혼합형

> 이 폴더는 2단계를 **YOLO 봉오리 위치(1클래스) → FastSAM 봉오리 마스크 → 분류 모델 등급** 으로 나눈 혼합형입니다. 동작 원리와 설정은 [HYBRID.md](HYBRID.md) 를 보세요.

## 배치

트레이 구멍 3행 × 4열. 식물은 체크무늬로 6개입니다.

식물 유무는 기본적으로 사진에서 자동으로 판단합니다(`presence.method: dark_cover`). 식물 위치가 바뀌지 않으면 `layout.plants` 의 O/X 표로 고정할 수 있습니다(`presence.method: fixed`).

| 카메라 범위 | 1열 | 2열 | 3열 | 4열 |
|---|---|---|---|---|
| 1행 | CL, CR | CL, CR | CL, CR | CL, CR |
| 2행 | CL, CR | CL, CR | CL, CR | CL, CR |
| 3행 | CL, CR | CL, CR | CL, CR | CL, CR |

범위표는 `configs/pipeline.yaml` 의 `layout.coverage` 에 있습니다. 2026-11-12 카메라 위치 변경 후 두 카메라가 모든 칸을 봅니다(이전: 4열 = CR만, R3C1 = CL만).

## 흐름

```
CL, CR 이미지
 └─ 1단계: 각각의 미니 장미 구분
     1) 식물 유무: 구멍 중심 원 안의 어두운 픽셀 비율 (빈 화분 = 검은 덮개)
                   범위표의 카메라 점수 평균 < empty_ratio → 식물
     2) 세그멘테이션: 식물 색 픽셀을 가장 가까운 "식물 있는 구멍"에 배정 (잎이 겹쳐도 분리)
 └─ 2단계: 생장 단계 판단
     1) 모든 식물 = grade_zero
     2) 봉오리 위치(YOLO 1클래스) → 마스크(FastSAM) → 등급(분류 모델, not_bud 는 버림)
        → 봉오리 마스크와 가장 많이 겹치는 식물에 배정 (mask_overlap)
     3) 카메라별로 식물의 최고 등급 → 두 카메라 중 높은 등급이 최종 (fusion: max)
 └─ 출력: 3×4 표 (식물 = 등급, 빈 구멍 = '-', 판단 불가 = '?')
```

## 사용 순서

```
pip install -r requirements.txt
```

1. 카메라 이미지 넣기: `input/CL.jpg`, `input/CR.jpg`
2. 구멍 위치 보정 → `configs/holes.yaml`
   ```
   python tools/calibrate_holes.py
   ```
   - R1C1부터 순서대로 구멍 중심을 클릭합니다. 빈 구멍도 찍습니다.
   - 안 보이는 구멍은 `n` 으로 건너뜁니다.
   - 사진 가장자리 밖 구멍은 회색 여백에 짐작해 찍어 주세요. 그 식물의 잎이 이웃 식물로 섞이는 것을 막아 줍니다.
   - 카메라를 옮기지 않으면 한 번만 하면 됩니다.
3. 구멍 위치와 빈 구멍 점수 확인 → `output/holes_preview_CL.jpg`, `holes_preview_CR.jpg`
   ```
   python tools/preview_holes.py
   ```
4. 1단계만 확인 (검출 모델 없이, 모든 식물 = grade_zero)
   ```
   python run_pipeline.py --no-detector
   ```
5. 혼합형 모델 학습 → `runs/detect/rose_bud1/weights/best.pt` (위치), `runs/classify/rose_grade/weights/best.pt` (등급)
   ```
   python tools/prepare_hybrid_data.py --data-root <dataset 폴더> --out hybrid_data
   python train_hybrid.py det --data hybrid_data --device 0
   python train_hybrid.py cls --data hybrid_data --device 0
   ```
6. 전체 실행 → `output/<시각>/result_buds.jpg`(칸별 등급과 봉오리 사진), `result.json`, `table.txt`, `vis_CL.jpg`, `vis_CR.jpg`, `masks/`
   ```
   python run_pipeline.py
   ```

검증 테스트 (합성 이미지, 모델 불필요): `python tests/test_pipeline.py`, `python tests/test_hybrid.py`

## Colab 에서 실행

이 폴더(`rose_hybrid`)가 그대로 Colab 용입니다. 코드, 설정, 입력 사진, `dataset.zip`(new_MR_growth 장미농장 데이터셋을 640 타일로 자른 것), `rose_hybrid.ipynb` 가 들어 있습니다.

1. 폴더를 구글 드라이브 **내 드라이브**에 통째로 업로드합니다.
2. `rose_hybrid.ipynb` 를 Colab 으로 열고 위에서부터 실행합니다.
   - 구멍 보정은 노트북 안에서 클릭으로 합니다 (`tools/calibrate_colab.py`).


## 파일 구성

| 파일 | 역할 |
|---|---|
| `configs/pipeline.yaml` | 전체 설정 (배치·범위표, 유무 판단, 세그멘테이션, 검출, 할당·합치기 규칙) |
| `configs/holes.yaml` | 카메라별 구멍 중심 좌표 (보정 도구가 생성) |
| `rose_pipeline/sources.py` | 이미지 입력 (`file` / `camera`) |
| `rose_pipeline/layout.py` | 칸·범위표·구멍 위치 |
| `rose_pipeline/presence.py` | 1단계-1: 식물 유무 판단 |
| `rose_pipeline/segmentation.py` | 1단계-2: 식물 세그멘테이션 |
| `rose_pipeline/detection.py` | 봉오리 검출기 (hybrid / YOLO / none) |
| `rose_pipeline/assignment.py` | 봉오리 → 식물 할당 규칙 |
| `rose_pipeline/grading.py` | 2단계: 검출·할당·카메라별 최고 등급 |
| `rose_pipeline/fusion.py` | 두 카메라 등급 합치기 |
| `rose_pipeline/pipeline.py` | 전체 연결 (`RosePipeline`) |
| `rose_pipeline/visualize.py` | 결과 이미지·JSON 저장 |
| `tools/calibrate_holes.py` | 구멍 보정 GUI |
| `tools/preview_holes.py` | 구멍 위치·점수 미리보기 |
| `train_hybrid.py` | 혼합형 학습: 봉오리 위치(det)·등급 분류(cls) |
| `tools/prepare_hybrid_data.py` | 혼합형 학습 데이터 생성 |
| `tools/mine_crops.py` | 실제 사진에서 분류 보강용 크롭 수집 |
| `train_detector.py` | (기존 방식) YOLOv11 위치+등급 한 번에 학습 |
| `run_pipeline.py` | 실행 진입점 |

## 바꾸기 쉬운 부분

- **카메라 연결**: `source` 를 `{type: camera, device: 0}` (또는 RTSP URL) 로 변경
- **봉오리 할당 규칙**: `grading.assignment` (`center_in_mask` / `max_overlap` / `mask_overlap`), 또는 `assignment.py` 에 `@register` 함수 추가
- **카메라 합치기 규칙**: `fusion.py` 에 `@register` 함수 추가 후 `grading.fusion` 변경
- **유무 판단·세그멘테이션 방법**: `presence.py` / `segmentation.py` 에 클래스를 추가하고 등록 (예: 학습한 분류기, SAM)
- **식물 위치 고정**: `presence.method: fixed` 로 바꾸면 `layout.plants` 표를 따릅니다. 사진 판단과 다르면 경고만 출력합니다.
- **기준값**: `presence.empty_ratio`, `presence.dark_v_max`, `segmentation.hsv_ranges`, `max_reach`

## 알려진 한계

- **잎에 완전히 덮인 빈 구멍**: 검은 덮개가 안 보여서 식물로 잘못 판단합니다. 현재 사진의 CR R3C4가 이 경우입니다(점수 0.17). 색 기준만으로는 해결이 어렵습니다. 식물 위치가 고정이면 `presence.method: fixed` 로 피할 수 있고, 자동 판단이 필요하면 구멍 영역을 "식물/빈 구멍"으로 분류하는 작은 모델을 학습하면 개선할 수 있습니다.
- **기준값 여유**: 현재 사진에서 식물 구멍은 최대 0.31, 빈 구멍은 최소 0.46으로 여유가 크지 않습니다. 조명이 바뀌면 `empty_ratio` 를 다시 맞춰야 할 수 있습니다.
- **학습 데이터 차이**: 현재 학습 데이터(`train/valid/test`)는 일반 장미 근접 사진이라 실제 트레이 사진과 차이가 큽니다. 실제 사진으로 봉오리를 라벨링해야 2단계 정확도를 기대할 수 있습니다.
- **분할 경계**: 겹치는 잎은 "가까운 구멍" 기준으로 나누므로, 긴 가지가 이웃 구멍 쪽으로 뻗으면 잘못 나뉠 수 있습니다.
