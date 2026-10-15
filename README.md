# leafdx: 미니장미 잎 탐색 + 잎 상태 판정 (이식용 패키지, v8 / 2026-10-15)

이 폴더만 복사하면 동작합니다. 서버의 다른 폴더(common, v7_leafid 등)에 의존하지 않습니다.

## 구성
```
deploy/
├── models/
│   ├── leaf_seg_v8r2.pt   # 1단계 잎 탐색: YOLO-seg, 잎 1클래스 (원본: v8_reallabel/runs/r2_fromv4/weights/best.pt)
│   ├── leaf_cls_v8.pt     # 2단계 상태 분류: EfficientNetV2-S state_dict, 4클래스 (원본: v8_reallabel/cls/runs/effnetv2s/best.pt)
│   └── states.json        # 2단계 판정 기준 (val로 선택)
├── leafdx/                # 추론 코드: stage1.py, stage2.py, pipeline.py, draw.py, _compat.py
├── config.json            # 추론 설정
├── run.py                 # 명령줄 실행
├── smoke_test.py          # 서버 결과 재현 확인 (samples/fix_eafda190.jpg)
├── samples/fix_eafda190.jpg
└── requirements.txt
```

## 설치와 실행
```
pip install -r requirements.txt
python smoke_test.py                                  # SMOKE_TEST PASS 확인
python run.py <사진 또는 폴더> --out <출력폴더>        # [--device cpu|cuda:0] [--L 잎길이px]
```
출력: 사진마다 `<이름>.json`(잎 전체 결과), `<이름>_vis.jpg`(상태별 윤곽), 그리고 `summary.csv`(사진별 상태 개수, 처리 시간)

### 파이썬에서 사용
```python
from leafdx import LeafPipeline, load_rgb, draw_result
pl = LeafPipeline("deploy/models")       # config.json은 models/ 또는 그 상위 폴더에서 자동으로 읽음
rgb = load_rgb("photo.jpg")              # HxWx3 uint8 RGB (EXIF 회전 반영)
res = pl.run(rgb)                        # dict: L, size, counts, leaves, timing
draw_result(rgb, res).save("vis.jpg")
json_ready = LeafPipeline.to_json(res)   # 마스크 배열을 뺀 직렬화용 사본
```

## 처리 흐름
1. **잎 길이 추정:** 사진 전체를 1280으로 줄여 1회 예측 → 박스 긴 변의 중앙값 L (실패하면 90px). 카메라 배치가 고정이면 `--L`로 지정해 이 단계를 건너뛸 수 있음
2. **1단계 잎 탐색:** 한 변이 L × 8인 정사각 타일(25% 겹침)을 640으로 추론 → 타일 경계에 잘린 마스크 제거 → 타일 간 중복 병합(마스크 IoU > 0.5) → 넓이 400px 미만 제거 → 읽는 순서로 ID(L0001…)
3. **2단계 입력:** 사진 전체 화이트밸런스(gray world) → 잎 박스 중심으로 긴 변 × 1.25 정사각형 크롭 → 224
4. **판정 상태**
| 상태 | 조건 | 의미 |
|---|---|---|
| HOLD_SHAPE | 볼록도 < 0.8 또는 사진 가장자리에 닿음 | 잎이 합쳐졌거나 잘림 → 판정 안 함 |
| HOLD | 선명도 < s_min, 어두운 비율 > d_max, 잎 길이 < L_min | 품질 부족 → 판정 안 함 (현재 기준은 사실상 꺼짐) |
| ANOMALY | max(흑점, 구멍, 황변 확률) ≥ t_anom (0.5) | 이상 (anomaly_class에 클래스) |
| FRESH | 건강 확률 ≥ t_fresh (0.7) | 정상 |
| UNKNOWN | 그 외 | 이상 의심 → 경보가 아니라 관찰 대상 (며칠 계속되면 확인 요청 권장) |

## 출력 JSON (잎 1개)
`id, box[x0,y0,x1,y1], polygon[[x,y]...], area, conf(1단계), solidity, touches_border, sharpness, dark, L(잎 박스 긴 변), state, anomaly_class, probs{Black Spot, Fresh, Hole, Yellow}`

## 설정 (config.json)
`conf` 0.35 (1단계 신뢰도, val로 선택), `tile_mult` 8, `imgsz` 640, `est_imgsz` 1280, `min_area` 400, `dedup_iou` 0.5, `box_scale` 1.25, `min_solidity` 0.8, `device` null(자동)

## 성능 (서버 V100, test 4장 = 학습에 안 쓴 장면, 잎 880개)
| 항목 | 값 |
|---|---|
| 1단계 재현율 / 정밀도 (mask IoU ≥ 0.5) | 0.80 / 0.77 |
| 정답 잎 중 2단계까지 깨끗하게 간 비율 | 73% |
| 건강 잎(사용자 확인) 이상 오판 | 0 / 865 |
| 같은 잎에 그린 합성 이상 검출 | 99.8% |
| 처리 시간 (2560×1920 사진) | 1단계 약 2초, 2단계 약 2초 (잎 300개 기준) |

**한계:** 실제 이상 잎 검출률은 미측정(실제 이상 사진 없음). 흐린 저해상도 사진(1280×720)에서는 1단계 F1 약 0.65. 학습·평가 사진은 같은 챔버·같은 시기라 새 환경에서는 재검증 필요.

## Jetson 이식 메모
- JetPack용 PyTorch/torchvision을 먼저 설치한 뒤 `ultralytics==8.4.175` 설치 (pip가 torch를 덮어쓰지 않게 주의)
- 1단계 가속: Jetson에서 `yolo export model=models/leaf_seg_v8r2.pt format=engine half=True imgsz=640` → `config.json`의 `seg_weights`를 생성된 `.engine`으로 변경
- 2단계 가속: torchvision EfficientNetV2-S를 ONNX(입력 1×3×224×224, 고정 배치 권장)로 내보낸 뒤 TensorRT 변환 (현재 코드는 PyTorch로 실행)
- `leafdx/_compat.py`는 일부 환경의 opencv 4.7/4.11 혼합 설치 문제(cv2.typing) 우회용이며 다른 환경에서는 영향 없음
- 변환 후에는 `smoke_test.py`로 결과가 같은지(또는 차이가 작은지) 확인

## 출처
학습·평가 과정과 데이터: `v8_reallabel/README.md` (1단계 r2_fromv4, 2단계 cls/runs/effnetv2s)
