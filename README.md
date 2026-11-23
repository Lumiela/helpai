# helpai — Rose_Growth

미니 장미 트레이 사진(카메라 2대)에서 화분별 **생장 단계**(`grade_zero` ~ `grade_four`)를 판단하는 파이프라인입니다.
코드는 [`rose_hybrid/`](rose_hybrid) 폴더에 있습니다. 진행 정리 문서: [`장미_생장단계_진행정리_20261123.pdf`](rose_hybrid/장미_생장단계_진행정리_20261123.pdf)

- [1. 개요](#1-개요)
- [2. rose_hybrid 구성](#2-rose_hybrid-구성)
- [3. 사용법](#3-사용법)
- [4. 진행 정리](#4-진행-정리-기준일-2026-11-23)

---

## 1. 개요

- 트레이 3행×4열, 식물 6개(체크무늬), 카메라 2대(CL·CR)
- **1단계 · 각각의 미니 장미 구분**: 구멍마다 식물 유무 판단 → 식물이 있으면 그 식물 영역 세그멘테이션
- **2단계 · 생장 단계 판단**
    - 모든 식물에 기본 클래스 `grade_zero` 부여
    - 꽃봉오리가 보이면 `grade_one` ~ `grade_four`로 재할당 (뒤로 갈수록 높은 단계, 꽃이 핀 정도)
    - 한 식물에서 보이는 클래스 중 **가장 높은 클래스**가 그 식물의 생장 단계

> **약어**: G1 = grade_one, G2 = grade_two, G3 = grade_three, G4 = grade_four
> (이 문서와 결과 이미지의 `G1`~`G4` 표기는 모두 이 클래스를 뜻합니다.)

2단계는 **혼합형**으로 동작합니다. 봉오리를 찾는 일과 등급을 매기는 일을 서로 다른 모델이 나눠 맡고, 마지막에 Claude가 결과를 검수합니다.

| | 모델 (파일) | 하는 일 | 학습 |
| --- | --- | --- | --- |
| ① | **YOLOv8m** 검출, 1클래스 `bud` (`weights/yolov8m_best.pt`) | 봉오리 **위치**만 찾음 | 완료 (30 epoch) |
| ② | FastSAM (`FastSAM-s.pt`) | 박스 안 봉오리 **마스크** | 안 함 (공개 가중치) |
| ③ | **YOLO26m-cls** 분류 (`weights/yolo26m-cls_best.pt`) | 봉오리 크롭의 **등급** (`not_bud` / G1~G4). `not_bud`는 버려 오탐지를 거름 | 완료 (30 epoch) |
| ④ | Claude 검수 (API) | ③이 남긴 봉오리를 다시 판단해 최종 등급으로 씀. 아니면 버림 | 안 함 |
| ⑤ | Claude 누락 추가 (API) | 식물마다 모델이 놓친 봉오리를 찾아 추가 | 안 함 |

모델 파일이 모두 저장소에 있어 **학습 없이 바로 실행**합니다. ④⑤를 끄면(`review: false`, `add_missed: false`) Claude API 없이 모델만으로 동작합니다.

### 모델 선정 이유 (2026-11-23)

YOLO11 · YOLOv8 · YOLO26 계열의 n/s/m 9개 조합을 같은 데이터로 30 epoch씩 학습해 비교했습니다 (탐지 val 545장·봉오리 3,549개, 분류 val 4,235장).

| 기준 | 선택 | 값 |
| --- | --- | --- |
| 탐지 · 오탐을 줄이는 게 중요 (Precision, 정밀도) | **YOLOv8m** | 0.744 (1위) |
| 탐지 · 놓치는 물체를 줄이는 게 중요 (Recall, 재현율) | **YOLOv8m** | 0.703 (1위) |
| 탐지 · 상자 위치를 정밀하게 맞추는 게 중요 (mAP50-95) | **YOLOv8m & YOLO11m** | 0.310 (m 크기 중 공동 1위. 전체로는 YOLO26s 0.317) |
| 분류 (top-1 정확도) | **YOLO26m-cls** | 0.970 (1위) |
| **결론** | **YOLOv8m + YOLO26m-cls** | |

<details>
<summary>9개 조합 전체 비교표</summary>

| 버전 | 탐지 모델 | 정밀도 | 재현율 | F1(계산) | mAP50 | mAP50-95 | 학습 GPU 메모리 | 분류 모델 | top-1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1 | YOLO11n | 0.735 | 0.643 | 0.686 | 0.701 | 0.303 | 3.62 GB | YOLO11n-cls | 0.959 |
| v2 | YOLO11m | 0.686 | 0.658 | 0.672 | 0.693 | 0.310 | 10.5 GB | YOLO11m-cls | 0.964 |
| v3 | YOLO11s | 0.719 | 0.653 | 0.684 | 0.713 | 0.309 | 5.45 GB | YOLO11s-cls | 0.961 |
| v4 | YOLOv8n | 0.723 | 0.649 | 0.684 | 0.682 | 0.292 | 2.91 GB | YOLOv8n-cls | 0.962 |
| **v5** | **YOLOv8m** | **0.744** | **0.703** | **0.723** | 0.712 | 0.310 | 7.61 GB | YOLOv8m-cls | 0.967 |
| v6 | YOLOv8s | 0.718 | 0.688 | 0.703 | 0.701 | 0.306 | 5.07 GB | YOLOv8s-cls | 0.963 |
| v7 | YOLO26n | 0.737 | 0.666 | 0.700 | 0.692 | 0.305 | 3.66 GB | YOLO26n-cls | 0.962 |
| **v8** | YOLO26m | 0.716 | 0.686 | 0.701 | 0.705 | 0.308 | 11.3 GB | **YOLO26m-cls** | **0.970** |
| v9 | YOLO26s | 0.738 | 0.684 | 0.710 | 0.721 | 0.317 | 6.05 GB | YOLO26s-cls | 0.967 |

F1 = 2×정밀도×재현율/(정밀도+재현율)로 계산했습니다(로그에 없음). 탐지에는 정확도가, 분류에는 정밀도·재현율·F1이 로그에 없습니다.
</details>

### 파이프라인 동작 순서

1. **식물 유무**: `layout.plants`의 O/X 고정 표를 씁니다(`presence.method: fixed`). 사진 판단(구멍 원 안 어두운 픽셀 비율)은 참고로 계산해 다르면 경고만 냅니다.
2. **세그멘테이션**: 잎·꽃 색(HSV) 픽셀을 가장 가까운 '식물 있는 구멍'에 배정합니다.
3. **① 봉오리 위치**: 식물 영역을 잘라 YOLOv8m으로 검출합니다(신뢰도 0.15 이상). 긴 변 200px 또는 식물 크롭의 30%보다 큰 박스는 버립니다.
4. **② 봉오리 마스크**: FastSAM으로 박스 안 봉오리 모양을 따냅니다.
5. **③ 등급 분류**: 봉오리 크롭을 YOLO26m-cls에 넣습니다. `not_bud`이거나 확신이 0.5 미만이면 버립니다.
6. **④ Claude 검수**: 남은 크롭을 ③ 결과를 알려 주지 않고 Claude가 다시 판단합니다. `not_bud`이거나 확신 0.6 미만이면 버립니다.
7. **배정 → ⑤ Claude 누락 추가**: 봉오리 마스크와 가장 많이 겹치는 식물에 배정한 뒤, 식물마다 이미 찾은 봉오리를 청록색 박스로 그린 사진을 Claude에게 보내 놓친 봉오리만 추가합니다.
8. **식물 등급 → 카메라 합치기**: 식물별 최고 등급, 두 카메라 중 높은 등급이 최종입니다.
9. **출력**: 3×4 표(등급 / 빈 구멍 `-` / 판단 불가 `?`), `result_buds.jpg`(칸별 봉오리 사진판), `vis_CL.jpg`·`vis_CR.jpg`, Claude 판단 내용(`vlm/`), 사용량(요청·토큰)

다른 방식은 설정에서 고를 수 있습니다: `detector.type: vlm_plant`(Claude가 식물마다 직접 판단), `detector.type: yolo`(기존 YOLO 한 모델).

---

## 2. rose_hybrid 구성

```
rose_hybrid/
├── rose_hybrid.ipynb          Colab 노트북 (준비 → 보정 → 실행 → 내려받기, 학습 단계 없음)
├── run_pipeline.py            실행 진입점 → output/<시각>/
├── weights/
│   ├── yolov8m_best.pt        ① 학습된 봉오리 위치 모델 (현재 기본)
│   └── yolo26m-cls_best.pt    ③ 학습된 등급 분류 모델 (현재 기본)
├── FastSAM-s.pt               ② 봉오리 마스크 모델
├── ANTHROPIC_API_KEY.txt      Claude API 키 (직접 만듦 · 저장소에 없음)
├── train_hybrid.py            (선택) 혼합형 재학습: det(봉오리 위치) / cls(등급 분류)
├── train_detector.py          (기존 방식) YOLO 위치+등급 한 번에 학습
├── requirements.txt
├── configs/
│   ├── pipeline.yaml          전체 설정 (배치·범위표, 유무 판단, 세그멘테이션, 검출기, Claude, 배정·합치기 규칙)
│   └── holes.yaml             카메라별 구멍 중심 좌표 (보정 도구가 생성)
├── input/                     카메라 사진 CL.jpg, CR.jpg
├── rose_pipeline/
│   ├── pipeline.py            전체 연결 (RosePipeline), 결과 표·봉오리 개수 표
│   ├── layout.py              칸 이름(R행C열)·범위표·구멍 위치
│   ├── presence.py            1단계-1: 식물 유무 (dark_cover / fixed)
│   ├── segmentation.py        1단계-2: 식물 세그멘테이션 (nearest_pot)
│   ├── detection.py           봉오리 검출기 (hybrid / vlm_plant / yolo / none), Claude 검수·중복 정리
│   ├── vlm.py                 ③ 등급 분류기 (yolo / embed / Claude / cascade)
│   ├── plant_vlm.py           Claude 화분 단위 판단, ⑤ 누락 추가
│   ├── api_key.py             Claude API 키 읽기 + 사용법 설명
│   ├── grading.py             2단계: 검출 → 배정 → 식물별 최고 등급
│   ├── assignment.py          봉오리 → 식물 배정 규칙 (mask_overlap / center_in_mask / max_overlap)
│   ├── fusion.py              두 카메라 등급 합치기 (max)
│   ├── sources.py             이미지 입력 (file / camera)
│   ├── visualize.py           결과 이미지·결과판·JSON 저장
│   └── config.py              설정 읽기·경로 처리
├── tools/
│   ├── calibrate_colab.py     Colab에서 클릭으로 구멍 위치 보정
│   ├── preview_holes.py       구멍 위치·빈 구멍 점수 미리보기
│   ├── prepare_hybrid_data.py Roboflow 형식 데이터셋 → 혼합형 학습 데이터(det/, cls/)
│   ├── mine_crops.py          실제 사진에서 분류 보강용 후보 크롭 수집
│   ├── eval_classifier.py     val 크롭으로 분류기 평가 (혼동 행렬)
│   └── make_vlm_examples.py   Claude 예시 사진·참고 크롭 만들기
├── tests/
│   ├── test_pipeline.py       합성 트레이 이미지로 전체 흐름 검증 (모델 불필요)
│   └── test_hybrid.py         가짜 모델·가짜 Claude로 혼합형 검증 (모델·API 불필요)
├── HYBRID.md                  혼합형 동작 원리·설정값·Claude API 사용법·모델 선정 이유
├── PIPELINE.md                파이프라인 흐름·사용 순서·알려진 한계
├── CODE_GUIDE.md              모듈별 코드 설명서
└── 장미_생장단계_진행정리_20261123.pdf   진행 정리 (11/23 기준)
```

### 저장소에 없는 것

| 파일 | 이유 | 위치 |
| --- | --- | --- |
| `ANTHROPIC_API_KEY.txt` | **Claude API 키 — 공유 금지** (`.gitignore`) | 각자 만듦 (아래 3절) |
| `dataset.zip` (1.06 GB) | GitHub 파일 크기 제한(100 MB) 초과. 재학습할 때만 필요 | 구글 드라이브 `rose_hybrid/` |
| `dataset_v1.zip` (351 MB) | 위와 같음. 이전 데이터셋(Roboflow + 장미농장 타일) | 로컬 보관 |
| `runs/`, `output/` | 학습·실행 결과물 | 실행 시 생성 |
| `cls_reference/` | 등급 참고 크롭 (현재 기본 설정에서 안 씀, 데이터셋에서 다시 만듦) | 로컬 보관 |

`configs/holes.yaml`과 `input/` 사진은 11/14 카메라 위치 변경 후 기준입니다.

---

## 3. 사용법

### Claude API 키 (먼저 준비)

Claude 검수(④)·누락 추가(⑤)는 Claude API를 씁니다. **11/23부터 키를 직접 입력하지 않고 파일에서 읽습니다.**

1. [console.anthropic.com](https://console.anthropic.com) → API Keys에서 키(`sk-ant-...`)를 발급합니다. 사용량만큼 요금이 나갑니다(11/19 사진 기준 약 20번 요청/실행).
2. `rose_hybrid/` 폴더에 `ANTHROPIC_API_KEY.txt`를 만들고 **키 한 줄만** 넣습니다.
3. 파일이 없으면 `ANTHROPIC_API_KEY` 환경 변수를 씁니다. Claude 없이 모델만 쓰려면 `configs/pipeline.yaml`에서 `review: false`, `add_missed: false`로 바꿉니다(키 불필요).

자세한 설명은 [`rose_pipeline/api_key.py`](rose_hybrid/rose_pipeline/api_key.py) 맨 위에 있습니다. **키 파일은 깃에 올리거나 공유하지 마세요.**

### Colab (권장)

1. `rose_hybrid` 폴더에 `ANTHROPIC_API_KEY.txt`를 넣고 구글 드라이브 **내 드라이브** 바로 아래에 올립니다.
2. `rose_hybrid.ipynb`를 Colab으로 열고 위에서부터 셀을 실행합니다. GPU는 없어도 됩니다.

| 셀 | 하는 일 |
| --- | --- |
| 0 | 드라이브 연결, `ultralytics`·`anthropic` 설치, 모델 파일·API 키 확인 |
| 1~3 | 입력 사진 확인, 구멍 위치 보정(처음 1회), 빈 구멍 점수 확인 |
| 4 | 1단계만 실행 (검출 모델 없이) |
| 5 | 전체 실행 (학습된 모델 바로 적용) → `result_buds.jpg`, `table.txt`, `vis_*.jpg`, `vlm/` |
| 6 | 결과 내려받기 |

### 로컬 실행

```bash
pip install -r requirements.txt

# 합성 이미지 테스트 (모델·API 불필요)
python tests/test_pipeline.py
python tests/test_hybrid.py

# 1단계만 (검출 모델 없이, 모든 식물 = grade_zero)
python run_pipeline.py --no-detector

# 전체 실행 (weights/ 의 학습된 모델 사용)
python run_pipeline.py                        # input/CL.jpg, input/CR.jpg
python run_pipeline.py --CL a.jpg --CR b.jpg  # 사진 직접 지정

# 분류 모델 평가 (val 크롭)
python tools/eval_classifier.py --backend yolo

# (선택) 재학습 — dataset.zip 필요, GPU 권장. 결과 best.pt 를 weights/ 로 복사하고 설정 변경
python tools/prepare_hybrid_data.py --data-root <dataset.zip 푼 폴더> --out hybrid_data
python train_hybrid.py det --data hybrid_data --device 0   # 시작 가중치 weights/yolov8m.pt (없으면 자동 다운로드)
python train_hybrid.py cls --data hybrid_data --device 0   # 시작 가중치 weights/yolo26m-cls.pt (없으면 자동 다운로드)
```

### 결과 파일 (`output/<시각>/`)

| 파일 | 내용 |
| --- | --- |
| `result_buds.jpg` | 최종 결과판. 칸마다 최종 등급과 배정된 봉오리 사진, Claude 요약 |
| `vis_CL.jpg`, `vis_CR.jpg` | 카메라 사진 위 식물 마스크, 구멍(빨강 = 식물, 파랑 = 빈 구멍), 봉오리 박스·마스크 윤곽, 칸 등급. `+VLM` = Claude가 추가한 봉오리, `(cls Gx)` = 검수가 바꾸기 전 ③ 등급 |
| `table.txt` | 3×4 표 + 칸별·카메라별 봉오리 개수 + Claude 판단 표 |
| `result.json` | 칸별 유무 점수, 카메라별 등급, 봉오리 목록(등급·확신·③ 등급 `cls_class`·`added_by`) |
| `vlm/` | Claude에 실제로 보낸 식물 사진 + 답 |
| `masks/` | 카메라·칸별 식물 마스크 |

### 주요 설정 (`configs/pipeline.yaml`)

| 항목 | 기본값 | 뜻 |
| --- | --- | --- |
| `layout.coverage` | 모든 칸 `CL,CR` | 각 칸을 어느 카메라로 판단할지 |
| `presence.method` | `fixed` | `layout.plants`의 O/X 표 사용. `dark_cover` = 사진에서 판단 |
| `grading.detector.type` | `hybrid` | `hybrid` / `vlm_plant` / `yolo`(기존) / `none` |
| `grading.detector.weights` | `weights/yolov8m_best.pt` | ① 위치 모델 |
| `grading.detector.conf` | 0.15 | ① 위치 신뢰도 하한 |
| `grading.detector.max_box_px` | 200 | 이보다 긴 박스는 버림 |
| `grading.detector.cls_weights` | `weights/yolo26m-cls_best.pt` | ③ 등급 모델 |
| `grading.detector.cls_conf` | 0.5 | ③ 분류 확신 하한 |
| `grading.detector.review` / `review_conf` | `true` / 0.6 | ④ Claude 검수 |
| `grading.detector.add_missed` | `true` | ⑤ Claude 누락 추가 |
| `grading.detector.vlm.api_key_file` | `ANTHROPIC_API_KEY.txt` | Claude API 키 파일 |
| `grading.assignment` | `mask_overlap` | 봉오리 → 식물 배정 규칙 |
| `grading.fusion` | `max` | 두 카메라 합치기 규칙 |

자세한 설명은 [`rose_hybrid/HYBRID.md`](rose_hybrid/HYBRID.md)에 있습니다.

---

## 4. 진행 정리 (기준일 2026-11-23)

원본: 「장미 잎 생장 탐지 모델 진행 - 이지후」(2026-10-15 ~ 2026-11-12) + 데이터 보강 + VLM 적용(11/14~11/20) + 학습 모델 적용(11/23).
전체 내용은 [진행 정리 PDF](rose_hybrid/장미_생장단계_진행정리_20261123.pdf)에 있습니다.

> 📌 **요약**: 판단 구조(식물 구분 → 봉오리 등급 → 식물별 최고 등급)는 완성했습니다.
> 11/23 현재 **YOLOv8m(탐지) + YOLO26m-cls(분류)** 학습 모델에 **Claude 검수·누락 추가**를 더해 실행합니다.
> 11/19 사진 결과는 R1C1 = grade_three, R1C3 = grade_one, R2C4 = grade_one, 나머지 식물 grade_zero입니다.
> 남은 과제는 다음과 같습니다.
> - **G2~G4 분류 정확도** (재현율 0.42~0.63)
> - **분류 모델 성능 비교 보강** (정확도만 비교함), 다른 모델과의 비교
> - **정답 있는 트레이 사진으로 정량 평가**, 봉오리가 많은 시기 사진으로 검증

### 2026-11-23 · 노트북 5번에서 사용할 모델 선정 + API 사용 방법 변경

- **적용 내용**
    - 사용할 모델: **YOLOv8m(탐지) + YOLO26m-cls(분류)** — 선정 이유는 [1절](#모델-선정-이유-2026-11-23)
    - API 사용 방법 변경: **직접 입력 → 파일로 읽기** (`ANTHROPIC_API_KEY.txt`)
- **결과**: `20261123T160536.zip` (`output/20261123T160536`)

  | 단계 | 개수 |
  | --- | --- |
  | ① 위치 후보 | 19 |
  | ③ 등급 통과 | 12 |
  | ④ Claude 검수 | 6개 같은 등급으로 남김 · 6개 버림 |
  | ⑤ Claude 누락 추가 | 1개 (R2C4, CR) |
  | 최종 봉오리 | 6개 (R1C1 2 · R1C3 2 · R2C4 2) |
  | Claude 요청 | 20번 |

  | 칸 | CL | CR | 최종 등급 |
  | --- | --- | --- | --- |
  | R1C1 | G3 | G3 | grade_three |
  | R1C3 | G1 | G1 | grade_one |
  | R2C4 | G1 | G1 (+VLM) | grade_one |
  | R2C2 · R3C1 · R3C3 | 없음 | 없음 | grade_zero |
  | 나머지 6칸 | | | empty |

  분류 모델 val 평가(크롭 2,169장): 정확도 95.0%, 재현율 not_bud 0.99 · G1 0.98 · **G2 0.42 · G3 0.58 · G4 0.63**
- **한계**
    1. 분류 모델의 성능 비교 미흡 (정확도만 비교)
    2. 다른 모델과의 비교 미흡

### 카메라 시야와 식물 위치

| 카메라 시야 | 1열 | 2열 | 3열 | 4열 |
| --- | --- | --- | --- | --- |
| 1행 | CR/CL | CR/CL | CR/CL | CR/CL |
| 2행 | CR/CL | CR/CL | CR/CL | CR/CL |
| 3행 | CR/CL | CR/CL | CR/CL | CR/CL |

10/15에는 4열을 CR만, R3C1을 CL만 봤습니다. 카메라 위치를 바꾼 뒤 모든 칸을 두 카메라가 봅니다.

| 식물 위치 | 1열 | 2열 | 3열 | 4열 |
| --- | --- | --- | --- | --- |
| 1행 | O | X | O | X |
| 2행 | X | O | X | O |
| 3행 | O | X | O | X |

### 등급 기준 (작업자 4, 11/14 반영)

| 등급 | 이름 | 기준 |
| --- | --- | --- |
| grade_one | 닫힌 봉오리 | 꽃잎이 꽉 말려 있고 끝이 뾰족함. 초록 꽃받침이 감쌈. 색이 거의 안 보이는 초록 봉오리도 1등급 |
| grade_two | 피기 시작 | 꽃받침이 젖혀지고 바깥 꽃잎 1~2장이 벌어지기 시작 |
| grade_three | 반쯤 핌 | 바깥 꽃잎 여러 장이 컵 모양으로 벌어짐. 가운데 소용돌이 심이 남음 |
| grade_four | 만개 | 꽃잎이 납작하게 다 펼쳐짐. 심이 거의 안 보임 |

### 진행 경과

| 날짜 | 단계 | 내용 | 결과 |
| --- | --- | --- | --- |
| 10/15 | ① 초기 버전 | CL/CR 사진, 자동 유무 판단 | 동작은 하지만 식물끼리 분리가 안 됨 |
| 10/15 | ② 식물 분리 대안 | 가지치기 / 격벽 설치 | 가지치기는 시기상조, 격벽은 광량 부족으로 폐기 |
| 10/15 | ③ 화분 위치 고정 | `presence.method: fixed` | 유무 판단은 정확. 배경을 **G4로 크게 오탐지** |
| 11/02 | ④ 학습 데이터 증가 | Roboflow 사진 추가 | G4 오탐지 지속 |
| 11/02 | ⑤ 실제 사진 추가 | 생육 사진 143장 라벨링 → 데이터셋 v3 | 아래 데이터 보강 참고 |
| 11/02 | ⑥ 최신 결과 | 새 CL/CR 카메라 사진 | G4 오탐지 사라짐. 좌측 식물 영역 미인식, 케이블 G1 오탐지 |
| 11/12 | ⑦ 새 데이터셋 + 혼합형 | new_MR_growth 데이터셋, 혼합형 학습·실행 | R1C1 = G3, R2C4 = G1, 배경 오탐지 없음 |
| 11/14 | ⑧ VLM 등급 분류 옵션 | ③ 분류를 yolo / vlm / cascade 중 선택, 등급 기준 반영 | 잎을 G1으로 잡는 문제 확인 → VLM 검토 |
| 11/14 | ⑨ 설정 정리 | 식물 유무 fixed, 구멍 좌표·입력 사진 최신화 | 12개 구멍 원이 화분 중심에 맞음 |
| 11/16 | ⑩ 화분 단위 VLM | `detector.type: vlm_plant` (Claude가 식물마다 판단) | 테스트 통과 |
| 11/19 | ⑪ VLM 실행 결과 | Colab에서 vlm_plant 실행 | R1C1 = G3, R1C3 = G1, R2C4 = G1, 잎 G1 오탐지 없음 |
| 11/20 | UI/UX 위치 변경 검토 | 식물 위치를 화면에서 바꾸는 기능 | 가능 확인 → 보류 |
| 11/23 | ⑫ 학습 없이 공개 모델 적용 | yolov8m.pt(COCO) + yolo26m-cls.pt(ImageNet) + Claude 검수·누락 추가 | 작은 봉오리를 거의 못 찾아 Claude가 대부분 추가 |
| 11/23 | ⑬ 'G1 0.50' 오탐지 수정 | 박스 크기 픽셀 제한(200px), 검수 확신 0.6, 검수·누락 추가 중복 정리 | 큰 박스 사라짐, Claude 요청 24 → 18번 |
| 11/23 | **⑭ 모델 선정 + API 사용 방법 변경** | **YOLOv8m + YOLO26m-cls 학습 모델 적용, API 키 파일로 읽기** | 최종 표 11/19와 같음, 봉오리 6개 중 5개를 모델이 찾음 |

### 보류 중인 항목

- [ ] `grade_two` + `grade_three` 클래스 통합 → 장미농원 방문 후 논의
- [ ] UI/UX에서 식물 위치 고정·변경 → 11/20 검토: 가능, 보류 (현재는 설정 파일의 O/X 표)
- [x] 카메라 위치 변경 후 구멍 위치 재보정 (11/14)
- [x] 범위표(`layout.coverage`)를 새 카메라 시야(모든 칸 CR/CL)에 맞게 수정

### 데이터

**새 학습 데이터셋 (⑦, new_MR_growth)** — 현재 모델의 학습 데이터
- 장미농장 사진 **143장**을 새로 라벨링(클래스 4개)하고, G2~G4 크롭을 원본 사진에 붙인 **copy-paste 합성 사진**을 더했습니다.
- 클래스 대응: `class_0` = grade_four, `class_1` = grade_one, `class_2` = grade_three, `class_3` = grade_two
- 사진 5장 묶음 단위로 train 118장 / val 25장, 960 창 → 640 타일(train 7,943장 / valid 545장). **test 분할은 없어서** val로 평가합니다.

| 분류 크롭 | G1 | G2 | G3 | G4 | not_bud |
| --- | --- | --- | --- | --- | --- |
| train | 8,643 | 1,182 | 1,009 | 963 | 11,855 |
| valid | 1,940 | 79 | 33 | 57 | 2,126 |

**데이터 보강 (⑤, 이전 데이터)** — 생육 사진 143장 봉오리 라벨링(박스 2,326개)과 Roboflow 데이터셋 통합. `dataset_v1.zip`으로 보관하며 이 데이터로는 학습하지 않았습니다.

### 현재 남은 문제

| 문제 | 현재 상황 | 관련 항목 |
| --- | --- | --- |
| G2~G4 분류 약함 | 재현율 G2 0.42 · G3 0.58 · G4 0.63. 한 단계 낮게 보는 경향. Claude 검수가 보완 | G2~G4 데이터 / G2·G3 통합 논의 |
| 분류 모델 성능 비교 미흡 | 9개 조합 비교에서 분류는 top-1 정확도만 비교 | 11/23 한계 1 |
| 다른 모델과의 비교 미흡 | YOLO11·YOLOv8·YOLO26 (n/s/m), 30 epoch만 비교 | 11/23 한계 2 |
| 트레이 결과 정확도 미검증 | 세 방식(화분 단위 VLM·공개 모델·학습 모델) 결과가 같지만 정답 비교 수치 없음 | 평가 데이터 |
| 등급 판단 검증 부족 | 결과 사진에 봉오리가 3개(꽃 1, 닫힌 봉오리 2)뿐 | 봉오리 많은 시기 사진 |
| Claude 운영 조건 | 실행마다 인터넷·API 키·요금 필요(약 20번 요청/실행) | 운영 방식 결정 |
| 식물 간 분리 | 잎이 겹치면 '가장 가까운 구멍' 기준 직선 분할 | 학습형 분할 |
| Colab 미확인 | 11/23 설정은 로컬(CPU)에서만 실행 | — |

### 다음 단계 (권장 순서)

1. **Colab 실행 확인**: API 키 파일을 넣고 노트북 5번 실행
2. **결과 확인**: R1C3·R2C4 G1, R1C1 G3을 원본 사진에서 사람이 확인 (`vlm/` 폴더 참고)
3. **운영 방식 결정**: 모델 + Claude(현재) / 모델만(요금 없음, G2~G4 약함) / 화분 단위 VLM
4. **분류 모델 비교 보강**: 클래스별 정밀도·재현율·혼동 행렬로 비교, 다른 모델 추가 비교
5. **G2~G4 보강**: 크롭 추가 라벨링 후 재학습, 또는 G2·G3 통합 논의
6. **봉오리가 보이는 트레이 사진 확보**: 실제 검증 + 평가 데이터(test)에 추가
7. **식물 분리 개선 (선택)**: 학습형 분할(SAM / YOLO-seg)

### 참고

- Roboflow 프로젝트: [rose_project_4](https://app.roboflow.com/task1-zjf7b/rose_project_4-hkazp-rasbj/browse)
- 추가 데이터 후보: [Roboflow Universe rose](https://universe.roboflow.com/sanjay-m-7nbqj/rose-tk4yv)
