# rose_leaf_colab: 장미 잎 탐색 + 새 분류 모델 (Colab 실행용, 2026-11-04)

1단계 잎 탐색은 기존 세그멘테이션 모델을 그대로 쓰고, 2단계 상태 분류만 새 YOLOv8m-cls 모델로 바꾼 패키지입니다.
기존 폴더 `helpai-rose_leaf_search/`는 건드리지 않았습니다.

## 구성
```
rose_leaf_colab/
├── rose_leaf_colab.ipynb          # Colab에서 여는 노트북
├── rose_leaf_colab_package.zip    # 아래 package/ 를 압축한 파일 (Drive에 올리는 파일)
└── package/
    ├── models/
    │   ├── leaf_seg_v8r2.pt       # 1단계: 기존 세그멘테이션 모델
    │   ├── leaf_cls_yolov8m.pt    # 2단계: 새 분류 모델 (zip의 rose_leaf_cls/weights/best.pt)
    │   └── states.json            # 판정 기준
    ├── leafdx/                    # 추론 코드 (stage2.py, pipeline.py, draw.py 수정)
    ├── config.json, run.py, requirements.txt
    └── samples/fix_eafda190.jpg
```

## Colab 실행 방법
1. `rose_leaf_colab_package.zip`을 Google Drive의 `내 드라이브` 바로 아래에 올립니다.
2. Colab에서 `rose_leaf_colab.ipynb`를 엽니다 (파일 > 노트북 업로드).
3. 런타임 > 런타임 유형 변경 > T4 GPU를 선택합니다.
4. 위에서부터 셀을 차례대로 실행합니다. 3번 단계에서 사진을 올리고, 마지막 셀에서 결과를 내려받습니다.

Drive를 쓰지 않으면 2번 셀의 `USE_DRIVE = False`로 바꾸고 zip을 직접 업로드합니다.

## 기존 패키지와 달라진 점
| 항목 | 기존 (EfficientNetV2-S) | 새 모델 (YOLOv8m-cls) |
|---|---|---|
| 클래스 | Black Spot, Fresh, Hole, Yellow | black_spot, downy_mildew, healthy, insects_infected |
| 입력 | 잎 주변을 포함한 정사각 크롭 | 마스크로 잎만 오려 흰 캔버스 중앙에 배치 (긴 변 = 캔버스의 60%) |
| 판정 기준 | val로 선택 | 기존 값 재사용 (t_anom 0.5, t_fresh 0.7), 새 모델로 다시 고르지 않음 |

입력 형식은 학습 결과의 `val_batch` 이미지에서 측정했습니다 (흰 배경, 잎 긴 변 약 135/224px, 잎 박스 중심 정렬).
화이트밸런스는 켜 두었습니다 (`config.json`의 `white_balance`). 켜고 끈 결과 차이는 거의 없었고, 학습 데이터에 적용됐는지는 알 수 없습니다.

## 판정 상태
| 상태 | 조건 | 의미 |
|---|---|---|
| HOLD_SHAPE | 볼록도 < 0.8 또는 사진 가장자리에 닿음 | 잎이 합쳐졌거나 잘림, 판정 안 함 |
| HOLD | 품질 기준 미달 | 판정 안 함 (현재 기준은 사실상 꺼짐) |
| ANOMALY | 이상 클래스 확률 최댓값 ≥ 0.5 | 이상 (anomaly_class에 클래스) |
| FRESH | healthy 확률 ≥ 0.7 | 정상 |
| UNKNOWN | 그 외 | 이상 의심, 관찰 대상 |

## 모델 성능과 한계
- 학습 기록의 val top-1 정확도는 약 0.95, test 혼동행렬 기준 정확도는 약 0.93입니다 (2,406 / 2,587).
- test 혼동행렬에서 약한 쪽은 **downy_mildew**입니다. 실제 downy_mildew 271개 중 106개가 black_spot으로 분류됐습니다.
- 학습 이미지는 흰 캔버스 위의 단일 잎입니다. 챔버 사진의 실제 병든 잎으로는 검출률을 확인하지 못했습니다.
- 잎 탐색 단계의 한계는 그대로입니다. 챔버의 거울 벽에 비친 잎도 잎으로 세어집니다.
