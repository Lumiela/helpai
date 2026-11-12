# 코드 설명서 — 미니 장미 생장 단계 판단 파이프라인

> 이 폴더는 혼합형입니다. 2단계(봉오리 위치 → 마스크 → 등급)의 동작은 [HYBRID.md](HYBRID.md) 가 우선하며, 아래 5.3·5.5 절의 검출기·배정 설명은 기존 방식 기준입니다.
>
> 사용 순서(실행 명령)는 [PIPELINE.md](PIPELINE.md)를 보세요. 이 문서는 **코드가 어떻게 동작하는지**, **어디를 고치면 무엇이 바뀌는지**를 설명합니다.

---

## 1. 한눈에 보기

카메라 두 대(CL, CR)로 찍은 트레이 사진에서 화분별 미니 장미의 생장 단계를 판단합니다. 결과는 3행 × 4열 표로 나옵니다.

```
          1열          2열          3열          4열
1행   grade_two        -       grade_three       -
2행        -       grade_zero        -       grade_four
3행   grade_one        -       grade_three       -
```

- `grade_*` 는 식물이 있는 구멍의 생장 단계입니다.
- `-` 는 빈 구멍입니다.
- `?` 는 어느 카메라로도 판단할 수 없는 구멍입니다.

처리는 두 단계로 나뉩니다.

| 단계 | 하는 일 | 담당 모듈 |
|---|---|---|
| **1단계: 각각의 미니 장미 구분** | ① 구멍마다 식물이 있는지 판단<br>② 식물마다 픽셀 마스크 생성 | `presence.py`, `segmentation.py` |
| **2단계: 생장 단계 판단** | ① 꽃봉오리 검출<br>② 봉오리를 식물에 배정<br>③ 식물별 최고 등급<br>④ 두 카메라 결과 합치기 | `detection.py`, `assignment.py`, `grading.py`, `fusion.py` |

---

## 2. 폴더 구조

```
rose_Project_4.v1i.yolov11/
├── configs/
│   ├── pipeline.yaml        ← 모든 설정 (배치, 기준값, 규칙 선택)
│   └── holes.yaml           ← 카메라별 구멍 중심 좌표 (보정 도구가 생성)
├── rose_pipeline/           ← 핵심 패키지
│   ├── config.py            설정 읽기·경로 처리
│   ├── sources.py           이미지 입력 (파일 / 카메라)
│   ├── layout.py            칸 이름, 범위표, 구멍 위치 계산
│   ├── presence.py          1단계-①  식물 유무 판단
│   ├── segmentation.py      1단계-②  식물 세그멘테이션
│   ├── detection.py         2단계-①  봉오리 검출기
│   ├── assignment.py        2단계-②  봉오리 → 식물 배정 규칙
│   ├── grading.py           2단계-①②③ 검출 실행·배정·최고 등급
│   ├── fusion.py            2단계-④  카메라 결과 합치기
│   ├── pipeline.py          전체 연결 (RosePipeline) + 결과 자료형
│   └── visualize.py         시각화 이미지·JSON 저장
├── tools/
│   ├── calibrate_holes.py   구멍 보정 GUI (PC, Tkinter)
│   ├── calibrate_colab.py   구멍 보정 (Colab/Jupyter 셀 안에서 클릭)
│   ├── preview_holes.py     구멍 위치·빈 구멍 점수 미리보기
│   └── build_colab.py       Colab용 rose_colab/ 폴더 생성
├── tests/test_pipeline.py   합성 이미지로 전체 흐름 검증
├── run_pipeline.py          실행 진입점 (CLI)
├── train_detector.py        YOLOv11 봉오리 검출 모델 학습
├── input/                   CL.jpg, CR.jpg
├── train/ valid/ test/      학습 데이터 (Roboflow, YOLO 형식)
└── data.yaml                학습 데이터 클래스 정의
```

---

## 3. 핵심 개념

### 3.1 칸 이름: `R{행}C{열}`

모든 모듈은 구멍을 `R1C1` ~ `R3C4` 같은 문자열로 가리킵니다. 변환 함수는 `layout.cell_id(row, col)` 과 `layout.parse_cell_id("R2C3")` 입니다.

### 3.2 트레이 배치와 범위표

구멍은 3 × 4 = 12개이고, 식물은 체크무늬로 6개입니다. 식물 위치는 기본적으로 **사진에서 자동으로 판단**합니다(`presence.method: dark_cover`). `layout.plants` 의 O/X 표로 **고정**할 수도 있습니다(`presence.method: fixed`, 5.1절).
**범위표(coverage)** 는 "이 칸을 어느 카메라 결과로 판단할지"를 정합니다.

```yaml
# configs/pipeline.yaml
layout:
  rows: 3
  cols: 4
  coverage:
    - ["CL,CR", "CL,CR", "CL,CR", "CL,CR"]
    - ["CL,CR", "CL,CR", "CL,CR", "CL,CR"]
    - ["CL,CR", "CL,CR", "CL,CR", "CL,CR"]
```

`layout.coverage_map()` 이 이 표를 `{"R1C1": ["CL", "CR"], "R1C2": ["CL", "CR"], ...}` 형태로 바꿉니다.
한 카메라에만 보이는 칸이 있으면 `"CR"` 처럼 그 카메라만 적습니다 (예: 카메라 위치 변경 전에는 4열 = `"CR"`, R3C1 = `"CL"`).

### 3.3 구멍 좌표 (`holes.yaml`)

카메라마다 구멍 중심을 **정규화 좌표**(이미지 너비·높이 대비 0~1)로 저장합니다. 그래서 사진 해상도가 바뀌어도 그대로 쓸 수 있습니다.

```yaml
CL:
  R1C1: [0.3672, 0.0885]
  R1C4: [1.0, 0.1]        # 사진 가장자리 밖 → 1 이상도 허용
  ...
```

사진 밖 구멍도 좌표를 넣는 이유는 잎 섞임을 막기 위해서입니다. 그 식물의 잎이 사진 가장자리에 걸치면, 좌표가 없을 때는 이웃 식물로 잘못 배정됩니다. 자세한 내용은 5.2절에 있습니다.

`layout.camera_holes()` 는 정규화 좌표를 픽셀 좌표로 바꾸고, 구멍마다 두 값을 계산해 `Hole` 객체를 만듭니다.

- `spacing`: 가장 가까운 다른 구멍까지의 거리(px)입니다. 카메라가 비스듬해서 위치마다 구멍 간격이 다르기 때문에, 고정값 대신 구멍마다 계산합니다.
- `radius = spacing × radius_ratio` (기본 0.3): 식물 유무를 검사할 원의 반지름입니다.

### 3.4 등급 순서

```yaml
grading:
  grades: [grade_zero, grade_one, grade_two, grade_three, grade_four]
```

목록 순서가 곧 높낮이입니다. **첫 번째 항목(`grade_zero`)이 기본 클래스**이고, 봉오리가 없는 식물은 이 등급을 받습니다. 검출 모델은 `grade_one` ~ `grade_four` 4개 클래스만 학습합니다(`data.yaml`).

---

## 4. 전체 흐름 (`RosePipeline.run`)

`rose_pipeline/pipeline.py` 의 `run()` 이 아래 순서로 실행합니다.

```
images = {CL: 이미지, CR: 이미지}          ← sources.build_source(...).read()
holes  = {CL: {R1C1: Hole, ...}, CR: ...}  ← layout.camera_holes()

[1단계-①] presence = detect_presence(images, holes)
          칸마다: 범위표의 카메라 중 그 구멍이 보이는 카메라들의 "빈 구멍 점수" → 평균 → 식물 있음/없음

for 카메라 in (CL, CR):
    [1단계-②] plants = separate_plants(...)
              식물 있는 구멍 전부(범위 밖 포함)를 기준점으로, 식물 색 픽셀을 가장 가까운 구멍에 배정
    [2단계-①②③] grades, buds = grade_plants(image, plants)
              봉오리 검출 → 식물에 배정 → 이 카메라에서의 식물별 최고 등급

[2단계-④] 칸마다: 범위표의 카메라 등급들을 fusion 규칙(max)으로 합침
          → CellResult 12개 → PipelineResult
```

| 메서드 | 단계 | 입력 → 출력 |
|---|---|---|
| `detect_presence(images, holes)` | 1-① | → `{칸: (True/False/None, {카메라: 점수})}` |
| `separate_plants(camera, image, holes, presence)` | 1-② | → `[PlantSegment, ...]` |
| `grade_plants(image, plants)` | 2-①②③ | → `({칸: 등급}, [BudDetection, ...])` |
| `run(images=None)` | 전체 | → `PipelineResult` |

`run()` 에 이미지를 직접 넘기면 소스에서 읽지 않습니다. 테스트에서 합성 이미지를 넣을 때 이 방식을 씁니다.

---

## 5. 모듈별 설명

### 5.1 `presence.py` — 식물 유무 판단 (1단계-①)

**아이디어:** 빈 화분에는 검은 덮개가 보이고, 식물이 있는 화분은 가운데가 잎으로 덮여 있습니다.

`DarkCoverPresence` 의 동작은 다음과 같습니다.

1. `score(image, hole)`: 구멍 중심, 반지름 `radius` 원 안에서 HSV 명도 V ≤ `dark_v_max`(70)인 픽셀의 비율을 계산합니다. 이 값이 **빈 구멍 점수**입니다.
   - 원이 사진 밖에 있으면 `NaN` 을 돌려주고, 그 카메라는 판단에서 빠집니다.
2. `is_present(cell_id, scores)`: 범위표상 카메라들의 점수 평균이 `empty_ratio`(0.38)보다 작으면 식물이 있다고 판단합니다. 점수를 낸 카메라가 없으면 `None`(판단 불가)을 돌려줍니다.

현재 사진에서 측정한 점수는 다음과 같습니다.

| | 식물 있는 구멍 | 빈 구멍 |
|---|---|---|
| 빈 구멍 점수 | 0.02 ~ 0.31 | 0.46 ~ 0.87 |

**한계:** 빈 구멍이 이웃 잎에 완전히 덮이면 점수가 낮게 나와 식물로 오판합니다. 현재 사진의 CR R3C4가 그렇습니다(0.17).

**식물 위치 고정 (`FixedPresence`)**
식물 위치가 바뀌지 않는다면 O/X 표로 고정할 수 있습니다.

```yaml
layout:
  plants:              # O = 식물, X = 빈 구멍
    - [O, X, O, X]
    - [X, O, X, O]
    - [O, X, O, X]
presence:
  method: fixed
```

- 유무 판단은 표를 그대로 따릅니다.
- 빈 구멍 점수는 계속 계산합니다. `result.json` 의 `empty_scores` 에 남고, 사진 판단과 표가 다르면 경고를 출력합니다.
  - 예: `R3C4: 고정 설정은 빈 구멍, 사진 판단은 식물 (점수 0.17)`
  - 화분을 옮겼는데 표를 안 고친 경우를 알아챌 수 있습니다.
- 실제 사진에 적용하면 12칸 모두 표와 일치하고, R3C4 오판도 사라집니다. 표가 맞으니 세그멘테이션에서 가짜 식물이 이웃 잎을 가져가는 일도 없습니다.
- `tools/preview_holes.py` 는 고정 설정과 상관없이 **사진 판단**을 보여줍니다. 기준값 조정용이기 때문입니다.

### 5.2 `segmentation.py` — 식물 세그멘테이션 (1단계-②)

**문제:** 잎이 이웃 구멍 위로 넘어가 서로 겹치기 때문에, 칸 경계로 자르면 한 식물이 여러 칸에 나뉩니다.
**해결:** 칸 경계 대신, 식물 색 픽셀을 **가장 가까운 "식물 있는 구멍"** 에 배정합니다(보로노이 분할).

`NearestPotSegmenter.segment(image, camera, plant_holes)` 는 다음 순서로 동작합니다.

1. **축소:** 긴 변이 `work_max_side`(1280px)가 되도록 줄여서 계산합니다(속도).
2. **식물 색 마스크** (`plant_color_mask`): `hsv_ranges` 중 하나에 들어가는 픽셀을 모읍니다(잎 초록 + 빨강·분홍·노랑 꽃색). 그다음 열림/닫힘 연산으로 잡음을 정리합니다.
3. **가장 가까운 구멍 찾기:** 픽셀마다 각 구멍 중심까지의 거리를 계산해 가장 가까운 구멍 번호를 붙입니다.
   - 단, 거리 > `max_reach`(1.6) × `spacing` 인 픽셀은 어느 식물에도 배정하지 않습니다. 사진 가장자리에 찍힌 다른 트레이의 잎을 제외하기 위해서입니다.
4. **작은 조각 제거:** 구멍 원 면적 × `min_component_ratio`(0.02)보다 작은 조각은 버립니다.
5. **원래 해상도로 복원:** 마스크를 원본 크기로 되돌리고, 메모리를 아끼기 위해 **bbox 영역만 잘라서** 저장합니다.

**왜 범위 밖 구멍도 기준점으로 쓰는가:** `pipeline.separate_plants()` 는 범위표와 상관없이 "식물이 있는 구멍"을 전부 기준점으로 넘깁니다.
예를 들어 CL 사진 오른쪽 끝에 R2C4 식물이 일부 보입니다. R2C4를 기준점에서 빼면 그 잎이 R1C3이나 R3C3로 배정되어 버립니다. 다만 R2C4의 등급 판단에는 범위표대로 CR 결과만 씁니다.

결과 자료형 **`PlantSegment`** 는 다음과 같습니다.

| 필드/메서드 | 설명 |
|---|---|
| `cell_id`, `camera`, `hole` | 어느 칸의, 어느 카메라에서의 식물인지 |
| `mask`, `offset` | bbox 크기의 bool 마스크와 그 좌상단의 이미지 좌표 |
| `contains(x, y)` | 이미지 좌표가 마스크 안인지 (봉오리 배정에 사용) |
| `bbox()` | 마스크를 감싸는 사각형 (봉오리 검출용 잘라내기에 사용) |
| `overlap(xyxy)` | 박스 안에 들어오는 마스크 픽셀 수 (`max_overlap` 규칙에 사용) |

### 5.3 `detection.py` — 봉오리 검출기 (2단계-①)

| 클래스 | 설명 |
|---|---|
| `BudDetector` | 추상 클래스. `detect(image) -> [BudDetection]` |
| `YoloBudDetector` | `train_detector.py` 로 학습한 YOLOv11 가중치(`best.pt`)로 검출. ultralytics 는 이 클래스를 만들 때만 불러옴 |
| `NullDetector` | 항상 빈 결과. 검출 모델 없이 1단계만 확인할 때 사용 (`--no-detector`) |

**`BudDetection`** 의 필드는 `class_name`(등급 이름), `conf`(신뢰도), `xyxy`(이미지 좌표 박스), `cell_id`(배정된 칸, 배정 후 채워짐)입니다.

### 5.4 `grading.py` — 검출 실행·배정·최고 등급 (2단계-①②③)

- **`detect_buds(image, plants, detector, detect_on, crop_padding)`**
  - `plant_crop`(기본): 식물 마스크 bbox에 여백(10%)을 더해 잘라낸 뒤 검출기에 넣고, 결과 좌표를 원본 이미지 기준으로 되돌립니다.
    - 넓은 화각에서는 봉오리가 아주 작게 찍히므로, 잘라서 확대하면 검출에 유리합니다.
    - 이웃 식물의 잘라낸 영역이 겹치면 같은 봉오리가 두 번 잡힐 수 있어서, `dedupe()` 로 IoU ≥ 0.5인 중복을 지웁니다.
  - `full_image`: 이미지 전체를 한 번에 검출합니다.
- **`assign_buds(dets, plants, assigner)`**: 배정 규칙(5.5절)으로 봉오리마다 `cell_id` 를 채웁니다.
- **`decide_grades(plants, dets, grades)`**: 모든 식물을 `grades[0]` 으로 시작하고, 배정된 봉오리 중 **순위가 가장 높은 등급**으로 올립니다. `grades` 에 없는 클래스 이름은 경고를 내고 무시합니다.

### 5.5 `assignment.py` — 봉오리 → 식물 배정 규칙 (2단계-②)

규칙은 이름으로 등록하고, 설정의 `grading.assignment` 로 고릅니다.

| 이름 | 규칙 |
|---|---|
| `center_in_mask` (기본) | 봉오리 박스 **중심점**이 들어 있는 식물 마스크 |
| `max_overlap` | 봉오리 박스 안에 마스크 픽셀이 가장 많이 들어오는 식물 |

어느 식물에도 속하지 않으면 `None` 이 되고 등급 판단에서 빠집니다. 예를 들어 빈 구멍 위에 떠 있는 봉오리가 그렇습니다.

### 5.6 `fusion.py` — 카메라 결과 합치기 (2단계-④)

한 식물이 두 카메라에 모두 찍히면 카메라별 등급이 따로 나옵니다(`CellResult.camera_grades`). 현재 규칙은 **`max`** 로, 두 카메라 중 높은 등급을 씁니다. 한쪽에서 잎에 가려진 봉오리를 다른 쪽에서 볼 수 있기 때문입니다.

### 5.7 `sources.py` — 이미지 입력

| `source.type` | 클래스 | 설명 |
|---|---|---|
| `file` | `FileSource(path)` | 이미지 파일. 한글 경로도 읽히도록 `cv2.imdecode` 사용 |
| `camera` | `CameraSource(device, warmup_frames=5)` | `cv2.VideoCapture` 로 한 프레임 캡처. `device` 는 장치 번호 또는 RTSP/HTTP URL. 자동 노출이 안정되도록 앞 프레임 몇 장은 버림 |

카메라를 설치하면 설정만 바꾸면 됩니다.

```yaml
cameras:
  CL:
    source: {type: camera, device: 0}
  CR:
    source: {type: camera, device: "rtsp://192.168.0.11/stream"}
```

### 5.8 `pipeline.py` — 결과 자료형

**`CellResult`** (칸 하나)

| 필드 | 설명 |
|---|---|
| `cell_id`, `row`, `col` | 칸 |
| `cameras` | 범위표상 이 칸을 판단하는 카메라 |
| `present` | 식물이 있으면 `True`, 빈 구멍이면 `False`, 볼 수 있는 카메라가 없으면 `None` |
| `empty_scores` | `{카메라: 빈 구멍 점수}` |
| `camera_grades` | `{카메라: 그 카메라에서의 등급}` |
| `grade` | 최종 등급 (빈 구멍이면 `None`) |
| `buds` | `[(카메라, BudDetection), ...]` 이 칸에 배정된 봉오리 |
| `symbol` | 표에 찍을 문자: 등급 / `-` / `?` |

**`PipelineResult`** 는 `cells`(12개), `frames`(카메라별 중간 결과: 이미지, 구멍, 식물 마스크, 봉오리)를 가집니다. 메서드는 다음과 같습니다.
- `grid()`: 행×열 2차원 목록
- `table()`: 콘솔용 표 문자열
- `to_dict()`: JSON용 dict

### 5.9 `visualize.py` — 저장

`save_outputs(result, out_dir)` 는 `output/<시각>/` 아래에 다음 파일을 만듭니다.

| 파일 | 내용 |
|---|---|
| `result.json` | 표 + 칸별 상세 (아래 예시) |
| `table.txt` | 콘솔 표와 같은 내용 |
| `vis_CL.jpg`, `vis_CR.jpg` | 시각화 이미지 (아래 설명) |
| `masks/<카메라>_<칸>.png` | 식물별 마스크 (흰색 = 식물) |

시각화 이미지에 그려지는 것은 다음과 같습니다.
- 식물 마스크: 칸마다 다른 색으로 칠합니다.
- 구멍 원: 빨강 = 식물, 파랑 = 빈 구멍, 회색 = 이 카메라 범위 밖
- 봉오리 박스와, 그 봉오리가 배정된 칸
- 칸별 등급

```json
{
  "timestamp": "2026-10-15T18:59:44",
  "table": [["grade_zero", "-", "grade_zero", "-"], ["-", "grade_zero", "-", "grade_zero"], ...],
  "cells": [
    {
      "cell": "R1C1", "row": 1, "col": 1, "cameras": ["CL", "CR"],
      "present": true, "grade": "grade_zero",
      "empty_scores": {"CL": 0.064, "CR": 0.314},
      "camera_grades": {"CL": "grade_zero", "CR": "grade_zero"},
      "buds": []
    },
    {
      "cell": "R1C2", "row": 1, "col": 2, "cameras": ["CL", "CR"],
      "present": false, "grade": null,
      "empty_scores": {"CL": 0.694, "CR": 0.867},
      "camera_grades": {}, "buds": []
    }
  ]
}
```

봉오리가 있으면 `buds` 에 `{"camera": "CR", "class": "grade_three", "conf": 0.81, "xyxy": [x1, y1, x2, y2]}` 형태로 들어갑니다.

### 5.10 `config.py`

| 함수 | 설명 |
|---|---|
| `PROJECT_ROOT` | 프로젝트 루트. 설정 속 상대 경로는 모두 이곳 기준 |
| `resolve(path)` | 상대 경로를 절대 경로로 변환 |
| `load_config(path)` | `pipeline.yaml` 을 읽고 `holes.yaml` 을 `cfg["holes"]` 에 합쳐서 반환 |

---

## 6. 설정 항목 정리 (`configs/pipeline.yaml`)

| 항목 | 기본값 | 의미 / 조정 시점 |
|---|---|---|
| `layout.rows`, `cols`, `coverage` | 3, 4, 범위표 | 트레이·카메라 배치가 바뀌면 |
| `cameras.<이름>.source` | `file` | 카메라 연결 시 `camera` 로 |
| `radius_ratio` | 0.3 | 유무 검사 원 크기. 원이 화분 밖까지 나가면 줄임 |
| `layout.plants` | 체크무늬 O/X | 식물 위치 표. `presence.method: fixed` 일 때만 사용 |
| `presence.method` | `dark_cover` | `dark_cover` = 사진에서 자동 판단, `fixed` = `layout.plants` 표로 고정 |
| `presence.dark_v_max` | 70 | "어두운 픽셀" 기준. 조명이 어두워지면 낮춤 |
| `presence.empty_ratio` | 0.38 | 빈 구멍 판단 기준. 식물을 빈 구멍으로 오판하면 올리고, 반대면 내림 |
| `segmentation.hsv_ranges` | 잎·꽃 색 | 잎·꽃이 덜 잡히면 범위를 넓힘 (흰 꽃은 현재 안 잡힘) |
| `segmentation.max_reach` | 1.6 | 가지가 길게 뻗은 식물이 잘리면 올리고, 다른 트레이 잎이 섞이면 내림 |
| `segmentation.min_component_ratio` | 0.02 | 잡음 조각 제거 기준 |
| `grading.detector.type` | `yolo` | `none` = 검출 생략 |
| `grading.detector.weights` | `runs/detect/rose_bud/weights/best.pt` | 다른 모델을 쓸 때 |
| `grading.detector.conf` | 0.25 | 오검출이 많으면 올림 |
| `grading.detector.detect_on` | `plant_crop` | `full_image` 와 비교해 볼 때 |
| `grading.assignment` | `center_in_mask` | 5.5절 |
| `grading.fusion` | `max` | 5.6절 |

---

## 7. 확장하는 방법

모든 교체 지점은 **"클래스/함수를 만든다 → 등록한다 → 설정 이름을 바꾼다"** 의 같은 패턴입니다.

| 바꿀 것 | 파일 | 등록 위치 | 설정 키 |
|---|---|---|---|
| 봉오리 배정 규칙 | `assignment.py` | `@register("이름")` | `grading.assignment` |
| 카메라 합치기 규칙 | `fusion.py` | `@register("이름")` | `grading.fusion` |
| 식물 유무 판단 | `presence.py` | `PRESENCE` dict | `presence.method` |
| 세그멘테이션 | `segmentation.py` | `SEGMENTERS` dict | `segmentation.method` |
| 봉오리 검출기 | `detection.py` | `DETECTORS` dict | `grading.detector.type` |
| 이미지 입력 | `sources.py` | `SOURCES` dict | `cameras.*.source.type` |

**예 1) 배정 규칙 추가:** 봉오리 중심에서 가장 가까운 구멍의 식물에 배정하는 규칙입니다.

```python
# rose_pipeline/assignment.py
import numpy as np

@register("nearest_hole")
def nearest_hole(det, plants):
    cx, cy = det.center
    return min(plants, key=lambda p: np.hypot(p.hole.center[0] - cx, p.hole.center[1] - cy), default=None)
```

```yaml
grading:
  assignment: nearest_hole
```

**예 2) 합치기 규칙 추가:** 두 카메라 중 낮은 등급을 쓰는 보수적인 규칙입니다.

```python
# rose_pipeline/fusion.py
@register("min")
def fuse_min(camera_grades, grades):
    return min(camera_grades.values(), key=grades.index)
```

**예 3) 유무 판단을 학습 모델로 교체:** 구멍 영역 이미지를 "식물/빈 구멍"으로 분류하는 모델입니다.

```python
# rose_pipeline/presence.py
class ClassifierPresence(PresenceDetector):
    def __init__(self, weights, threshold=0.5, **_):
        self.model = ...                       # 분류 모델 불러오기

    def score(self, image, hole):              # 빈 구멍일 확률 (0~1)
        crop = ...                             # hole.center, hole.radius 로 잘라내기
        return float(self.model(crop))

    def is_present(self, cell_id, scores):     # True / False / None(판단 불가)
        valid = [s for s in scores if not np.isnan(s)]
        return bool(np.mean(valid) < self.threshold) if valid else None

PRESENCE["classifier"] = ClassifierPresence
```

```yaml
presence:
  method: classifier
  weights: runs/presence/best.pt
```

설정 블록의 값은 클래스 생성자에 키워드 인자로 그대로 전달됩니다(`**cfg`). 모르는 키는 각 클래스의 `**_` 가 받아서 무시합니다. 유무 판단 클래스에는 `layout` 도 함께 전달됩니다.

---

## 8. 도구와 스크립트

| 파일 | 하는 일 |
|---|---|
| `run_pipeline.py` | 전체 실행. `--CL`/`--CR` 로 이미지를 지정하고, `--no-detector` 로 검출을 생략 |
| `train_detector.py` | 학습 실행 (아래 설명) |
| `tools/calibrate_holes.py` | 구멍 보정 GUI (아래 설명) |
| `tools/calibrate_colab.py` | 구멍 보정 Colab판 (아래 설명) |
| `tools/preview_holes.py` | 구멍 원 + 빈 구멍 점수를 그린 `output/holes_preview_*.jpg` 와 콘솔 출력. 기준값 조정용 |
| `tools/build_colab.py` | Colab 폴더 생성 (아래 설명) |

**`train_detector.py`**
- Roboflow `data.yaml` 의 `../train/images` 상대 경로 문제를 피하려고, 절대 경로로 바꾼 `configs/rose_data.yaml` 을 만든 뒤 YOLOv11을 학습합니다.
- `--data-root` 로 데이터셋 위치를 바꿀 수 있습니다(Colab).
- 학습 결과는 `runs/detect/rose_bud/` 에 저장됩니다.

**`tools/calibrate_holes.py`** (Tkinter)
- R1C1부터 순서대로 클릭하고, 안 보이는 구멍은 `n` 으로 건너뜁니다.
- 드래그로 점을 고치고, `BackSpace` 로 되돌리고, `Enter` 로 저장합니다.
- 회색 여백을 클릭하면 사진 밖 구멍을 지정할 수 있습니다.

**`tools/calibrate_colab.py`**
- 같은 기능을 HTML 캔버스로 구현했습니다.
- Colab에서는 `google.colab.kernel.invokeFunction` 으로 Python 쪽에 좌표를 넘겨 `holes.yaml` 에 저장합니다.
- 일반 Jupyter에서는 저장 대신 붙여넣을 YAML을 보여줍니다.

**`tools/build_colab.py`**
- `rose_colab/` 폴더를 만듭니다. 내용은 코드 복사본, 설정, 입력 사진, `dataset.zip`, 노트북입니다.
- 노트북 내용은 이 스크립트의 `NOTEBOOK_CELLS` 에 정의되어 있습니다.
- 이 스크립트가 만든 폴더(표시 파일 `.built_by_build_colab`)만 덮어씁니다.

---

## 9. 테스트 (`tests/test_pipeline.py`)

실제 모델 없이 전체 흐름을 검증합니다.

1. **합성 트레이:** 정면 기준으로 3 × 4 구멍과 체크무늬 식물 6개를 그리고, 봉오리는 등급별로 다른 색 원으로 그립니다.
2. **원근 변환:** CL(1~3열)·CR(2~4열) 시점으로 비스듬하게 변환합니다. 구멍 좌표는 변환 행렬로 정확히 계산하며, 사진 밖 좌표도 포함합니다.
3. **가림 상황:** R3C3에는 CR에서만 보이는 `grade_three` 봉오리를 넣어 가림 상황을 흉내 냅니다. 이 경우 CL은 `grade_one`, CR은 `grade_three` 이고, 최종 결과는 `max` 규칙에 따라 `grade_three` 여야 합니다.
4. **가짜 검출기:** `ColorBudDetector` 가 YOLO 대신 봉오리 색으로 박스를 만듭니다.
5. **조합 검증:** 배정 규칙 2가지 × 검출 방식 2가지, 모두 4조합에서 3 × 4 결과가 기대값과 같은지 확인합니다.

```bash
python tests/test_pipeline.py              # "통과: 3×4 모든 칸이 기대 결과와 일치"
python tests/test_pipeline.py --save out   # 합성 이미지와 시각화 저장
```

---

## 10. 알려진 한계와 개선 방향

| 한계 | 원인 | 개선 방향 |
|---|---|---|
| 잎에 완전히 덮인 빈 구멍을 식물로 오판 (현재 CR R3C4) | 색 기반 판단이라 덮개가 안 보이면 구분 불가 | 식물 위치가 고정이면 `presence.method: fixed` (5.1절)<br>자동 판단이 필요하면 구멍 영역 "식물/빈 구멍" 분류 모델 학습 (7절 예 3) |
| 빈 구멍 판단 기준의 여유가 작음 (식물 최대 0.31 / 빈 구멍 최소 0.46) | 조명·그림자 영향 | 조명이 바뀌면 `preview_holes.py` 로 점수를 보고 `empty_ratio` 재조정 |
| 겹친 잎의 경계가 근사치 | "가까운 구멍" 기준 분할 | SAM 등 세그멘테이션 모델로 교체 (`SEGMENTERS` 에 등록) |
| 2단계 정확도 미검증 | 학습 데이터가 일반 장미 근접 사진. 실제 트레이 사진과 다름 | 실제 트레이 사진의 봉오리 라벨링 후 재학습 |
| 흰 꽃이 식물 마스크에 안 잡힘 | HSV 범위가 채도 기준 | 흰색 범위 추가 (단, 흰 배경과 섞이지 않게 주의) |
