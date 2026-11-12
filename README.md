# helpai — Rose_Growth

미니 장미 트레이 사진(카메라 2대)에서 화분별 **생장 단계**(`grade_zero` ~ `grade_four`)를 판단하는 파이프라인입니다.
코드는 [`rose_hybrid/`](rose_hybrid) 폴더에 있습니다.

- [1. 개요](#1-개요)
- [2. rose_hybrid 구성](#2-rose_hybrid-구성)
- [3. 사용법](#3-사용법)
- [4. 진행 정리](#4-진행-정리-기준일-2026-11-12)

---

## 1. 개요

- 트레이 3행×4열, 식물 6개(체크무늬), 카메라 2대(CL·CR)
- **1단계 · 각각의 미니 장미 구분**: 구멍마다 식물 유무 판단 → 식물이 있으면 그 식물 영역 세그멘테이션
- **2단계 · 생장 단계 판단**
    - 모든 식물에 기본 클래스 `grade_zero` 부여
    - 꽃봉오리가 보이면 `grade_one` ~ `grade_four`로 재할당 (뒤로 갈수록 높은 단계)
    - 한 식물에서 보이는 클래스 중 **가장 높은 클래스**가 그 식물의 생장 단계

2단계는 **혼합형**으로 동작합니다. 봉오리를 찾는 일과 등급을 매기는 일을 서로 다른 모델이 나눠 맡습니다.

| | 모델 | 하는 일 | 학습 |
| --- | --- | --- | --- |
| ① | YOLO 검출 (1클래스 `bud`) | 봉오리 **위치**만 찾음 | `train_hybrid.py det` |
| ② | FastSAM | 박스 안 봉오리 **마스크** | 안 함 (공개 가중치 `FastSAM-s.pt`) |
| ③ | YOLO 분류 | 봉오리 크롭의 **등급** (`not_bud` / G1~G4). `not_bud`는 버려 오탐지를 거름 | `train_hybrid.py cls` |

### 파이프라인 동작 순서

1. **식물 유무**: 구멍 중심 원 안의 어두운 픽셀 비율을 봅니다(빈 화분 = 검은 덮개). 카메라 평균이 0.38 미만이면 식물입니다. 위치가 고정이면 `presence.method: fixed`로 O/X 표를 씁니다.
2. **세그멘테이션**: 잎·꽃 색(HSV) 픽셀을 가장 가까운 '식물 있는 구멍'에 배정합니다.
3. **봉오리 위치**: 식물 영역을 잘라 YOLO(1클래스)로 검출합니다. 신뢰도 0.15 이상으로 넓게 잡습니다.
4. **봉오리 마스크**: FastSAM으로 박스 안 봉오리 모양을 따냅니다.
5. **등급 분류**: 봉오리 크롭을 분류 모델에 넣습니다. `not_bud`이거나 확신이 0.5 미만이면 버립니다.
6. **배정**: 봉오리 마스크와 가장 많이 겹치는 식물에 배정하고, 식물별 최고 등급을 고릅니다.
7. **카메라 합치기**: 두 카메라 중 높은 등급이 최종 등급입니다.
8. **출력**
    - 3×4 표: 등급 / 빈 구멍 `-` / 판단 불가 `?`
    - `result_buds.jpg`: 칸별 봉오리 사진판
    - `vis_CL.jpg`·`vis_CR.jpg`: 카메라별 표시

기존 방식(YOLO 한 모델이 위치와 등급을 함께 판단)은 설정에서 `detector.type: yolo`로 되돌릴 수 있습니다.

---

## 2. rose_hybrid 구성

```
rose_hybrid/
├── rose_hybrid.ipynb          Colab 노트북 (준비 → 보정 → 학습 → 실행 → 내려받기)
├── run_pipeline.py            실행 진입점 → output/<시각>/
├── train_hybrid.py            혼합형 학습: det(봉오리 위치) / cls(등급 분류)
├── train_detector.py          (기존 방식) YOLO 위치+등급 한 번에 학습
├── requirements.txt
├── configs/
│   ├── pipeline.yaml          전체 설정 (배치·범위표, 유무 판단, 세그멘테이션, 검출기, 배정·합치기 규칙)
│   └── holes.yaml             카메라별 구멍 중심 좌표 (보정 도구가 생성)
├── input/                     카메라 사진 CL.jpg, CR.jpg
├── rose_pipeline/
│   ├── pipeline.py            전체 연결 (RosePipeline), 결과 표·봉오리 개수 표
│   ├── layout.py              칸 이름(R행C열)·범위표·구멍 위치
│   ├── presence.py            1단계-1: 식물 유무 (dark_cover / fixed)
│   ├── segmentation.py        1단계-2: 식물 세그멘테이션 (nearest_pot)
│   ├── detection.py           봉오리 검출기 (hybrid / yolo / none)
│   ├── grading.py             2단계: 검출 → 배정 → 식물별 최고 등급
│   ├── assignment.py          봉오리 → 식물 배정 규칙 (mask_overlap / center_in_mask / max_overlap)
│   ├── fusion.py              두 카메라 등급 합치기 (max)
│   ├── sources.py             이미지 입력 (file / camera)
│   ├── visualize.py           결과 이미지·결과판·JSON 저장
│   └── config.py              설정 읽기·경로 처리
├── tools/
│   ├── calibrate_colab.py     Colab에서 클릭으로 구멍 위치 보정
│   ├── preview_holes.py       구멍 위치·빈 구멍 점수 미리보기
│   ├── prepare_hybrid_data.py Roboflow 형식 데이터셋 → 혼합형 학습 데이터(det/, cls/), 분류 크롭 중복 제거
│   └── mine_crops.py          실제 사진에서 분류 보강용 후보 크롭 수집
├── tests/
│   ├── test_pipeline.py       합성 트레이 이미지로 전체 흐름 검증 (모델 불필요)
│   └── test_hybrid.py         가짜 모델로 혼합형 검출기·배정·결과판 검증 (모델 불필요)
├── HYBRID.md                  혼합형 동작 원리·설정값·학습 데이터 설명
├── PIPELINE.md                파이프라인 흐름·사용 순서·알려진 한계
└── CODE_GUIDE.md              모듈별 코드 설명서
```

### 저장소에 없는 것

| 파일 | 이유 | 위치 |
| --- | --- | --- |
| `dataset.zip` (1.06 GB) | GitHub 파일 크기 제한(100 MB) 초과 | 구글 드라이브 `rose_hybrid/` |
| `dataset_v1.zip` (351 MB) | 위와 같음. 이전 데이터셋(Roboflow + 장미농장 타일) | 로컬 보관 |
| `runs/`, `output/`, `*.pt` | 학습·실행 결과물 | 실행 시 생성 |

`dataset.zip`은 `new_MR_growth/dataset_cp.zip`을 640×640 타일로 자른 Roboflow 형식 데이터셋입니다. 구성은 `data.yaml`, `train/`, `valid/`, `tiles_manifest.csv`입니다.

### 저장소의 사진·좌표에 대해

`configs/holes.yaml`과 `input/` 사진은 카메라 위치를 바꾸기 **전** 기준입니다. 새 위치로 보정한 `holes.yaml`과 새 사진은 구글 드라이브에 있습니다.

---

## 3. 사용법

### Colab (권장)

1. `dataset.zip`을 넣은 `rose_hybrid` 폴더를 구글 드라이브 **내 드라이브** 바로 아래에 올립니다. 노트북은 `MyDrive/rose_hybrid/dataset.zip`을 찾습니다.
2. `rose_hybrid.ipynb`를 Colab으로 엽니다. **런타임 → 런타임 유형 변경 → T4 GPU**를 선택합니다.
3. 위에서부터 셀을 차례로 실행합니다.

| 셀 | 하는 일 | 예상 시간 (T4) |
| --- | --- | --- |
| 0 | 드라이브 연결, `ultralytics` 설치, 동작 테스트 | 1~2분 |
| 1~3 | 입력 사진 확인, 구멍 위치 보정(처음 1회), 빈 구멍 점수 확인 | 몇 분 |
| 4 | 1단계만 실행 (검출 모델 없이) | 1분 |
| 5-1 | `dataset.zip` 풀기 → 혼합형 학습 데이터 생성 | 5~10분 |
| 5-2 | ① 봉오리 위치 검출 학습 (yolo11n, 100 epoch) | 약 1.5~3시간 |
| 5-3 | ③ 등급 분류 학습 (yolo11n-cls, 50 epoch) | 약 20~40분 |
| 6 | 전체 실행 → `result_buds.jpg`, `table.txt`, `vis_*.jpg` | 1~2분 |
| 7 | 결과 내려받기 | — |

- 학습 결과는 드라이브의 `runs/`에 저장됩니다. 다음부터는 5번을 건너뛰어도 됩니다.
- 학습이 끊기면 아래 명령으로 이어서 학습합니다.

```bash
yolo train resume model=runs/detect/rose_bud1/weights/last.pt
```

### 로컬 실행

```bash
pip install -r requirements.txt

# 합성 이미지 테스트 (모델 불필요)
python tests/test_pipeline.py
python tests/test_hybrid.py

# 1단계만 (검출 모델 없이, 모든 식물 = grade_zero)
python run_pipeline.py --no-detector

# 학습 데이터 준비 → 학습 (GPU 권장)
python tools/prepare_hybrid_data.py --data-root <dataset.zip 푼 폴더> --out hybrid_data
python train_hybrid.py det --data hybrid_data --device 0   # → runs/detect/rose_bud1/weights/best.pt
python train_hybrid.py cls --data hybrid_data --device 0   # → runs/classify/rose_grade/weights/best.pt

# 전체 실행
python run_pipeline.py                        # input/CL.jpg, input/CR.jpg
python run_pipeline.py --CL a.jpg --CR b.jpg  # 사진 직접 지정
```

### 결과 파일 (`output/<시각>/`)

| 파일 | 내용 |
| --- | --- |
| `result_buds.jpg` | 최종 결과판. 칸마다 최종 등급과 배정된 봉오리 사진 (높은 등급부터 칸당 8장) |
| `vis_CL.jpg`, `vis_CR.jpg` | 카메라 사진 위에 식물 마스크, 구멍(빨강 = 식물, 파랑 = 빈 구멍), 봉오리 박스·마스크 윤곽, 칸 등급 표시 |
| `table.txt` | 3×4 표 + 칸별·카메라별 봉오리 개수 |
| `result.json` | 칸별 유무 점수, 카메라별 등급, 봉오리 목록(등급·확신·위치 신뢰도·마스크 크기) |
| `masks/` | 카메라·칸별 식물 마스크 |

### 주요 설정 (`configs/pipeline.yaml`)

| 항목 | 기본값 | 뜻 |
| --- | --- | --- |
| `layout.coverage` | 모든 칸 `CL,CR` | 각 칸을 어느 카메라로 판단할지 |
| `presence.method` | `dark_cover` | `fixed`면 `layout.plants`의 O/X 표 사용 |
| `presence.empty_ratio` | 0.38 | 이 값 이상이면 빈 구멍 |
| `grading.detector.type` | `hybrid` | `hybrid` / `yolo`(기존) / `none` |
| `grading.detector.conf` | 0.15 | ① 위치 신뢰도 하한 |
| `grading.detector.cls_conf` | 0.5 | ③ 분류 확신 하한 |
| `grading.detector.sam_weights` | `FastSAM-s.pt` | `null`이면 FastSAM 생략(박스만) |
| `grading.assignment` | `mask_overlap` | 봉오리 → 식물 배정 규칙 |
| `grading.fusion` | `max` | 두 카메라 합치기 규칙 |

자세한 설명은 [`rose_hybrid/HYBRID.md`](rose_hybrid/HYBRID.md)에 있습니다.

---

## 4. 진행 정리 (기준일 2026-11-12)

원본: 「장미 잎 생장 탐지 모델 진행 - 이지후」(2026-10-15 ~ 2026-11-12) + 데이터 보강 작업

> 📌 **요약**: 판단 구조(식물 구분 → 봉오리 등급 → 식물별 최고 등급)는 완성했습니다.
> 11/12에 **새 학습 데이터셋(new_MR_growth)** 으로 바꾸고, 2단계를 **혼합형**으로 돌려 첫 결과를 얻었습니다.
> 카메라 위치를 옮긴 뒤 트레이 전체가 식물로 분할되고, 배경 G4 오탐지도 보이지 않습니다.
> 남은 과제는 세 가지입니다.
> - **같은 꽃의 등급이 카메라·크롭마다 다르게 나오는 문제**
> - **작은 빨간 물체를 G1으로 판정한 결과 확인**
> - **봉오리가 많은 사진으로 등급 판단 검증**

### 카메라 시야와 식물 위치

| 카메라 시야 | 1열 | 2열 | 3열 | 4열 |
| --- | --- | --- | --- | --- |
| 1행 | CR/CL | CR/CL | CR/CL | CR/CL |
| 2행 | CR/CL | CR/CL | CR/CL | CR/CL |
| 3행 | CR/CL | CR/CL | CR/CL | CR/CL |

10/15에는 4열을 CR만, R3C1을 CL만 봤습니다. 카메라 위치를 바꾼 뒤 모든 칸을 두 카메라가 봅니다. 코드의 범위표(`layout.coverage`)도 같게 맞췄습니다.

| 식물 위치 | 1열 | 2열 | 3열 | 4열 |
| --- | --- | --- | --- | --- |
| 1행 | O | X | O | X |
| 2행 | X | O | X | O |
| 3행 | O | X | O | X |

### 진행 경과

| 날짜 | 단계 | 내용 | 결과 |
| --- | --- | --- | --- |
| 10/15 | ① 초기 버전 | CL/CR 사진, 자동 유무 판단 | 동작은 하지만 식물끼리 분리가 안 됨 |
| 10/15 | ② 식물 분리 대안 | 가지치기 / 격벽 설치 | 가지치기는 시기상조, 격벽은 광량 부족으로 폐기 |
| 10/15 | ③ 화분 위치 고정 | `presence.method: fixed` | 유무 판단은 정확. 반사판·손·프레임 등 배경을 **G4로 크게 오탐지** → 등급 오판정 |
| 11/02 | ④ 학습 데이터 증가 | Roboflow 사진 추가 | R2C4 / R3C3 / R1C3 부근 G4 오탐지 지속 |
| 11/02 | ⑤ 실제 사진 추가 | 생육 사진 143장 라벨링 → 데이터셋 v3 | 아래 데이터 보강 참고 |
| 11/02 | ⑥ 최신 결과 | 새 CL/CR 카메라 사진 | G4 오탐지는 사라짐. **좌측 식물 영역 미인식**, 케이블 1개를 G1으로 오탐지 |
| 11/12 | ⑦ 새 데이터셋 + 혼합형 | new_MR_growth 데이터셋 적용, 혼합형 학습·실행 | 트레이 전체 분할, 배경 오탐지 없음. R1C1 = grade_three, R2C4 = grade_one, 나머지 식물 grade_zero |

### 보류 중인 항목

- [ ] `grade_two` + `grade_three` 클래스 통합 → 장미농원 방문 후 논의
- [ ] UI/UX에서 식물 위치 고정·변경 → 향후 추가 (현재는 설정 파일의 O/X 표)
- [x] 카메라 위치 변경 후 구멍 위치 재보정
- [x] 범위표(`layout.coverage`)를 새 카메라 시야(모든 칸 CR/CL)에 맞게 수정

### 데이터 보강 (⑤, 이전 데이터)

**생육 사진 143장 봉오리 라벨링**
- 색으로 후보를 뽑은 뒤 사람이 눈으로 보고 등급을 매겼습니다.
- **제거**: 봉오리가 아닌 것(화분·신발·테이프 등), 잎, 봉오리 일부만 잘린 것
- **추가**: 색이 안 보이는 초록 봉오리(G1), 노란 꽃, 일부만 잘렸던 봉오리(박스 다시 그림)
- 결과는 박스 **2,326개**(G1 1,431 / G2 639 / G3 125 / G4 131)입니다. 애매한 77개는 라벨에서 뺐습니다.
- 이후 미사용 사진 20장에서 봉오리 306개(G1 28 / G2 40 / G3 130 / G4 108)를 더 라벨링했습니다.

**Roboflow 데이터셋과 통합**
- 생육 사진을 640×640 타일(`farm_`)로 잘랐습니다.
- 같은 사진의 타일이 학습·검증에 섞이지 않도록 원본 사진 단위로 8:1:1 분할했습니다.

| 분할 | 이미지 | 박스 (G1 / G2 / G3 / G4) |
| --- | --- | --- |
| train | 2,744 | 2,335 / 1,283 / 562 / 248 |
| valid | 316 | 295 / 133 / 65 / 32 |
| test | 388 | 382 / 189 / 63 / 38 |

이 데이터는 `dataset_v1.zip`으로 보관하고 있으며, 이 데이터로는 학습하지 않고 ⑦로 넘어갔습니다.

### 새 학습 데이터셋 (⑦, new_MR_growth)

- **출처**: 장미농장 사진 **143장**을 새로 라벨링(클래스 4개)하고, G2~G4 크롭을 원본 사진에 붙인 **copy-paste 합성 사진**을 더했습니다.
- **클래스 대응**:

  | 원본 클래스 | 등급 |
  | --- | --- |
  | `class_0` | grade_four |
  | `class_1` | grade_one (초록 봉오리) |
  | `class_2` | grade_three |
  | `class_3` | grade_two |

- **분할**: 사진 5장 묶음 단위로 train 118장 / val 25장입니다. 합성 사진은 train 사진의 크롭만 씁니다. **test 분할은 없어서** val로 평가합니다.
- **타일**: 960 창을 640 타일로 잘라 train 7,943장 / valid 545장을 만들었습니다.

**등급 분류 모델용 크롭** (봉오리당 한 번만 자르도록 중복 제거)

| 분할 | G1 | G2 | G3 | G4 | not_bud |
| --- | --- | --- | --- | --- | --- |
| train | 8,643 | 1,182 | 1,009 | 963 | 11,855 |
| valid | 1,940 | 79 | 33 | 57 | 2,126 |

> 중복 제거 전에는 G1이 43,175개로 집계됐습니다. 같은 봉오리를 두 번 이상 셌기 때문입니다.
> - 합성 사진 2장이 원본 사진을 바탕으로 씁니다(×약 2.9).
> - 타일이 서로 겹칩니다(×약 1.7).
>
> 위치 검출용 데이터는 중복을 그대로 둡니다(박스 50,390개).

### 결과 (11/12)

| 칸 | 최종 등급 | 근거 |
| --- | --- | --- |
| R1C1 | grade_three | 주황 꽃 1송이: G3 0.82 (CL), G3 0.75 (CR), G1 0.69 (CR) |
| R1C3 | grade_zero | 봉오리 없음 |
| R2C2 | grade_zero | 봉오리 없음 |
| R2C4 | grade_one | 잎 사이 작은 빨간 물체: G1 0.97 (CR 한 카메라에서만) |
| R3C1 | grade_zero | 봉오리 없음 |
| R3C3 | grade_zero | 봉오리 없음 |
| 나머지 6칸 | empty | 빈 구멍 |

- 식물 6칸, 빈 구멍 6칸 모두 맞게 판단했습니다.
- 카메라를 옮긴 뒤 두 카메라 모두 트레이 전체가 식물 색으로 분할됩니다. ⑥의 좌측 미인식은 사진상 해소됐습니다.
- R1C1의 꽃은 빈 구멍 R2C1 위로 늘어져 있지만, 마스크 기준 배정으로 R1C1에 맞게 배정됐습니다.
- 반사판·프레임·케이블 오탐지는 없었습니다.

### 현재 남은 문제

| 문제 | 원인 (확인한 것) | 관련 항목 |
| --- | --- | --- |
| 같은 꽃의 등급이 다르게 나옴 | R1C1 주황 꽃이 G3 두 번, G1 한 번. 최종은 최고 등급이라 G3지만, 분류 모델이 같은 대상을 다르게 봄 | 분류 모델 / G2·G3 통합 논의 |
| R2C4 G1 판정 확인 필요 | 잎 사이 작은 빨간 물체(신뢰도 0.97, CR에서만). 실제 봉오리인지 사진으로 확인해야 함 | 오탐지 |
| 등급 판단 검증 부족 | 결과 사진에 봉오리가 2개뿐. 봉오리가 많은 시기의 사진이 필요 | — |
| 식물 간 분리 | 잎이 겹치면 '가장 가까운 구멍' 기준으로 직선 분할 (결과 사진에서도 대각선 경계가 보임) | 식물 분리 (격벽 폐기) |
| 등급 불균형·평가 데이터 부족 | 분류 크롭이 G1 8,643개, G2~G4는 각 약 1,000개. valid의 G2~G4는 33~79개라 등급별 수치가 흔들림 | grade_two·three 통합 논의 |

### 다음 단계 (권장 순서)

1. **다시 실행**: 바뀐 범위표(모든 칸 `CL,CR`)로 11/12 사진을 다시 실행합니다.
2. **결과 확인**: R2C4의 빨간 물체가 봉오리인지 원본 사진에서 확인합니다. 오탐지라면 `tools/mine_crops.py`로 `not_bud` 예시를 모아 분류 모델을 다시 학습합니다.
3. **분류 모델 점검**: 혼동 행렬에서 G1↔G3, G2↔G3 혼동을 확인합니다. 필요하면 `cls_conf`를 조정하거나 G2·G3을 통합합니다.
4. **봉오리가 보이는 트레이 사진 확보**: 등급 판단을 실제로 검증하고, 라벨링해서 학습 데이터와 평가 데이터(test)에 추가합니다.
5. **식물 분리 개선 (선택)**: 색 기준 대신 학습형 분할(SAM / YOLO-seg)로 겹친 잎을 분리합니다.

### 참고

- Roboflow 프로젝트: [rose_project_4](https://app.roboflow.com/task1-zjf7b/rose_project_4-hkazp-rasbj/browse)
- 추가 데이터 후보: [Roboflow Universe rose](https://universe.roboflow.com/sanjay-m-7nbqj/rose-tk4yv)
