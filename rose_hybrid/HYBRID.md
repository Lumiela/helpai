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
| ① | YOLO 검출 (1클래스 `bud`) — `weights/yolov8m_best.pt` | 봉오리 **위치**만 찾음. 등급을 몰라도 되므로 박스 5만여 개를 한 클래스로 모두 씀 | 학습 완료 (`train_hybrid.py det`) |
| ② | FastSAM — `FastSAM-s.pt` | ①의 박스 안에서 봉오리의 정확한 **마스크**를 따냄 | 안 함 (공개 가중치) |
| ③ | YOLO 분류 — `weights/yolo26m-cls_best.pt` | 봉오리 크롭의 **등급** 판단. `not_bud` 클래스로 ①의 오탐지를 거름 | 학습 완료 (`train_hybrid.py cls`) |
| ④⑤ | Claude (API) | ③이 남긴 봉오리 다시 판단(검수), 모델이 놓친 봉오리 추가 | 안 함 |

모델 파일은 모두 이 폴더에 들어 있어 **학습 없이 바로 실행**합니다 (학습 시작 가중치 `weights/yolov8m.pt`·`weights/yolo26m-cls.pt` 는 폴더에 없으며, 재학습할 때 ultralytics 가 내려받습니다).

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
- 클래스: `class_0`=grade_four, `class_1`=grade_one(닫힌 봉오리), `class_2`=grade_three, `class_3`=grade_two. 번호 순서가 기존 `data.yaml` 과 같아 라벨 번호는 그대로입니다.
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

`rose_hybrid.ipynb`를 위에서부터 실행합니다. **학습 단계는 없습니다** — 학습된 `weights/yolov8m_best.pt`·`weights/yolo26m-cls_best.pt` 와 `FastSAM-s.pt` 를 그대로 쓰고 5번(전체 실행)에서 바로 적용합니다. 폴더에 `ANTHROPIC_API_KEY.txt` 를 함께 넣어 두세요 (아래 "Claude API 사용법"). GPU 는 없어도 됩니다.

```
python run_pipeline.py
```

(모델을 다시 학습하려면 노트북 밖에서 `tools/prepare_hybrid_data.py` → `train_hybrid.py det` / `cls` 로 학습하고, 설정의 `weights`·`cls_weights` 를 새 `best.pt` 로 바꿉니다.)

## Claude API 사용법

Claude 검수(④)·누락 추가(⑤)·화분 단위 VLM 은 Claude API 를 씁니다. 키는 **파일에서 읽습니다** (11/23 변경: 직접 입력 → 파일로 읽기).

1. https://console.anthropic.com → API Keys 에서 키(`sk-ant-...`)를 발급합니다. 사용량만큼 요금이 나갑니다.
2. 프로젝트 폴더(`rose_hybrid/`)에 `ANTHROPIC_API_KEY.txt` 를 만들고 **키 한 줄만** 넣습니다. (`ANTHROPIC_API_KEY=sk-ant-...` 형식, `#` 주석 줄도 됨)
3. Colab 이면 이 파일을 `rose_hybrid` 폴더와 함께 드라이브에 올립니다. 노트북 0번 셀이 키가 '있음' 만 출력합니다.
4. 파일이 없으면 `ANTHROPIC_API_KEY` 환경 변수를 씁니다. 파일 위치는 `grading.detector.vlm.api_key_file` 로 바꿉니다.
5. `review: false`, `add_missed: false` 로 하면 API 를 쓰지 않으므로 키가 필요 없습니다.

- 요청 수는 실행 끝 `사용량` 에 나옵니다 (11/19 사진 기준 약 20번).
- 코드: `rose_pipeline/api_key.py` (맨 위에 같은 설명). **키 파일은 `.gitignore` 에 있어 깃에 올라가지 않습니다. 다른 사람에게 공유하지 마세요.**


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
| `weights` | `weights/yolov8m_best.pt` | ① 위치 검출 가중치 (학습 시작 가중치 `weights/yolov8m.pt` 는 `train_hybrid.py` 기본값) |
| `conf` | 0.15 | ① 신뢰도 하한 (낮게 두고 ③이 거름) |
| `max_box_frac` / `max_box_px` | 0.3 / 200 | 식물 크롭 넓이의 30% 또는 긴 변 200px 보다 큰 박스는 버림 |
| `sam_weights` | `FastSAM-s.pt` | ② 가중치. `null`이면 FastSAM 생략(박스만) |
| `sam_imgsz` / `sam_conf` | 1024 / 0.4 | ② 입력 크기 / 조각 신뢰도 |
| `mask_iou` | 0.3 | 박스와 조각 IoU가 이보다 작으면 마스크 없이 박스만 사용 |
| `mask_margin` | 0.1 | 고른 조각을 박스보다 이만큼만 넓게 잘라 씀 |
| `cls_weights` | `weights/yolo26m-cls_best.pt` | ③ 분류 가중치 (학습 시작 가중치 `weights/yolo26m-cls.pt`) |
| `cls_backend` | `yolo` | ③ 분류 방식: `yolo` / `embed` / `vlm` / `cascade` |
| `cls_imgsz` / `cls_conf` | 224 / 0.5 | ③ 입력 크기 / 확신 하한 |
| `review` / `review_conf` | `true` / 0.6 | ④ Claude 검수 / 검수 확신 하한 |
| `add_missed` | `true` | ⑤ Claude 누락 추가 |
| `crop_pad` | 0.15 | 분류 크롭 여백 (`prepare_hybrid_data.py --pad`와 같게) |
| `vlm.api_key_file` | `ANTHROPIC_API_KEY.txt` | Claude API 키 파일 (프로젝트 폴더 기준, 키 한 줄). 없으면 `ANTHROPIC_API_KEY` 환경 변수 |
| `grading.assignment` | `mask_overlap` | 봉오리 마스크 기준 화분 배정 |

`conf`, `cls_conf`, `mask_iou`는 아직 실제 데이터로 맞추지 않은 출발값입니다. 학습 후 검증 사진으로 조정하세요.

## 현재 기본: 학습된 yolov8m_best + yolo26m-cls_best → Claude 검수·누락 추가

| 단계 | 모델 / 설정 |
|---|---|
| ① 위치 | `weights/yolov8m_best.pt` — yolov8m.pt 에서 `bud` 1클래스로 학습 (30 epoch, 11/23). `conf: 0.15` |
| ③ 등급 | `weights/yolo26m-cls_best.pt` — yolo26m-cls.pt 에서 `not_bud`/G1~G4 로 학습 (30 epoch, 11/23). `cls_backend: yolo` |
| ④⑤ | Claude 검수·누락 추가 (아래 "학습 없이" 와 같음). `review`·`add_missed` 를 false 로 하면 모델만 사용 |

**모델 선정 이유 (11/23)** — YOLO11·YOLOv8·YOLO26 의 n/s/m 9개 조합을 같은 데이터로 30 epoch 학습해 비교 (탐지 val 545장·봉오리 3,549개, 분류 val 4,235장)

| 기준 | 선택 | 값 |
|---|---|---|
| 탐지 · 오탐을 줄이는 게 중요 (Precision, 정밀도) | **YOLOv8m** | 0.744 (1위) |
| 탐지 · 놓치는 물체를 줄이는 게 중요 (Recall, 재현율) | **YOLOv8m** | 0.703 (1위) |
| 탐지 · 상자 위치를 정밀하게 맞추는 게 중요 (mAP50-95) | **YOLOv8m & YOLO11m** | 0.310 (m 크기 중 공동 1위. 전체로는 YOLO26s 0.317) |
| 분류 (top-1 정확도) | **YOLO26m-cls** | 0.970 (1위) |
| **결론** | **YOLOv8m + YOLO26m-cls** | |

한계: 분류 모델은 정확도만 비교했고(클래스별 정밀도·재현율 비교 없음), 위 9개 외 다른 모델과는 비교하지 않았습니다.

**확인한 성능 (2026-11-23)**
- ③ val 크롭 2169장 (`tools/eval_classifier.py --backend yolo`): 정확도 0.950. 재현율 not_bud 0.99 · G1 0.98 · **G2 0.42 · G3 0.58 · G4 0.63** — 데이터가 적은 G2~G4 는 아직 약함 (G2 를 G1 으로 41/79 판단).
- 전체 실행 (11/19 사진, `output/20261123T160536`): 후보 19 → ③ 통과 12 → 검수 6개 같음·6개 버림 → 누락 추가 1개 (R2C4 CR). 최종 R1C1 G3 / R1C3 G1 / R2C4 G1 / 나머지 grade_zero (이전 결과와 같음). Claude 요청 20번.

## 학습 없이: yolov8m 후보 → yolo26m-cls 등급 → Claude 검수·누락 추가 (이전 설정)

`weights: weights/yolov8m.pt`, `conf: 0.01`, `cls_weights: weights/yolo26m-cls.pt`, `cls_backend: embed` 로 바꾸면 이 방식입니다.

`detector.type: hybrid` 를 학습하지 않은 공개 모델 그대로 씁니다.

| 단계 | 모델 | 하는 일 |
|---|---|---|
| ① 후보 | `weights/yolov8m.pt` (COCO 80클래스) | 봉오리 클래스가 없으므로 **클래스는 무시**하고 박스 위치만 후보로 씀 (`conf: 0.01`, `agnostic_nms`). 식물 크롭 넓이의 30% 보다 큰 박스(식물 전체 = potted plant)와 긴 변이 200px 보다 큰 박스는 버림 (`max_box_frac`, `max_box_px`) |
| ② 마스크 | `FastSAM-s.pt` | 기존과 같음 |
| ③ 등급 | `weights/yolo26m-cls.pt` (ImageNet) | ImageNet 클래스 이름은 쓰지 않고 **특징 추출기**로만 씀. 등급을 아는 참고 크롭(`cls_reference/<클래스>/`, 클래스마다 200장) 중 가장 비슷한 15장의 등급으로 투표 (`cls_backend: embed`). 확신 = 득표 비율 |
| ④ 검수 | Claude (`vlm` 설정) | ③ 에서 남은 크롭을 **③ 결과를 알려 주지 않고** 다시 판단시켜 그 답을 최종 등급으로 씀. `not_bud` 이거나 확신 < `review_conf`(0.6) 이면 버림 (`review: true`) |
| ⑤ 누락 추가 | Claude (`vlm` 설정) | 봉오리를 식물에 배정한 뒤, 식물마다 사진(이웃 식물 어둡게 + 화분 흰 원)에 **이미 찾은 봉오리를 청록색 번호 박스로 그려** 보내고 박스 밖에서 놓친 봉오리·꽃만 받아 추가. 이미 찾은 봉오리와 겹치거나(중심이 박스 안, IoU ≥ 0.3) 확신 < `min_conf` 인 답은 버림 (`add_missed: true`). 추가 뒤, ④ 검수 봉오리의 등급 판단 크롭(박스보다 넓음) 안에 같은 등급 추가 봉오리가 있으면 검수 답이 그 봉오리를 본 것이므로 검수 봉오리를 뺌 (`사용량` 의 `dup_removed`) |

- `result.json` 의 봉오리마다 `cls_class`/`cls_conf`(③ yolo26m-cls 등급)가 함께 저장되고, `vis_*.jpg` 에서 검수로 등급이 바뀐 봉오리는 `G3 0.80 -> R1C1 (cls G4)` 처럼 ③ 등급이 괄호로 표시됩니다.
- ⑤ 로 추가된 봉오리는 `vis_*.jpg` 에 `+VLM`, `result.json` 에 `added_by: claude` 로 표시됩니다. ⑤ 에서 Claude 가 식물마다 답한 요약은 `VLM 판단` 표·결과판 칸 제목 아래·`vlm/` 폴더(보낸 사진)에 vlm_plant 와 같은 양식으로 나옵니다.
- 실행이 끝나면 `사용량` 에 후보 수, ③ 통과 수, 검수에서 같음/바뀜/버림 개수, 추가 개수(`added`)와 Claude 요청·토큰 수가 출력됩니다. 요청 수 ≈ 식물 크롭 수(④) + 식물 수 × 카메라 수(⑤).
- 참고 크롭 다시 만들기: `python tools/prepare_hybrid_data.py --data-root <dataset 폴더> --out hybrid_data` → `python tools/make_vlm_examples.py --data hybrid_data --out cls_reference --per-class 200 --min-side 16`

**확인한 성능 (2026-11-23, 학습 없음)**

- ③ 만 (val 크롭 클래스마다 30장, 150장): 정확도 약 67%. `not_bud` 는 29/30 맞지만 G2↔G3↔G4 처럼 이웃 등급끼리 많이 헷갈림.
- ① yolov8m 후보: 실제 CL/CR 사진에서 대부분 식물 전체(potted plant)나 잎 묶음(banana·orange 등)을 잡고, **작은 닫힌 봉오리는 거의 후보로 잡지 못함**.
- 전체 실행 (11/19 사진): 후보 73개 → ③ 통과 42개(대부분 잎을 G4 로 판단) → 검수에서 41개를 버리고 1개(R1C1 G3, 열린 꽃) 남김. Claude 요청 12번. 같은 사진에서 화분 단위 VLM 이 찾은 R1C3·R2C4 의 G1 봉오리는 ① 후보에 없어서 놓침.
- ⑤ 누락 추가를 켠 뒤 (같은 사진, `review_conf` 0.5): 검수에서 40개 버림 → ⑤ 에서 5개 추가 (R1C1 G3 꽃, R1C3·R2C4 G1 봉오리). 최종 표 R1C1 G3 / R1C3 G1 / R2C4 G1 / 나머지 grade_zero 로 화분 단위 VLM 결과(11/19)와 같아짐. Claude 요청 24번.
- 이때 R1C3(CL) 에 식물 일부를 크게 잡은 박스가 검수에서 `G1 0.50` 으로 남아 봉오리 수가 1개 늘었음 → `review_conf` 를 0.6 으로 올림 (등급 결과는 같음, 올린 뒤 재실행은 안 함).
- 원인 분석: 그 박스(209×576, ① 신뢰도 0.016) 안에는 봉오리가 없었지만, 등급 판단 크롭(749×589) 왼쪽 가장자리에 R1C3 의 작은 빨간 봉오리(30px)가 들어 있어 Claude 가 그것을 보고 G1 이라고 답함. 같은 봉오리를 ⑤ 가 따로 추가해 두 번 세어짐.
- 대책 적용 후 (`max_box_px: 200` + 중복 정리, 같은 사진): 후보 73 → 14개, ③ 통과 6개, 검수 5개 버림·1개 남김, ⑤ 5개 추가, 중복 정리 0개(큰 박스가 ① 에서 이미 빠짐). 최종 봉오리 6개 모두 작은 박스, 표는 그대로 R1C1 G3 / R1C3 G1 / R2C4 G1. Claude 요청 24 → 18번.
- 결국 봉오리를 찾는 일은 대부분 ⑤ 의 Claude 가 하고, ①~③ 은 후보를 조금 보태는 정도입니다.

## 화분 단위 VLM (`detector.type: vlm_plant`)

YOLO 위치 검출·FastSAM·분류 모델을 쓰지 않고, **1단계에서 나눈 식물마다 Claude(VLM)가 봉오리·꽃의 위치와 등급을 직접** 판단합니다. 그 뒤(식물 등급 = 봉오리 중 최고 등급, 봉오리 없으면 `grade_zero` → 카메라 합치기 → 표·`result.json`·`result_buds.jpg`·`vis_*.jpg`)는 기존과 같습니다.

```
[1단계] 식물 마스크 (그대로)
   ▼  식물마다
식물 bbox + 여백 10% 크롭, 이웃 식물로 나뉜 픽셀만 어둡게(dim_outside 0.35), 이 식물 화분에 흰 원 → 긴 변 1024px
   ▼
Claude: {요약 한 문장, 봉오리·꽃마다 {등급 grade_one~four, 확신, 박스(0~1 비율)}}. 없으면 빈 목록
   ▼  확신 < min_conf(0.5) 버림, 박스를 카메라 좌표로 되돌림, 칸 = 그 식물 (배정 규칙 안 씀)
[화분 등급] grade_zero 에서 시작, 봉오리 중 최고 등급  →  [카메라 합치기] max  →  출력 (양식 동일)
```

- 요청 수 = 식물 수 × 카메라 수 (현재 6 × 2 = 12번). `max_workers` 개씩 동시에 보냅니다. 실행 끝에 `VLM 사용량` 이 출력됩니다.
- 등급 설명(`vlm.grade_descriptions`)과 예시 사진(`vlm_examples/`, 있으면)을 함께 보냅니다. 예시는 요청마다 같아 프롬프트 캐시로 재사용됩니다.
- 박스는 VLM 이 어림한 위치라 YOLO 보다 덜 정확할 수 있습니다. 등급 판단에는 박스가 쓰이지 않고 결과판 크롭·표시에만 쓰입니다.
- FastSAM 마스크가 없으므로 `vis_*.jpg` 에 흰 윤곽선이 없고, `result.json` 봉오리에 `det_conf`·`mask_px` 가 빠집니다.
- 어둡게 하는 범위: 꽃·봉오리 색(연분홍·주황 등)은 식물 색 마스크에 잘 안 들어가므로, 마스크 밖 전체가 아니라 **이웃 식물로 나뉜 픽셀만** 어둡게 합니다. 이 식물의 꽃이 가려지지 않게 하기 위해서입니다.
- 기존 방식으로 돌아가려면 `detector.type: hybrid`.

**VLM 판단 내용 보기** (봉오리가 없다고 답한 경우도 확인할 수 있게)

| 위치 | 내용 |
|---|---|
| `result_buds.jpg` | 칸 제목 아래 `VLM CL (n): 요약` — 카메라별로 VLM 이 남긴 봉오리 수와 한 문장 요약 |
| `vis_CL.jpg`, `vis_CR.jpg` | 칸 등급 아래 `VLM n buds`, 확신이 낮아 버린 봉오리는 회색 박스 `x G2 0.30` |
| `vlm/<카메라>_<칸>.jpg` | **VLM 에 실제로 보낸 식물 사진** + VLM 이 답한 박스(버린 것 회색) + 아래에 요약 |
| `table.txt`, 실행 출력 | `VLM 판단` 표: 칸·카메라별 요약과 답한 봉오리 전체(버린 것 `x`) |
| `result.json` | 칸마다 `vlm: {카메라: {summary, buds: [{class, conf, kept, xyxy}]}}` |

| 설정 (`grading.detector.vlm`) | 기본값 | 뜻 |
|---|---|---|
| `plant_image_size` | 1024 | 식물 사진 긴 변. 크면 작은 봉오리가 잘 보이지만 비쌈 |
| `dim_outside` | 0.35 | 그 식물이 아닌 픽셀 밝기 배율 (1 = 안 어둡게) |
| `min_conf` | 0.5 | 이보다 확신이 낮은 봉오리는 버림 |
| `max_workers` | 4 | 동시에 보내는 요청 수 |

## ③ 등급 분류를 VLM 으로 (`cls_backend`)

③ 등급 분류는 YOLO 분류 모델 대신 **VLM(Claude)** 이 맡을 수 있습니다. ①·②, 화분 배정, 카메라 합치기는 그대로입니다.

| `cls_backend` | ③ 등급 판단 | 장단점 |
|---|---|---|
| `yolo` (기본) | `cls_weights` 의 YOLO 분류 | 빠르고 무료. G2~G4 데이터가 적어 흔들림 |
| `vlm` | Claude 가 등급 설명 + 예시 사진을 보고 판단 | 학습 불필요, 처음 보는 오탐지(반사판·케이블·손)에 강함. 요금·인터넷 필요, 느림 |
| `cascade` | YOLO 먼저, 확신 < `cascade_below` 인 크롭만 VLM | 비용을 줄이면서 애매한 크롭만 VLM 으로 |

**동작:** 크롭을 `batch_size` 장씩 묶어 한 요청으로 보내고, 크롭마다 `{등급, 확신}` JSON 을 받습니다(형식은 structured outputs 로 고정). 등급 설명과 예시 사진은 요청마다 같은 앞부분이라 프롬프트 캐시로 재사용됩니다. 기존처럼 `not_bud` 이거나 확신 < `cls_conf` 이면 버립니다.

**쓰는 순서**

```
pip install anthropic
# 프로젝트 폴더에 ANTHROPIC_API_KEY.txt 를 만들고 키(sk-ant-...) 한 줄을 넣음 (공유 금지)
python tools/make_vlm_examples.py --data hybrid_data --per-class 4   # → vlm_examples/<클래스>/ (좋은 예시만 남기고 정리)
python tools/eval_classifier.py --backend yolo vlm cascade --per-class 30   # val 크롭으로 비교
```

`configs/pipeline.yaml` 에서 `cls_backend` 를 `vlm` 또는 `cascade` 로 바꾸면 `run_pipeline.py` 가 그대로 사용합니다.

**주의**

- **등급 설명(`vlm.grade_descriptions`)이 정확도를 좌우합니다.** 작업자 4 분류 기준(등급 = 꽃이 핀 정도, 1 닫힌 봉오리 → 2 피기 시작 → 3 반쯤 핌 → 4 만개)을 넣어 두었습니다. 기준이 바뀌면 여기를 고치세요.
- **시점 차이:** 기준 예시는 옆에서 찍은 근접 사진이지만 트레이 카메라는 위에서 내려다봅니다. 그래서 예시 사진(`vlm_examples/`)은 트레이 시점에 가까운 학습 크롭에서 고르는 것이 좋습니다.
- **VLM 의 확신은 모델이 스스로 매긴 값**이라 YOLO 확률과 다릅니다. `eval_classifier.py` 의 "cls_conf 별" 표를 보고 `cls_conf` 를 다시 맞추세요.
- **작은 크롭:** `image_size`(기본 256) 로 확대해 보내지만 원래 수십 px 인 봉오리는 정보가 부족할 수 있습니다.
- **비용:** 한 번 실행에 크롭 수십 장 수준입니다. 평가할 때는 `--per-class` 로 장수를 제한하세요. 실행 후 `VLM 사용량`(요청·토큰 수)이 출력됩니다.
- 예시 사진은 train 크롭에서, 평가는 val 크롭으로 하므로 서로 겹치지 않습니다.

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
| `rose_pipeline/plant_vlm.py` | 화분 단위 VLM `VlmPlantGrader` (`type: vlm_plant`) |
| `rose_pipeline/vlm.py` | ③ 분류기: `YoloGradeClassifier`, `VlmGradeClassifier`(Claude), `CascadeClassifier` |
| `tools/eval_classifier.py` | val 크롭으로 분류기 비교 (혼동 행렬, `cls_conf` 별 결과) |
| `tools/make_vlm_examples.py` | VLM 예시 사진 폴더 만들기 |
| `tests/test_hybrid.py` | 가짜 모델로 혼합형 검증 (모델·GPU 불필요, VLM 은 가짜 클라이언트로) |
| `rose_hybrid.ipynb` | 혼합형 Colab 노트북 (학습 단계 없음) |
| `rose_pipeline/api_key.py` | Claude API 키를 `ANTHROPIC_API_KEY.txt` 에서 읽기 (11/23) |
| `rose_pipeline/plant_vlm.py` → `VlmMissedFinder` | ⑤ Claude 누락 추가 (11/23) |
| `weights/yolov8m_best.pt`, `weights/yolo26m-cls_best.pt` | 학습된 ①·③ 모델 (11/23, 현재 기본) |
| `weights/yolov8m.pt`, `weights/yolo26m-cls.pt` | 학습 시작 가중치 (COCO·ImageNet 사전학습) (폴더에 없음 — 재학습할 때 ultralytics 가 내려받음) |
| `FastSAM-s.pt` | ② 마스크 모델 |
| `장미_생장단계_진행정리_20261123.pdf` | 진행 정리 (11/23 기준) |

## 한계

- **1단계 문제는 그대로입니다.** 혼합형은 2단계만 바꿉니다. 좌측 식물 영역 미인식 같은 1단계 문제는 따로 고쳐야 합니다.
  - 화분 마스크 밖 봉오리는 `max_overlap`으로 한 번 더 찾지만, 박스가 어느 화분 마스크와도 겹치지 않으면 버려집니다.
- **속도:** 화분마다 FastSAM을 한 번씩 돌립니다(카메라 2대 × 화분 6개). T4 GPU에서는 문제없지만 CPU에서는 느립니다.
- **작은 봉오리:** FastSAM이 잎과 구분하지 못할 수 있습니다. 이때도 마스크 없이 박스로 판단하므로 결과는 남습니다.
- **G1 어린 초록 봉오리:** 잎과 비슷하므로 ③의 정확도에 달려 있습니다. 혼동 행렬에서 `grade_one` ↔ `not_bud`를 확인하세요.
- **③ G2~G4 약함:** 학습된 분류 모델의 재현율이 G2 0.42 · G3 0.58 · G4 0.63 입니다 (한 단계 낮게 보는 경향). ④ Claude 검수가 보완하지만 검수를 끄면 그대로 나옵니다.
- **모델 비교 범위:** 분류 모델은 정확도만 비교했고, 9개 조합 외 다른 모델과는 비교하지 않았습니다.
- **Colab 미확인:** 11/23 설정(학습 모델 + API 키 파일)은 로컬(CPU)에서만 실행했습니다.
