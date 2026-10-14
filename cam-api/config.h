#pragma once
/*
 * 카메라 보드 설정 — 계정·Wi-Fi·SN 은 secrets.h
 * 설명서 9절 "아직 정해지지 않은 것" 의 기본값은 바뀔 수 있으므로 전부 상수로 뺐다.
 */
#include <Arduino.h>

#define FW_VERSION "cube-cam-0.7.1"           // 2026-10-12 0.7: 0.6 + 접속 유지 60초·HTTP 응답 대기 30초·즉시 촬영 요청 유지 60초(서버 api 0.9.2 와 맞춤)
                                             // 2026-10-14 0.7.1: 자동 초점(ESP32_OV5640_AF 라이브러리, 아래 "자동 초점" 절)

// ── 자동 초점 (2026-10-14, 사용자 결정) ──────────────────────────────
//   라이브러리 ESP32_OV5640_AF 0.1.1(Eric Nam, MIT) 세 파일을 이 폴더에 복사해 씀. 센서 안 보조 제어기에 초점 펌웨어(4,332B)를
//   내려받고 명령 레지스터로 초점을 맞춘다. 카메라를 켤 때마다 센서가 초기화되므로 켤 때마다 다시 내려받는다(1~2초).
//   ★ 조건: 초점 모터가 있는 OV5640 AF 모듈 + AI-Thinker 보드 카메라 커넥터 24번 핀(AF-VCC)에 3.3V 전선.
//     없는 보드에서 1 이면 촬영마다 2~4초만 더 걸린다 → 그런 보드는 0 으로 빌드.
#define ENABLE_AUTOFOCUS     1
#define AF_FOCUS_WAIT_MS     3000            // 단발 초점 완료(상태 0x10)를 기다리는 상한. 넘으면 초점 없이 그대로 촬영

// ── 화질 (실측 뒤 조정) ─────────────────────────────────────────
// 스냅샷 해상도 (2026-10-07 센서 OV3660 → OV5640). 판마다 이 파일의 FW_VERSION·SNAP_FRAME_SIZE 만 다르다
//   cube-cam-0.7      (cam-api)      FRAMESIZE_QSXGA 2560×1920 — 카메라 라이브러리(코어 3.3.12)가 OV5640 에 허용하는 최대. 기본 판
//   cube-cam-0.6-5mp  (cam-api-5mp)  FRAMESIZE_5MP   2592×1944 — 실기 실패(센서가 프레임을 주지 않음, 2026-10-07)
//   cube-cam-0.6-zoom-{i} (cam-api-zoom) 즉시 촬영 디지털 줌 + 시험 빌드(송신 출력·대기 시간 A/B). 시험용
#define SNAP_FRAME_SIZE      FRAMESIZE_QSXGA // 2560×1920
#define SNAP_JPEG_QUALITY    10              // 작을수록 고화질. 서버 상한 6MB
#define STREAM_FRAME_SIZE    FRAMESIZE_HD    // 1280×720
#define STREAM_JPEG_QUALITY  20              // 서버 상한 1MB
#define WARMUP_FRAMES        4               // 센서를 켠 직후 노출·색이 맞을 때까지 버리는 프레임 수
#define OV5640_VFLIP         0               // 사진이 위아래 뒤집혀 나오면 1

// ── 스냅샷 ──────────────────────────────────────────────────────
#define SNAP_RETRY_MS        3000            // 업로드 실패 후 재시도 간격
#define SNAP_RETRY_MAX       3
#define SNAP_REQ_WAIT_MS     60000           // 서버가 즉시 촬영을 기다리는 시간(api 0.9.2 부터 60초, 2026-10-12 사용자 결정). 넘으면 trigger=device

// ── 스트리밍 (C4) ───────────────────────────────────────────────
#define STREAM_DEFAULT_MAX_S     300         // 명령에 max_s 가 없을 때
#define STREAM_FRAME_INTERVAL_MS 0           // 프레임 사이 최소 간격. 0 = 가능한 빨리 (실측 뒤 결정)
#define STREAM_FAIL_MAX          5           // 연속 실패하면 스스로 정지

// ── HTTPS ───────────────────────────────────────────────────────
#define HTTP_TIMEOUT_MS      30000           // 본문을 다 보낸 뒤 서버 응답을 기다리는 시간. 15초에서는 무선이 나쁠 때 끊겨(nginx 400) 재시도 3회로 번짐(2026-10-12 C1 실측)
#define MQTT_REHELLO_MS      30000           // hello 뒤 MQTT 가 이 시간 동안 안 붙으면 hello 부터 다시
#define STATUS_INTERVAL_MS   10000           // 상태(칩 온도·IP·MAC·신호 세기 포함)를 이 간격으로 보낸다. 화면 값 갱신 주기

// ── 내장 플래시 LED (GPIO 4) — C2 기본값: 쓰지 않음 ────────────────
#define FLASH_ENABLE         1               // 1 이면 스냅샷 순간에만 켬
#define FLASH_DUTY           180             // 밝기 0~255
#define FLASH_WARMUP_MS      150

// ── 발열 대책 ───────────────────────────────────────────────────
#define WIFI_LOW_TX_POWER    1               // 1 = 송신 출력 11dBm. 와이파이가 자주 끊기면 0
#define STREAM_KEEP_WIFI_SLEEP 1             // 1 = 스트리밍 중에도 와이파이 모뎀 절전 유지(발열 감소). 처리량이 더 필요하면 0
#define SNAP_XCLK_HZ         20000000        // 스냅샷 때 카메라 클럭(기존값)
#define STREAM_XCLK_HZ       10000000        // 스트리밍 때 카메라 클럭. 센서 내부 클럭이 비례해 줄어 센서 발열 감소. 화질·색이 이상하면 20000000

// ── LAN 원격 업로드(ArduinoOTA, 코어 내장) — 2026-10-02 추가, 큐브 제어기와 같은 방식 ──
//   1 = 벤치: 같은 Wi-Fi 의 아두이노 IDE 네트워크 포트로 업로드(secrets.h OTA_PASSWORD 필요). 현장 보드는 0
//   ★ 보드 메뉴 Partition Scheme = "Minimal SPIFFS (1.9MB APP with OTA/128KB SPIFFS)".
//     파티션을 바꾼 뒤 첫 한 번은 USB 로 올려야 한다(기존 Huge APP 에는 원격 업로드 칸이 없다)
#define ENABLE_LAN_OTA       1
static const uint16_t OTA_PORT = 3232;                 // ArduinoOTA 기본 포트
static const char     OTA_HOSTNAME_PREFIX[] = "cube-";  // IDE 포트 목록에 "cube-<SN> at <IP>" 로 표시

// ── comm 모듈 (esp32_cam_node 와 같은 값. 접속 유지 간격만 다름) ──────────────────────
static const uint32_t NETWORK_RETRY_MS = 5000;
static const uint16_t MQTT_KEEPALIVE_S = 60;   // 30 → 60 (2026-10-12). 사진·프레임 업로드가 루프를 막는 동안 브로커가 끊는 기준이
                                               // 45초 → 90초가 돼 명령 유실이 줄어든다(zoom-1~3 실기에서 끊김 0 확인)
static const uint16_t MQTT_SOCKET_S    = 10;
static const uint16_t COMM_FRAME_BUF   = 1024;
static const long     TIME_OK_EPOCH    = 1700000000L;
