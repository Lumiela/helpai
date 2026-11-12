# 혼합형 생장 단계 판단 (YOLO 위치 + FastSAM 마스크 + 분류 등급)

`rose_colab_v3` 와 1단계(식물 유무·화분별 세그멘테이션), 화분별 최고 등급 규칙, 카메라 합치기는 같습니다.
**2단계에서 봉오리를 찾고 등급을 매기는 부분만** 세 모델로 나눴습니다.

## 왜 나눴나

기존 방식은 YOLO 한 모델이 "어디에 봉오리가 있나"와 "몇 등급인가"를 동시에 판단합니다.

- 등급 차이가 미묘하고 G3·G4 데이터가 적어 등급 판단이 흔들립니다.
- 봉오리가 없는 예시를 배우지 못해 반사판·손·케이블을 G4/G1로 잡았습니다.

혼합형은 일을 나눕니다.

| | 모델 | 하는 일 | 학습 |
|---|---|---|---|
| ① | YOLO 검출 (1클래스 `bud`) | 봉오리 **위치**만 찾음. 등급을 몰라도 되므로 박스 5만여 개를 한 클래스로 모두 씀 | `train_hybrid.py det` |
| ② | FastSAM | ①의 박스 안에서 봉오리의 정확한 **마스크**를 따냄 | 안 함 (공개 가중치) |
| ③ | YOLO 분류 | 봉오리 크롭의 **등급** 판단. `not_bud` 클래스로 ①의 오탐지를 거름 | `train_hybrid.py cls` |

## 화분 하나가 판단되는 과정

예: CL 카메라, R1C1 화분

```
[1단계] R1C1 식물 마스크 (색 기준, 기존 그대로)
   │  마스크를 감싸는 박스 + 여백 10% 로 잘라냄 (detect_on: plant_crop)
   ▼
[① 위치] YOLO 1클래스, conf ≥ 0.15
   │  박스 3개: A(0.82)  B(0.41)  C(0.18)
   ▼
[② 마스크] FastSAM 이 잘라낸 영역 전체를 조각냄 (잎·봉오리·화분 …)
   │  박스마다 IoU 가 가장 큰 조각을 고름
   │    A → 봉오리 조각 (IoU 0.71) → 마스크 사용, 박스를 마스크에 맞게 다시 잡음
   │    B → 잎 조각 (IoU 0.35)     → 박스 근처(+10%)만 잘라 마스크로 사용
   │    C → 맞는 조각 없음 (IoU < 0.3) → 마스크 없이 박스만 사용
   ▼
[③ 등급] 박스 중심 정사각형(긴 변 × 1.3) 크롭 → 224px 분류
   │    A → grade_three 0.88  → 남김
   │    B → not_bud 0.93      → 버림 (잎을 봉오리로 잘못 잡은 것)
   │    C → grade_one 0.42    → 버림 (확신 < cls_conf 0.5)
   ▼
[배정] mask_overlap: 봉오리 마스크와 픽셀이 가장 많이 겹치는 식물 → A 는 R1C1
   │  (마스크가 없거나 어느 식물과도 안 겹치면 박스 기준 max_overlap)
   ▼
[화분 등급] 기본 grade_zero, 배정된 봉오리 중 최고 등급 → R1C1 = grade_three
[카메라 합치기] CL·CR 중 높은 등급 → 최종
```

이웃 화분의 잘라낸 영역이 겹쳐 같은 봉오리가 두 번 나오면, 기존처럼 IoU 0.5 기준으로 하나만 남깁니다(분류 확신이 높은 쪽).

### 각 장치가 맡는 일

- **① 신뢰도를 낮게(0.15)** 둡니다. 놓치는 봉오리를 줄이고, 잘못 잡은 것은 ③이 `not_bud`로 거릅니다.
- **② 마스크 쓰임새**
  - 분류 크롭을 봉오리에 정확히 맞춥니다(박스가 커도 크롭이 봉오리 중심).
  - 봉오리가 두 화분 경계에 있을 때, 박스 중심이 아니라 실제 봉오리 픽셀로 화분을 정합니다.
  - 시각화에 흰 윤곽선으로 그려 확인할 수 있게 합니다.
- **② 마스크가 번지지 않게:** FastSAM이 봉오리와 잎을 한 조각으로 묶어도, 박스보다 10%(`mask_margin`)만 넓게 잘라 쓰므로 잎 전체로 번지지 않습니다.
- **③ 크롭 모양 통일:** 학습 크롭(`prepare_hybrid_data.py`)과 추론 크롭이 같은 함수(`square_box`)로 잘립니다.

## 최종 결과에서 봉오리 보기

| 파일 | 내용 |
|---|---|
| `result_buds.jpg` | **최종 결과판.** 3×4 칸마다 최종 등급과 그 칸에 배정된 봉오리 사진. 높은 등급부터 칸당 8장까지 표시 (`G3 0.88 CL` = 등급, 분류 확신, 카메라) |
| `vis_CL.jpg`, `vis_CR.jpg` | 카메라 사진 위 표시. 봉오리 자리는 식물 색을 입히지 않고 원래 색으로 두고, 등급 색 박스·흰 마스크 윤곽을 맨 위에 그림 |
| `table.txt` | 3×4 표 + 칸별·카메라별 봉오리 개수, 어느 식물에도 배정되지 않은 봉오리 수 |

같은 봉오리가 두 카메라에 모두 보이면 카메라마다 따로 표시되고 따로 셉니다.

## 학습 데이터 (`tools/prepare_hybrid_data.py`)

`dataset.zip` 에서 자동으로 만듭니다. `dataset.zip` 은 `new_MR_growth/dataset_cp.zip`(장미농장 사진 143장을 새로 라벨링한 원본 + copy-paste 합성 사진)을 960 창 → 640 타일로 자른 것입니다(만든 곳: `../dataset_mr/`, 출처는 그 안의 `README.txt`).
- 클래스: `class_0`=grade_four, `class_1`=grade_one(초록 봉오리), `class_2`=grade_three, `class_3`=grade_two. 번호 순서가 기존 `data.yaml` 과 같아 라벨 번호는 그대로입니다.
- 분할은 원본의 train/val 을 따릅니다(사진 5장 묶음 단위, 합성 사진은 train 사진의 크롭만 사용). **test 분할은 없어서** 학습 스크립트가 val 로 평가합니다.

| 분할 | ① 검출 이미지 | ① 박스 | ③ G1 | ③ G2 | ③ G3 | ③ G4 | ③ not_bud |
|---|---|---|---|---|---|---|---|
| train | 7,943 | 50,390 | 8,643 | 1,182 | 1,009 | 963 | 11,855 |
| val | 545 | 3,549 | 1,940 | 79 | 33 | 57 | 2,126 |

이전 데이터(Roboflow 근접 사진 + 장미농장 타일)는 상위 폴더의 `data.yaml`·`train/valid/test` 에 그대로 있습니다.

- **① 검출 데이터:** 이미지는 그대로 두고 라벨의 클래스 번호만 0(`bud`)으로 바꿉니다. 타일이 겹치고 합성 사진이 원본을 바탕으로 쓰므로 같은 봉오리가 여러 번 들어가지만, 위치 학습에는 문제가 없어 그대로 둡니다.
- **③ 중복 제거:** `tiles_manifest.csv`로 타일 박스를 원본 사진 좌표로 되돌리고, 같은 원본(합성 사진 `cp_<원본>_<번호>`도 원본으로 봄)·같은 등급에서 IoU 0.5 이상 겹치는 박스는 가장 크게 보이는 하나만 자릅니다. 그래서 ③의 G1은 실제 봉오리 수(train 원본 8,674개, 8px 미만 제외)와 거의 같습니다. 끄려면 `--no-dedupe`.
- **③ 등급 크롭:** 라벨 박스를 정사각형으로 잘라 등급 폴더에 넣습니다.
- **③ `not_bud`:** 같은 이미지에서 봉오리와 겹치지 않는 곳을 봉오리 크기로 무작위로 자릅니다. 대부분 잎과 배경입니다.
  - 타일 경계에서 잘려 라벨이 빠진 봉오리 조각이 드물게 섞일 수 있습니다.

**트레이 환경 보강 (`tools/mine_crops.py`):** 반사판·케이블·손 같은 트레이 오탐지는 위 데이터에 없습니다.
1. 실제 트레이 사진에서 ①의 후보를 모읍니다.
2. 사람이 폴더를 정리합니다(틀린 것은 `not_bud`로).
3. `--extra-cls extra_cls`로 학습 데이터에 추가해 ③을 다시 학습합니다.

## 실행 (Colab)

`rose_hybrid.ipynb`를 위에서부터 실행합니다. 5번 단계가 학습입니다.

```
python tools/prepare_hybrid_data.py --data-root /content/dataset --out /content/hybrid_data
python train_hybrid.py det --data /content/hybrid_data --device 0
python train_hybrid.py cls --data /content/hybrid_data --device 0
python run_pipeline.py
```

기존 방식으로 돌아가려면 `configs/pipeline.yaml`에서 다음을 바꿉니다.
- `detector.type: yolo`
- `weights: runs/detect/rose_bud/weights/best.pt`
- `conf: 0.25`
- `assignment: center_in_mask`

이후 `train_detector.py`로 학습합니다.

## 설정 (`configs/pipeline.yaml` → `grading.detector`)

| 항목 | 기본값 | 뜻 |
|---|---|---|
| `type` | `hybrid` | `yolo`(기존) / `hybrid` / `none` |
| `weights` | `runs/detect/rose_bud1/weights/best.pt` | ① 위치 검출 가중치 |
| `conf` | 0.15 | ① 신뢰도 하한 (낮게 두고 ③이 거름) |
| `sam_weights` | `FastSAM-s.pt` | ② 가중치. `null`이면 FastSAM 생략(박스만) |
| `sam_imgsz` / `sam_conf` | 1024 / 0.4 | ② 입력 크기 / 조각 신뢰도 |
| `mask_iou` | 0.3 | 박스와 조각 IoU가 이보다 작으면 마스크 없이 박스만 사용 |
| `mask_margin` | 0.1 | 고른 조각을 박스보다 이만큼만 넓게 잘라 씀 |
| `cls_weights` | `runs/classify/rose_grade/weights/best.pt` | ③ 분류 가중치 |
| `cls_imgsz` / `cls_conf` | 224 / 0.5 | ③ 입력 크기 / 확신 하한 |
| `crop_pad` | 0.15 | 분류 크롭 여백 (`prepare_hybrid_data.py --pad`와 같게) |
| `grading.assignment` | `mask_overlap` | 봉오리 마스크 기준 화분 배정 |

`conf`, `cls_conf`, `mask_iou`는 아직 실제 데이터로 맞추지 않은 출발값입니다. 학습 후 검증 사진으로 조정하세요.

## 추가·바뀐 파일

| 파일 | 내용 |
|---|---|
| `rose_pipeline/detection.py` | `HybridBudDetector`, `square_box`, `BudDetection` 에 `mask`·`mask_xy`·`det_conf` 추가 |
| `rose_pipeline/assignment.py` | `mask_overlap` 배정 규칙 |
| `rose_pipeline/segmentation.py` | `PlantSegment.overlap_mask` |
| `rose_pipeline/visualize.py` | 봉오리 강조 표시, 마스크 윤곽선, 최종 결과판 `draw_board` |
| `rose_pipeline/pipeline.py` | `result.json`에 `det_conf`, `mask_px`, 봉오리 개수 표 `bud_table` |
| `tools/prepare_hybrid_data.py` | ①·③ 학습 데이터 생성 |
| `tools/mine_crops.py` | 실제 사진에서 분류 보강용 크롭 수집 |
| `train_hybrid.py` | ①(`det`)·③(`cls`) 학습 |
| `tests/test_hybrid.py` | 가짜 모델로 혼합형 검증 (모델·GPU 불필요) |
| `rose_hybrid.ipynb` | 혼합형 Colab 노트북 |

## 한계

- **1단계 문제는 그대로입니다.** 혼합형은 2단계만 바꿉니다. 좌측 식물 영역 미인식 같은 1단계 문제는 따로 고쳐야 합니다.
  - 화분 마스크 밖 봉오리는 `max_overlap`으로 한 번 더 찾지만, 박스가 어느 화분 마스크와도 겹치지 않으면 버려집니다.
- **속도:** 화분마다 FastSAM을 한 번씩 돌립니다(카메라 2대 × 화분 6개). T4 GPU에서는 문제없지만 CPU에서는 느립니다.
- **작은 봉오리:** FastSAM이 잎과 구분하지 못할 수 있습니다. 이때도 마스크 없이 박스로 판단하므로 결과는 남습니다.
- **G1 초록 봉오리:** 잎과 비슷하므로 ③의 정확도에 달려 있습니다. 혼동 행렬에서 `grade_one` ↔ `not_bud`를 확인하세요.
