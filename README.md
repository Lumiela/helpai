# rose_leaf_colab: 장미 잎 탐색 + 새 분류 모델 + VLM 검증 (Colab 실행용, 2026-11-19)

1단계 잎 탐색은 기존 세그멘테이션 모델을 그대로 쓰고, 2단계 상태 분류만 새 YOLOv8m-cls 모델로 바꾼 패키지입니다.
3단계로 분류 결과를 VLM(Claude 또는 Qwen2.5-VL)에게 다시 확인받는 기능이 있습니다 (선택).
노트북은 Claude API 키를 넣는 방법에 따라 두 개입니다 (아래 **API 키 넣는 방법**).

## 변경 이력
| 날짜 | 변경 |
|---|---|
| 2026-11-19 | `rose_leaf_colab_keyfile.ipynb` 추가: API 키를 `ANTHROPIC_API_KEY` 파일에서 읽음 (Colab 실제 키 실행 결과 포함)<br>두 노트북 6번 단계에 API 키 넣는 방법 안내 칸 추가 (보안 비밀 / 키 파일)<br>두 노트북이 패키지 zip과 키 파일을 Drive의 `내 드라이브/rose_leaf_colab(VLM적용+API방식변경)` 폴더에서 찾도록 변경<br>저장소에 `.gitignore` 추가 (`ANTHROPIC_API_KEY*` 파일은 올리지 않음)<br>README·정리 문서에 API 키 넣는 방법, 키 파일 노트북 Colab 실행 결과(CL (1).jpg, 약 $0.11) 반영 |
| 2026-11-18 | VLM 검증(`verify.py`, `leafdx/vlm.py`), 재학습 분류 모델 2종(`_du`) 선택, 프로젝트 정리 문서(md·pdf)와 CL (1).jpg 결과 이미지 추가 |
| 2026-11-05 | 2단계 분류를 YOLOv8m-cls로 바꾼 Colab 실행 패키지 첫 공개 |

## 구성
```
rose_leaf_colab/
├── rose_leaf_colab.ipynb          # Colab 노트북 (API 키: Colab 보안 비밀)
├── rose_leaf_colab_keyfile.ipynb  # 같은 노트북 (API 키: ANTHROPIC_API_KEY 파일), Colab 실행 결과가 저장돼 있음
├── rose_leaf_colab_package.zip    # 아래 package/ 를 압축한 파일 (Drive에 올리는 파일)
├── README.md                      # 이 파일
├── 장미 잎 이상 탐지 프로젝트 정리.md / .pdf   # 프로젝트 전체 정리 문서
├── docs_images/                   # 정리 문서에 넣은 결과 이미지 4장 (CL (1).jpg)
├── ANTHROPIC_API_KEY.txt          # 개인 API 키 파일. 다른 사람과 공유하거나 압축해 보낼 때는 반드시 빼기
└── package/
    ├── models/
    │   ├── leaf_seg_v8r2.pt       # 1단계: 기존 세그멘테이션 모델
    │   ├── leaf_cls_yolov8m.pt    # 2단계 분류 모델 (기본값, zip의 rose_leaf_cls/weights/best.pt)
    │   ├── leaf_cls_yolov8m_du.pt # 2단계 분류 모델 (YOLOv8m-cls, du)
    │   ├── leaf_cls_yolo26m_du.pt # 2단계 분류 모델 (YOLO26m-cls, du)
    │   └── states.json            # 판정 기준
    ├── leafdx/                    # 추론 코드 (stage2.py, pipeline.py, draw.py 수정, vlm.py 추가)
    ├── run.py                     # 1·2단계 실행
    ├── verify.py                  # 3단계 VLM 검증 (run.py 결과 폴더를 읽음)
    ├── config.json, requirements.txt
    └── samples/fix_eafda190.jpg
```

## Colab 실행 방법
1. `rose_leaf_colab_package.zip`을 Google Drive의 `내 드라이브/rose_leaf_colab(VLM적용+API방식변경)` 폴더에 올립니다.
   다른 폴더에 두려면 노트북 2번 셀의 `Path("/content/drive/MyDrive/rose_leaf_colab(VLM적용+API방식변경)")` 경로를 바꿉니다 (`내 드라이브` 바로 아래면 `Path("/content/drive/MyDrive")`).
2. Claude로 VLM 검증을 하려면 API 키를 미리 넣어 둡니다 (아래 **API 키 넣는 방법**). Qwen만 쓰거나 VLM 검증을 건너뛰면 필요 없습니다.
3. Colab에서 `rose_leaf_colab.ipynb` 또는 `rose_leaf_colab_keyfile.ipynb`를 엽니다 (파일 > 노트북 업로드).
4. 런타임 > 런타임 유형 변경 > T4 GPU를 선택합니다.
5. 위에서부터 셀을 차례대로 실행합니다. 3번 단계에서 사진을 올리고, 6번 단계(VLM 검증)는 건너뛰어도 됩니다. 마지막 셀에서 결과를 내려받습니다.

Drive를 쓰지 않으면 2번 셀의 `USE_DRIVE = False`로 바꾸고 zip을 직접 업로드합니다.

## API 키 넣는 방법
노트북이 키를 환경변수 `ANTHROPIC_API_KEY`에 넣으면 `verify.py`가 이어받아 Claude를 씁니다. 두 노트북 모두 6번 단계에 단계별 안내 칸이 있습니다.

| 노트북 | 키를 넣는 곳 | 안내 칸 |
|---|---|---|
| `rose_leaf_colab.ipynb` | Colab 보안 비밀 (왼쪽 열쇠 아이콘, 이름 `ANTHROPIC_API_KEY`, 노트북 액세스 켜기) | **API 키 넣는 방법** |
| `rose_leaf_colab_keyfile.ipynb` | 이름이 `ANTHROPIC_API_KEY`(또는 `.txt`)인 텍스트 파일, 내용은 키 한 줄 | **API 키 파일 넣는 방법** |

공통 준비: Anthropic Console(console.anthropic.com) > API Keys > Create Key로 키(`sk-ant-...`)를 만들고 복사합니다. 키는 만들 때 한 번만 보이고, 요금은 Billing의 크레딧에서 나갑니다.

**키 파일 방식** (`rose_leaf_colab_keyfile.ipynb`)
- 메모장에 키 한 줄만 붙여 넣고, 파일 형식 **모든 파일**·인코딩 **UTF-8**로 `ANTHROPIC_API_KEY`라는 이름으로 저장합니다. `.txt`가 붙어도 됩니다.
- `ANTHROPIC_API_KEY=sk-ant-...` 형식, 앞뒤 공백·줄바꿈, BOM, 따옴표, `#` 주석 줄도 읽습니다.
- 키 읽기 셀이 찾는 순서: ① Drive의 `내 드라이브/rose_leaf_colab(VLM적용+API방식변경)` 폴더 ② `/content` ③ 둘 다 없으면 업로드 창.
  ①의 폴더는 키 읽기 셀의 `KEY_DIRS` 첫 줄이며, 다른 폴더에 두려면 이 경로를 바꿉니다 (`내 드라이브` 바로 아래면 `Path("/content/drive/MyDrive")`).
- 화면에는 키의 앞 7자와 끝 4자만 표시하고, 빈 파일이나 `sk-ant-`로 시작하지 않는 내용은 따로 알려 줍니다.

**보안**
- 키 파일은 결과 zip에 들어가지 않습니다. 보안 비밀 값은 노트북을 공유해도 함께 공유되지 않습니다.
- 키를 코드 셀에 직접 붙여 넣지 마세요. 키 파일은 공유 Drive 폴더나 남에게 보내는 압축 파일에 넣지 마세요.
  이 폴더의 `ANTHROPIC_API_KEY.txt`도 폴더를 통째로 공유할 때는 빼야 합니다.
- 키가 유출됐다고 생각되면 Anthropic Console에서 그 키를 바로 삭제하고 새로 만듭니다.
- 명령줄에서 `verify.py`를 직접 실행할 때는 키를 넣는 옵션이 없고 환경변수로만 받습니다.

## 분류 모델 선택
노트북 4번 셀의 `CLS_MODEL`(명령줄에서는 `run.py --cls <파일 이름>`)로 고릅니다. 결과 JSON과 `summary.csv`, `vlm_review.csv`에 사용한 모델 이름(`cls_model`)이 기록되고, 결과 이미지 제목에도 표시됩니다.

| 파일 | 기반 모델 | val 정확도 | 샘플 사진에서 이상·의심 판정 |
|---|---|---|---|
| `leaf_cls_yolov8m.pt` (기본값) | YOLOv8m-cls | 0.957 | 2개 (이상 1, 의심 1) |
| `leaf_cls_yolov8m_du.pt` | YOLOv8m-cls | 0.950 | 7개 (black_spot 5, 의심 2) |
| `leaf_cls_yolo26m_du.pt` | YOLO26m-cls | 0.948 | 6개 (black_spot 1, insects_infected 2, downy_mildew 1, 의심 2) |

- val 정확도는 각 모델 파일에 저장된 학습 기록 값입니다. 세 모델 모두 같은 데이터 폴더(`rose_leaf_canvas_cls`)와 같은 학습 설정이며, 기반 모델만 다릅니다.
- 샘플 사진은 건강한 잎만 있는 사진이라 이상·의심 판정은 모두 오경보일 가능성이 큽니다. 늘어난 판정은 대부분 두 잎이 하나로 합쳐진 윤곽, 흐린 잎, 붉은 잎 끝이었습니다.
  L0115(주황·갈색 부분이 보이는 잎)는 두 새 모델 모두 이상으로 판정했으니 직접 확인해 볼 만합니다.
- 두 새 모델은 ultralytics 8.4.215로 학습됐지만 패키지는 8.4.198을 씁니다. 세 분류 모델 모두 두 버전에서 출력이 완전히 같았고(최대 차이 0.0),
  잎 탐색은 8.4.198에서만 서버 결과(잎 308개)와 같았습니다 (8.4.215는 314개).

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

## VLM 검증 (verify.py)
분류 모델의 결과를 VLM에게 한 번 더 확인받는 기능입니다. 사진마다 다음 잎을 검증합니다.
- 이상(ANOMALY)·의심(UNKNOWN) 잎 전부 (이상 확률이 높은 순)
- 건강(FRESH) 잎 중 무작위 5장 (분류 모델이 놓친 이상이 있는지 확인용, `--fresh`)
- 사진당 최대 40장 (`--max`, 비용 상한)

잎마다 두 장의 이미지를 보냅니다. 분류 모델의 답은 알려 주지 않습니다 (선입견 없이 판정하게 하기 위해).
1. 확대 사진: 잎 박스의 1.4배 영역, 잎 밖은 어둡게, 잎 윤곽은 청록색 선
2. 주변 사진: 잎 박스의 3배 영역, 잎 윤곽은 청록색 선

VLM은 `healthy, black_spot, downy_mildew, insects_infected, not_a_leaf, unclear` 중 하나와 확신도, 보인 증상과 이유(한국어)를 답합니다.
프롬프트에는 챔버 환경(분홍색 LED, 거울 벽)과 병이 아닌 특징(붉은 잎 가장자리, 연한 새잎, 반사광)을 적어 두었습니다.

| 비교 결과 (verdict) | 의미 |
|---|---|
| AGREE | 분류 모델과 VLM 판정 일치 |
| FALSE_ALARM | 분류 모델은 이상, VLM은 건강 → 오경보 가능성 |
| MISSED | 분류 모델은 건강, VLM은 이상 → 놓친 이상 가능성 |
| OTHER_CLASS | 둘 다 이상이지만 종류가 다름 |
| VLM_HEALTHY / VLM_ANOMALY | 분류 모델이 의심(UNKNOWN)으로 남긴 잎에 대한 VLM 판정 |
| NOT_A_LEAF | 실제 잎이 아님 (거울 반사, 줄기, 여러 잎이 합쳐짐 등) |
| UNCLEAR | VLM도 판단 불가, 또는 답을 읽지 못함 (error 열 참고) |

출력 (run.py 결과 폴더에 추가): `vlm_review.csv`(전체 사진), `<사진>_vlm.json`, `<사진>_vlm.jpg`(검증한 잎 확대 사진과 두 판정).

### VLM 선택
| | Claude (`--backend claude`) | Qwen2.5-VL-3B (`--backend qwen`) |
|---|---|---|
| 필요한 것 | Anthropic API 키 (Colab 보안 비밀 또는 키 파일, 위 **API 키 넣는 방법**) | T4 GPU (처음에 약 7GB 내려받음) |
| 비용 | 사용량만큼 과금 (`claude-opus-5-5`, 실행 끝에 토큰 수와 대략적 금액 표시) | 무료 |
| 판단력 | 높음 | 낮음, JSON 형식을 지키지 못하면 UNCLEAR로 기록 |
| 설정 | effort `low`, 구조화 출력(JSON 스키마), 거절 시 서버 측 대체 모델(fallbacks) | 탐욕적 생성, 최대 300토큰 |

`--backend auto`(기본값)는 키가 있으면 Claude, 없으면 Qwen을 씁니다. `--dry-run`은 검증할 잎 수만 셉니다.

### 확인한 것과 못 한 것
- 노트북 전체 셀을 로컬 CPU에서 순서대로 실행해 동작을 확인했습니다 (Colab 업로드·Drive는 흉내 냄).
- Claude: 처음에는 가짜 API 서버로 요청 형식(이미지 2장, 스키마, effort, fallbacks)과 답 처리를 확인했습니다.
  이후 `rose_leaf_colab_keyfile.ipynb`를 Colab(T4)에서 실제 키 파일로 끝까지 실행했습니다 (실행 출력이 노트북에 저장돼 있음).
  키는 Drive 폴더의 `ANTHROPIC_API_KEY.txt`에서 읽혔고, 아래 결과가 나왔습니다.


| 항목 | 결과 (CL (1).jpg, 기본 분류 모델 `leaf_cls_yolov8m.pt`) |
|---|---|
| 잎 판정 | 잎 523개: FRESH 489, HOLD_SHAPE 29, ANOMALY 5 (black_spot 1, downy_mildew 2, insects_infected 2) |
| 시간 (T4) | 1단계 9.57초, 2단계 3.38초 |
| VLM 검증 | 10장 (이상 5 + 건강 무작위 5), backend claude (`claude-opus-5-5`) |
| VLM 비교 | AGREE 4, OTHER_CLASS 2, NOT_A_LEAF 2, FALSE_ALARM 1, UNCLEAR 1 |
| 비용 | 입력 20,684 / 출력 1,266 토큰, 약 $0.108 |

  - downy_mildew로 판정한 L0410·L0446은 VLM이 black_spot으로 봤습니다 (OTHER_CLASS)
  - insects_infected인 L0091은 VLM이 정상으로 봤고 (FALSE_ALARM, 다른 잎에 가려진 윤곽), L0467은 줄기와 여러 잎이 묶인 영역이라고 봤습니다 (NOT_A_LEAF)
  - black_spot인 L0246은 반점이 하나뿐이라 VLM도 판단하지 못했습니다 (UNCLEAR)
  - 건강 샘플 5장 중 4장은 VLM도 정상으로 봤고, L0482는 여러 잎이 겹친 윤곽이라고 봤습니다 (NOT_A_LEAF)
- Qwen: 같은 구조의 작은 테스트 모델로 불러오기·생성·답 처리를 확인했습니다. **실제 3B 모델의 판정 품질은 확인하지 못했습니다** (이 PC 메모리 부족).
- VLM 판정도 틀릴 수 있습니다. 판정이 갈린 잎(FALSE_ALARM, MISSED)은 사람이 `_vlm.jpg`로 직접 확인하는 용도로 쓰세요.
