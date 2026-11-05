# ESP32-CAM 스테레오 비전 거리 측정

## 폴더 구성
```
stereo/
├─ esp32_stereo_cam/esp32_stereo_cam.ino   # ESP32-CAM 펌웨어 (두 보드 공용)
├─ stereo.py                               # PC용 캘리브레이션 + 거리 측정
├─ stereo_colab.ipynb                      # Colab용: 캘리브레이션 + 사진 2장 → 거리 맵
├─ 코드_설명.md                            # 코드 상세 설명서
├─ requirements.txt
└─ README.md
```

## 준비물
- ESP32-CAM (AI-Thinker, OV2640) 2대 + USB-TTL 업로더
- 두 카메라를 **평행하게 고정**할 판 (간격 6~10cm 권장, 측정 중 절대 움직이면 안 됨)
- 체커보드 (예: 10x7칸, 한 칸 25mm를 A4에 인쇄해 평평한 판에 부착)
- PC와 ESP32가 같은 2.4GHz Wi-Fi에 연결되어 있어야 함

## 1. 펌웨어 업로드
1. Arduino IDE → 보드 매니저에서 `esp32` 설치 → 보드: **AI Thinker ESP32-CAM**
2. `esp32_stereo_cam.ino`에서 `ssid`, `pass`, IP 대역(공유기에 맞게) 수정
3. 왼쪽 보드: `#define CAM_SIDE 0` 으로 업로드 → 192.168.0.101
4. 오른쪽 보드: `#define CAM_SIDE 1` 으로 업로드 → 192.168.0.102
5. 브라우저에서 `http://192.168.0.101/capture` 접속해 사진이 나오면 성공

> 왼쪽/오른쪽은 **카메라 뒤에서 피사체를 바라볼 때** 기준입니다. 바뀌면 거리가 측정되지 않습니다.

## 2. PC 설정
```
pip install -r requirements.txt
```
`stereo.py` 상단의 `BOARD`, `SQUARE_MM`을 실제 체커보드에 맞게 수정하세요.

## 3. 실행 순서
| 단계 | 명령 | 설명 |
|---|---|---|
| 0 | `python stereo.py test` | 두 카메라 영상/FPS 확인 |
| 1 | `python stereo.py collect` | 체커보드를 여러 위치·각도로 SPACE 눌러 20~30쌍 저장 |
| 2 | `python stereo.py calibrate` | `stereo_calib.npz` 생성, RMS 오차 확인 (1.0px 미만 목표) |
| 3 | `python stereo.py measure` | 거리 측정 |

### measure 조작
- **왼쪽 클릭**: 해당 지점 거리 측정 (cm 표시)
- **오른쪽 클릭**: 화면 중앙으로 복귀
- **s**: 스크린샷 저장 / **q**: 종료

## 정확도 높이는 팁
- 측정 가능 거리: 대략 30cm ~ 3m (baseline 6cm, VGA 기준). 멀수록 오차가 급격히 커짐
- **가까운 물체가 N/A** → `NUM_DISP`를 160, 192로 증가
- **무늬 없는 벽, 반사면, 어두운 곳**은 측정 불가 → 조명을 밝게, 질감 있는 물체로 테스트
- 좌우 밝기가 다르면 `.ino`의 `AEC_VALUE`, `AGC_GAIN`을 두 보드 동일하게 조정
- 캘리브레이션 후 카메라가 조금이라도 틀어지면 **다시 캘리브레이션** 필요
- 해상도를 바꾸면(펌웨어 `FRAMESIZE_*`) 캘리브레이션을 다시 해야 함
- 움직이는 물체는 두 카메라 촬영 시점 차이로 오차 발생 → 정지된 물체로 측정 권장
- 실측 거리와 비교해 일정 비율로 틀리면 `SQUARE_MM` 값이 정확한지 확인
