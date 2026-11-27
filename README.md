# langgraph 브랜치: AWS 시험 서버 자동 반영

이 브랜치에 푸시하면 AWS 시험 서버("https://smartcubeaws.duckdns.org")와 젯슨 추론 서버에 자동으로 반영됩니다. 보통 푸시하고 1~2분 안에 끝납니다.

## 폴더

| 폴더 | 내용 | 반영되는 곳 |
|---|---|---|
| `frontend/` | 스마트큐브 화면 (React + Vite) | AWS 화면 |
| `crop_monitor/` | 작물 분석 파이프라인 (LangGraph, 제미나이 검수) | AWS 시험 서버 |
| `models/` | 추론 모델 | 젯슨 추론 서버 |
| `.github/workflows/aws-deploy-signal.yml` | 푸시하면 AWS 에 신호를 보냄 | - |

## 반영 순서

1. 푸시하면 GitHub Actions 가 AWS 에 "저장소를 다시 확인하라"는 신호만 보냅니다. 비밀값은 쓰지 않습니다.
2. AWS 가 이 브랜치를 직접 받아, 바뀐 폴더만 시험합니다.
3. 시험을 통과한 폴더만 반영하고, 실패한 폴더는 이전 판을 그대로 둡니다.

`models/` 는 AWS 가 젯슨에 알리고, 젯슨이 이 브랜치에서 직접 받아 검사한 뒤 바꿉니다.

## 폴더별 시험

| 폴더 | 시험 |
|---|---|
| `frontend/` | `npm ci` → `npm run lint` → `npm test` → `vite build --mode aws` |
| `crop_monitor/` | `requirements.txt` 가 지금 서버와 같은지 확인 → `python -m unittest discover -s tests` |
| `models/` | 내려받은 파일이 깃허브 파일과 같은지 → 모델이 열리는지 → 작업 종류와 클래스 이름이 지금 모델과 같은지 → 샘플 사진 추론 |

푸시 전에 같은 시험을 로컬에서 돌려 보면 실패를 줄일 수 있습니다.

## models 규칙

젯슨은 아래 다섯 이름만 받습니다. 다른 이름의 파일은 건너뜁니다.

| 파일 | 쓰는 곳 | 클래스 |
|---|---|---|
| `models/growth/yolov8m_best.pt` | 봉오리 위치 | bud |
| `models/growth/FastSAM-s.pt` | 봉오리 마스크 | (FastSAM) |
| `models/growth/yolo26m-cls_best.pt` | 생장 단계 | grade_four, grade_one, grade_three, grade_two, not_bud |
| `models/disease/leaf_seg_v8r2.pt` | 잎 찾기 | item |
| `models/disease/leaf_cls_yolov8m.pt` | 잎 상태 | black_spot, downy_mildew, healthy, insects_infected |

- 모델을 바꾸려면 같은 이름으로 덮어써서 올립니다.
- 클래스가 바뀌는 모델은 코드도 함께 바뀌어야 해서 자동으로 반영되지 않습니다.
- 한 커밋의 모델 중 하나라도 검사에 실패하면 그 커밋의 모델은 하나도 바뀌지 않습니다. 고친 뒤 다시 커밋하세요.
- 파일 하나가 100MB 를 넘으면 깃허브에 일반 방식으로 올릴 수 없습니다. Git LFS 로 올린 파일은 젯슨이 받지 않습니다.
- 젯슨 모델은 네이버 서버와 AWS 서버가 같이 씁니다. 바꾸면 두 서버의 결과가 함께 바뀝니다.
- 바뀌기 전 모델은 젯슨에 백업으로 남습니다.

## 올리면 안 되는 것

- `.env`, `.env.aws` 같은 환경 파일, API 키
- `node_modules/`, `dist/`, `dist-aws/` (`frontend/.gitignore` 에 `dist-aws` 는 빠져 있으니 주의)
- `crop_monitor/out/` (실행 결과), `crop_monitor/secrets/` 안의 키 파일

공개 저장소라 한 번 올라간 비밀값은 지워도 기록에 남습니다.

## 알아 둘 것

- `crop_monitor/requirements.txt` 를 바꾸면 서버가 패키지를 자동으로 설치하지 않고 반영을 멈춥니다. 서버 담당자에게 알려 주세요.
- `crop_monitor/config/crops.yaml` 의 젯슨 주소는 AWS 가 반영할 때 자기 쪽 값으로 바꿔 씁니다.
- AWS 화면은 네이버 클라우드 api 를 조회만 합니다. 제어 버튼은 동작하지 않습니다.
- 반영 여부는 GitHub 의 Actions 탭에서 신호가 성공했는지로 먼저 확인하고, 서버 쪽 결과는 aws서버에서 확인하세요.
