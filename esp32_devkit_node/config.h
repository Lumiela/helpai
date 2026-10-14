/*
 * ESP32 DevKitC(WROOM-32D) 통합판 — 핀 배정 · 상수 · 기능 플래그 (2026-09-29)
 * ------------------------------------------------------------------
 * esp32_cam_node(ESP32-CAM 단독 통합판 1.0.2)를 새 ESP32 DevKitC WROOM-32D 로 옮긴 판입니다.
 * 09-23 에 죽은 DevKitC 와 같은 모델이 09-29 에 도착해 다시 프로젝트 보드로 씁니다.
 * 제어 규칙·안전 상수·서버 통신·원격 보정은 ESP32-CAM 판 그대로이고, 핀과 보드 종류가 바뀌었으며
 * 핀이 넉넉해져 09-22 병합본(esp32_cube_node)의 배기팬 모듈을 다시 넣었습니다.
 *
 *   젯슨 서버 --Wi-Fi/TLS MQTT--> ESP32 DevKitC --GPIO18 PWM--> 모터(펌프) MOSFET
 *                                               --GPIO16 PWM--> LED MOSFET
 *                                               --GPIO25/26/34--> 배기팬(릴레이 · PWM · TACH)
 *                                               --I2C 21/22--> ADS1115 --> 토양 센서 AOUT
 *
 * ── 핀 배정 (사용자 결정 2026-09-29, TACH 만 2026-10-06 변경) ─────────
 *   GPIO18 → 모터(펌프) MOSFET 게이트 PWM. 09-22 팀 병합본과 같은 핀. 부팅 스트래핑 핀이 아님.
 *   GPIO16 → LED MOSFET 게이트 PWM. 09-22 팀 병합본과 같은 핀. WROOM-32D 에서는 비어 있음
 *            (★ WROVER 계열은 GPIO16/17 이 PSRAM 에 물려 있어 못 씁니다. 이 보드는 WROOM 이라 무관).
 *   GPIO25 → 배기팬 12V 릴레이, GPIO26 → 배기팬 PWM (fan_control.ino 실기 확인 배선)
 *   GPIO34 → 배기팬 TACH. 입력 전용 핀 (사용자 결정 2026-10-06, 이전 GPIO27). 내부 풀업이 없어 외부 10kΩ 풀업 필요.
 *   GPIO21 → I2C SDA, GPIO22 → I2C SCL (ADS1115). DevKitC 기본 I2C 핀. 09-22 병합본이 예약해 둔 자리.
 *
 *   비워 둔 핀: 0·2·5·12·15 = 부팅 스트래핑 핀, 1·3 = USB 시리얼(UART0), 6~11 = 플래시.
 *   35·36·39 = 입력 전용 ADC1 (센서 확장용으로 남김). 27 = TACH 를 34 로 옮기며 비움(2026-10-06).
 *
 * ── 토양 센서는 왜 여전히 ADS1115 인가 ───────────────────────────
 *   이 보드는 ADC1(GPIO32~39)이 있어 내부 ADC 로도 읽을 수 있지만, ESP32-CAM 판의 코드·눈금
 *   (0~32767, 0.125mV)·서버 원격 보정·초기 보정값을 그대로 쓰기 위해 ADS1115 를 유지합니다
 *   (사용자 결정 2026-09-29). 팀 하드웨어 구성도에도 ADS1115 + SEN0193 이 들어 있습니다.
 *
 * ── 배선 ─────────────────────────────────────────────────────────
 *   모터  : GPIO18 → 100Ω → 로직레벨 N-MOSFET 게이트, 게이트↔GND 10kΩ 풀다운(필수),
 *           드레인 → 모터(-), 소스 → GND, 모터(+) → 12V(+), 쇼트키 다이오드(1N5819)
 *           캐소드 → 12V(+), 애노드 → 드레인. 12V 전원 GND 와 보드 GND 공통.
 *   LED   : GPIO16 → 100Ω → 로직레벨 N-MOSFET 게이트, 풀다운 10kΩ. 소형 LED 라면
 *           GPIO16 → 220Ω → LED 애노드, 캐소드 → GND 직결도 됩니다(우노 D10 배선과 동일).
 *   배기팬: 4핀 PC 팬. GPIO25 → 릴레이 IN(12V 차단), GPIO26 → 팬 PWM(파랑), GPIO34 ← 팬 TACH(노랑).
 *           GPIO34 는 내부 풀업이 없으므로 TACH 선 ↔ 보드 3.3V 사이에 10kΩ 풀업을 반드시 답니다.
 *           ★ 풀업은 3.3V 에만. 5V/12V 로 풀업하면 입력을 태웁니다.
 *   ADS1115: VDD → 3.3V, GND → GND, SDA → GPIO21, SCL → GPIO22, ADDR → GND(0x48).
 *           모듈에 풀업이 없으면 SDA·SCL 에 4.7kΩ 풀업. 토양 센서 AOUT → A0, VCC → 3.3V 권장.
 *   전원  : USB 5V 또는 12V→5V 컨버터 → 보드 5V(VIN) 핀. 3V3 핀에 5V 를 넣지 말 것.
 *
 *   ★ 우노는 게이트를 5V 로 밀었지만 이 보드는 3.3V 입니다. 로직레벨 MOSFET(IRLZ44N·IRLB8721 등)이어야
 *     합니다. 게이트 풀다운 10kΩ 은 부팅·모드 전환 중 핀이 뜨는 순간을 막는 마지막 안전장치라 빼면 안 됩니다.
 *
 * ── 업로드 ───────────────────────────────────────────────────────
 *   USB 케이블 하나로 됩니다(보드에 USB-시리얼 내장). 아두이노 IDE 보드 = "ESP32 Dev Module".
 *   자동 리셋이 안 되는 케이블·포트면 "Connecting..." 이 보일 때 BOOT 버튼을 누르고 있으면 됩니다.
 */

#ifndef CONFIG_H
#define CONFIG_H

#include <Arduino.h>

#define FW_VERSION "cube-devkit-1.2.3"   // 1.0.0 = 09-29 이식판, 1.1.0 = SHT45·BH1750, 1.1.1 = x/xr/g 진단, 1.2.0 = LAN 원격 업로드, 1.2.1 = 무선 콘솔, 1.2.2 = 칩 온도(09-30), 1.2.3 = 팬 TACH 27→34(10-06)

// ──────────────────────────────────────────────────── 기능 플래그
// 0 이면 컴파일은 되지만 동작하지 않고, 그 항목을 바꾸는 제어 명령은 denied 로 회신합니다.
#define ENABLE_MOTOR  1   // GPIO18 (펌프)
#define ENABLE_LED    1   // GPIO16
#define ENABLE_SOIL   1   // ADS1115 (I2C 21/22)
#define ENABLE_FAN    1   // 배기팬 fan1 (릴레이 25 · PWM 26 · TACH 34) — 09-22 병합본 모듈
#define ENABLE_FAN2   0   // 순환팬 fan2 — 핀 미배정. 가상값만 받아 둠 (fan.cpp 단일 인스턴스)
#define ENABLE_SHT45  1   // 온습도 SHT45 (I2C 0x44, PCA9548A 채널 경유 최대 3개) — 2026-09-29 추가
#define ENABLE_BH1750 1   // 조도 BH1750/GY-302 (I2C 0x23 또는 0x5C, 버스 직결) — 2026-09-29 추가
// LAN 원격 업로드(ArduinoOTA, 코어 내장) — 2026-09-30 추가 (사용자 결정 J86)
//   1 = 벤치: 같은 Wi-Fi 의 아두이노 IDE 네트워크 포트로 업로드 가능(secrets.h OTA_PASSWORD 필요)
//   0 = 현장 큐브: 같은 망의 누구나 시도할 수 있으므로 끈다. 원격 갱신은 젯슨 경유(별도 계획)로
#define ENABLE_LAN_OTA 1

// 무선 콘솔(TCP, 텔넷 방식) — 2026-09-30 추가 (사용자 결정 J87: 벤치 전용 플래그·비밀번호=OTA 와 동일·동시 접속 1·무입력 5분 끊김)
//   같은 Wi-Fi 에서 PuTTY(Raw)·Tera Term 으로 <보드 IP>:2323 에 접속 → 비밀번호 → USB 시리얼과 같은 명령·로그.
//   암호화 없음(비밀번호 평문). 현장 큐브는 0 으로 빌드.
#define ENABLE_WIFI_CONSOLE 1

// ──────────────────────────────────────────────────── 무선 콘솔
static const uint16_t CONSOLE_PORT    = 2323;     // 23 대신 2323: 텔넷 협상 바이트가 안 섞이도록
static const uint32_t CONSOLE_AUTH_MS = 60000;    // 접속 뒤 비밀번호를 이 안에 못 넣으면 끊음
static const uint32_t CONSOLE_IDLE_MS = 300000;   // 인증 뒤 무입력 5분이면 끊음 (사용자 결정)

// ──────────────────────────────────────────────────── LAN 원격 업로드
static const uint16_t OTA_PORT = 3232;                // ArduinoOTA 기본 포트
static const char     OTA_HOSTNAME_PREFIX[] = "cube-";  // IDE 포트 목록에 "cube-<SN> at <IP>" 로 표시
// 업로드 중 규칙(사용자 결정 2026-09-30): MQTT 접속 유지(진행 콜백에서 keepalive 처리), 배기팬 100% 유지,
// 펌프는 시작 즉시 정지, 서버 제어는 ota_busy 로 거부, 센서 읽기·텔레메트리는 전송이 끝날 때까지 멈춤.

// ──────────────────────────────────────────────────── 핀
static const uint8_t PIN_MOTOR_PWM = 18;
// LED 16 유지 (2026-09-30 실기): g18 로 LED 바 점등, g16 로 펌프 구동 → 핀 16·LED 채널 모두 정상.
// LED 선을 16 자리에 꽂았을 때만 안 켜진 것은 그 선 끝과 16 소켓의 접촉 문제(선 교체·재삽입으로 해결).
static const uint8_t PIN_LED_PWM   = 16;
static const uint8_t PIN_I2C_SDA   = 21;
static const uint8_t PIN_I2C_SCL   = 22;
static const uint32_t I2C_HZ       = 100000;

// ──────────────────────────────────────────────────── 배기팬 fan1 (09-22 병합본 fan 모듈, 검증된 배선)
// 4핀 PC 팬. 12V 전원은 릴레이로 끊고, 속도는 PWM 으로, 회전수는 TACH 로 읽습니다.
static const uint8_t PIN_FAN_RELAY = 25;  // 12V 전원 차단용 릴레이
static const uint8_t PIN_FAN_PWM   = 26;  // 속도 제어
// TACH 27 → 34 (사용자 결정 2026-10-06). 34 는 입력 전용·내부 풀업 없음 → 외부 10kΩ 풀업(3.3V) 필수.
static const uint8_t PIN_FAN_TACH  = 34;  // 회전수 피드백 (오픈 컬렉터)

// 25kHz 는 4핀 팬 표준입니다. 가청 주파수 밖이라 PWM 소음이 나지 않습니다.
static const uint32_t FAN_PWM_HZ   = 25000;
static const uint8_t  FAN_PWM_BITS = 8;
// LEDC 타이머 배정 확인 (2026-09-30, 코어 3.3.12 esp32-hal-ledc.c 확인): 채널마다 주파수·분해능이 같은 타이머를 찾고
// 없으면 빈 타이머를 새로 잡는다. 팬(25kHz/8bit)=타이머 0, 모터·LED(25kHz/10bit)=타이머 1 공유. 옛 코어처럼
// 채널 번호로 타이머가 묶이지 않으므로 팬 duty 눈금은 깨지지 않는다. 채널 고정은 불필요.
static const uint32_t FAN_PWM_MAX  = (1UL << FAN_PWM_BITS) - 1;

// GPIO26 → 팬 PWM 직결은 신호를 반전하지 않습니다.
// 외부 NPN 공통 이미터 회로로 한 번 반전할 때만 true 로 바꾸세요.
static const bool FAN_PWM_INVERTED = false;

static const uint8_t  FAN_PULSES_PER_REV = 2;     // 4핀 팬 표준
static const uint32_t FAN_TACH_SETTLE_MS = 500;   // 전원 인가 후 측정 시작까지
// PWM 스위칭 노이즈와 실제 TACH 펄스를 구분하는 실험적 기준입니다(측정으로 찾은 값).
static const uint32_t FAN_MIN_TACH_PULSE_US = 500;

// 진단용으로 허용하는 팬 PWM 주파수 (시리얼 ef21000 / ef25000 / ef28000)
static const uint32_t FAN_DIAG_FREQS[] = { 21000, 25000, 28000 };
static const uint8_t  FAN_DIAG_FREQ_COUNT = 3;

// 팬 스톨 판정: 전원 ON + duty>0 인데 이 시간 동안 유효 TACH 가 0 이면 고장
static const uint32_t FAN_STALL_TIMEOUT_MS = 3000;

// ──────────────────────────────────────────────────── 토양 수분 센서 (ADS1115)
// ADDR 핀을 GND 에 두면 0x48. (VDD 0x49 · SDA 0x4A · SCL 0x4B)
static const uint8_t  ADS1115_ADDR    = 0x48;
static const uint8_t  ADS1115_CHANNEL = 0;        // AIN0 ↔ GND 단극
// 설정 레지스터 비트. PGA ±4.096V → 1 LSB = 0.125 mV, 양의 값 0~32767.
// 팀 중계기 시험 발행기(fake_device.py)가 같은 눈금(raw × 0.000125 = V)을 씁니다.
static const uint16_t ADS1115_PGA_BITS = 0x0200;  // 001 = ±4.096V
static const uint16_t ADS1115_DR_BITS  = 0x00A0;  // 101 = 250 SPS (변환 4ms)
static const uint32_t ADS1115_CONV_WAIT_MS    = 6;    // 변환 시작 뒤 결과를 묻기까지
static const uint32_t ADS1115_CONV_TIMEOUT_MS = 50;   // 이 안에 안 끝나면 오류
static const uint32_t ADS1115_RETRY_MS        = 5000; // 응답 없을 때 다시 찾는 간격
static const int      SOIL_RAW_MAX     = 32767;
static const float    SOIL_MV_PER_LSB  = 0.125f;

// 우노 스케치와 같은 개수·간격. 10회 평균 한 묶음이 약 0.1초.
static const uint8_t  SOIL_SAMPLE_COUNT  = 10;
static const uint32_t SOIL_SAMPLE_GAP_MS = 10;

// 보정 초기값 (ADS1115 원시값). 우노 실측(공기 576 / 물 284, 10비트·5V 기준)을
// 전압으로 바꿔(2.815V / 1.388V) 0.125mV 눈금으로 환산한 것입니다.
// ★ 센서를 3.3V 로 급전하거나 개체가 다르면 맞지 않습니다. ca/cw(시리얼) 또는
//   서버 원격 보정(cal_air/cal_water)으로 실측하면 NVS 에 저장되어 이 값을 덮습니다.
static const int SOIL_AIR_VALUE   = 22520;   // 공기 중 (건조 = 0%)
static const int SOIL_WATER_VALUE = 11100;   // 물 속   (포화 = 100%)

// 두 보정값 차이가 이보다 작으면 % 환산이 거칠어지므로 경고 (≈0.25V)
static const int SOIL_MIN_SPAN = 2000;

// 보정값 NVS(Preferences) 이름공간. 키는 "air" · "water".
// ★ ESP32-CAM 판과 같은 이름이지만 NVS 는 보드마다 따로이므로, 새 보드는 초기값에서 시작합니다.
static const char SOIL_NVS_NAMESPACE[] = "soil";

// ──────────────────────────────────────────────────── I2C 버스 공유 (2026-09-29, 사용자 지시)
// 한 버스(GPIO21/22, 100kHz)에 넷이 붙습니다. 주소가 서로 다르므로 겹치지 않습니다.
//   ADS1115  0x48        토양 (버스 직결)
//   BH1750   0x23/0x5C   조도 (버스 직결, ADDR 핀에 따라 둘 중 하나 — 펌웨어가 둘 다 찾음)
//   PCA9548A 0x70        멀티플렉서 (A0~A2 → GND). 뒤에 SHT45 0x44 가 채널마다 하나
// Wire.begin 은 .ino 의 setup() 에서 한 번만 합니다.
static const uint32_t I2C_TIMEOUT_MS = 25;

// ── PCA9548A ──
static const uint8_t  I2C_MUX_ADDR     = 0x70;
static const uint32_t I2C_MUX_RETRY_MS = 5000;   // 없을 때 다시 찾는 간격

// ── SHT45 온습도 (사용자 결정 2026-09-29: 멀티플렉서 경유, 3개 기준·지금은 2개, 1~3개 모두 동작) ──
// 자리(slot) i 의 센서는 멀티플렉서 채널 SHT45_MUX_CHANNELS[i] 에 있습니다. 채널 배선이 다르면 이 표만 고칩니다.
// 멀티플렉서가 없으면 자리 0 하나를 버스 직결로 찾습니다.
static const uint8_t  SHT45_ADDR            = 0x44;
static const uint8_t  SHT45_MAX             = 3;
static const uint8_t  SHT45_MUX_CHANNELS[SHT45_MAX] = { 0, 1, 2 };
static const uint8_t  SHT45_CMD_MEASURE     = 0xFD;   // SHT4x 고정밀 단발 측정 (최대 8.3ms). 0xF6 = 중간, 0xE0 = 저정밀
static const uint32_t SHT45_MEAS_WAIT_MS    = 10;     // SHT4x 명령 뒤 결과를 읽기까지
static const uint8_t  SHT4X_CMD_SERIAL      = 0x89;   // SHT4x 일련번호 읽기 (6바이트) — 모델 판별용
// ── SHT3x(SHT30/31/35) 호환 (2026-09-29 저녁 추가) ──
// 주소가 SHT4x 와 같은 0x44 인데 명령 체계가 다르다. 부팅·재탐색 때 일련번호(0x89)·상태(0xF32D)로 어느 쪽인지 판별해 맞는 명령을 쓴다.
static const uint8_t  SHT3X_CMD_MEASURE[2]  = { 0x24, 0x00 };   // 단발, 고반복성, 클록 스트레칭 없음 (최대 15.5ms)
static const uint8_t  SHT3X_CMD_STATUS[2]   = { 0xF3, 0x2D };   // 상태 레지스터 읽기 (3바이트) — 모델 판별용
static const uint32_t SHT3X_MEAS_WAIT_MS    = 20;
static const uint32_t SHT45_PERIOD_MS       = 2000;   // 전체 한 바퀴 주기 (텔레메트리 5초보다 짧게)
static const uint32_t SHT45_RETRY_MS        = 5000;   // 없는 자리를 다시 찾는 간격
static const uint8_t  SHT45_MAX_FAILS       = 3;      // 연속 실패 → 없음 처리

// ── BH1750 조도 (근거: 2026-09-29_BH1750_GY-302_한국어설명서_Codex.html) ──
static const uint8_t  BH1750_ADDRS[]        = { 0x23, 0x5C };   // ADDR LOW / HIGH
static const uint8_t  BH1750_ADDR_COUNT     = 2;
static const uint8_t  BH1750_CMD_POWER_ON   = 0x01;
static const uint8_t  BH1750_CMD_CONT_HRES  = 0x10;   // 연속 H-Resolution, 1 lx, 대표 120ms
static const uint32_t BH1750_FIRST_WAIT_MS  = 180;    // 첫 변환 최대 시간
static const uint32_t BH1750_PERIOD_MS      = 1000;
static const uint32_t BH1750_RETRY_MS       = 5000;
static const uint8_t  BH1750_MAX_FAILS      = 3;
static const float    BH1750_COUNTS_PER_LUX = 1.2f;   // lux = raw ÷ 1.2 (MTreg 기본 69). 투명창 보정은 현장 실측으로

// ──────────────────────────────────────────────────── 모터 = 급수 펌프 (GPIO18)
// 우노와 같은 25kHz. 분해능은 우노의 640단계 대신 10bit(1024단계).
static const uint32_t MOTOR_PWM_HZ   = 25000;
static const uint8_t  MOTOR_PWM_BITS = 10;
static const uint32_t MOTOR_PWM_FULL = (1UL << MOTOR_PWM_BITS);   // 1024 (우노 PWM_TOP+1)

// ★ 실측값(우노 스케치와 동일). 이 모터는 duty 83% 미만에서 회전을 유지하지 못합니다.
//   입력 1~100% 를 실제 duty 83~100% 구간에 비례 배분합니다. 모터를 바꾸면 다시 실측.
static const uint8_t MOTOR_MIN_DUTY_PERCENT = 83;

// 1% 가감속 간격. 5ms → 0~100% 약 0.5초 (우노 RAMP_STEP_MS 와 동일)
static const uint32_t MOTOR_RAMP_STEP_MS = 5;

// 펌프를 켤 때 모터에 주는 속도 %. 1 = 최소 duty(83%) 로 가동 (사용자 결정 2026-09-23)
static const uint8_t PUMP_MOTOR_PERCENT = 1;

// ──────────────────────────────────────────────────── 진단 모드 (우노 d 명령)
// 모터 핀(GPIO18)을 보정·램프 없는 raw duty 로 구동하며 주파수를 바꿔 봅니다.
// 우노 f0~f5 와 같은 순서·같은 주파수. LEDC 제약으로 30Hz 만 12bit 가 필요합니다
// (freq = 80MHz / (분주 × 2^bits), 분주 ≤ 1023.996 → 25kHz 는 최대 11bit, 30Hz 는 최소 12bit).
struct DiagPwmMode {
  const char* label;
  uint32_t    hz;
  uint8_t     bits;
};
static const DiagPwmMode DIAG_PWM_MODES[] = {
  { "25 kHz", 25000, 10 },   // f0 — 일반 모드와 같은 주파수
  { "4 kHz",   4000, 10 },   // f1
  { "1 kHz",   1000, 10 },   // f2 — 실사용 하한
  { "500 Hz",   500, 10 },   // f3
  { "100 Hz",   100, 10 },   // f4
  { "30 Hz",     30, 12 },   // f5 — 12bit 필요
};
static const uint8_t DIAG_PWM_MODE_COUNT = sizeof(DIAG_PWM_MODES) / sizeof(DIAG_PWM_MODES[0]);

// ──────────────────────────────────────────────────── LED (GPIO16)
// 우노 일반 모드와 동일: 25kHz 고정, 감마 보정(밝기 = 입력²), 1%/5ms 램프.
static const uint32_t LED_PWM_HZ   = 25000;
static const uint8_t  LED_PWM_BITS = 10;
static const uint32_t LED_PWM_FULL = (1UL << LED_PWM_BITS);       // 1024
static const bool     LED_GAMMA_CORRECTION = true;
static const uint32_t LED_RAMP_STEP_MS     = 5;

// ──────────────────────────────────────────────────── 안전 상수
// 서버와 중복 구현입니다. 의도된 설계입니다 — 서버·브로커가 털려도 여기서 막습니다.
// 이 보드가 직접 핀을 잡고 있으므로 연결선이 빠져도 모터는 멈춥니다(게이트 풀다운 10kΩ 이 마지막 안전장치).
static const uint32_t PUMP_MAX_MS      = 30000;   // 최대 연속 가동 (사용자 결정 2026-09-23, 09-28 유지 확인)
// 재가동 최소 간격. 0 = 없음 (사용자 결정 2026-09-28: 펌프 안전장치 해제. 되살리려면 60000)
static const uint32_t PUMP_COOLDOWN_MS = 0;
static const uint32_t ACT_MAX_DURATION_MS = 60000; // 전 액츄에이터 공통 상한

// 제어 리스: 마지막으로 받아들인 제어 명령 뒤 이 시간 안에 새 명령이 없으면
// 리스가 만료됩니다. status 의 lease_ms 로 알립니다(LEASE_ENFORCED 가 false 면 0 으로 보고).
static const uint32_t LEASE_MS         = 15000;
// 리스 만료 때 출력을 끌지. (사용자 결정 2026-09-28: 끄지 않음. 펌프·LED·팬 모두 마지막 명령값 유지)
//   false = 만료돼도 아무것도 끄지 않음 (로그만). 펌프는 PUMP_MAX_MS 로만 멈춤
//   true  = 만료 시 정지. 무엇을 끌지는 LEASE_STOPS_ALL 로
static const bool     LEASE_ENFORCED   = false;
// LEASE_ENFORCED 일 때: true = 팬 SAFE_FAN_PERCENT · 모터 0 · LED 0 (09-22 병합본과 동일), false = 펌프만 정지
static const bool     LEASE_STOPS_ALL  = false;
// 부팅 직후(그리고 LEASE_STOPS_ALL 이면 리스 만료 시) 배기팬에 주는 값. 09-22 사용자 결정(J39): 환기 유지 100%.
// ★ 켜자마자 배기팬이 100% 로 돕니다. fan2 가상값도 이 값으로 시작합니다.
static const uint8_t  SAFE_FAN_PERCENT = 100;

// ──────────────────────────────────────────────────── 주기
static const uint32_t TELEMETRY_INTERVAL_MS = 5000;
static const uint32_t SERIAL_REPORT_MS      = 1000;   // 우노처럼 1초마다 상태 한 줄 (fan 모듈의 RPM 측정 창도 이 값)
static const uint32_t SERIAL_BAUD           = 115200;

// ──────────────────────────────────────────────────── 네트워크 (comm.cpp)
static const uint32_t NETWORK_RETRY_MS  = 5000;
static const uint16_t MQTT_KEEPALIVE_S  = 30;   // 암호화 접속이라 프로젝트 기본(10)보다 넉넉히
static const uint16_t MQTT_SOCKET_S     = 10;
// 프레임 한 개 최대. PubSubClient 버퍼 = 이 값이며 MQTT 머리(≈40B)+프레임 전체가 들어가야 합니다.
// 온습도·조도 필드가 붙어 최악 텔레메트리 ≈ 880B(머리 포함)라 1024 → 1280 으로 늘림 (2026-09-29).
static const uint16_t COMM_FRAME_BUF    = 1280;
// 시각이 이 값을 넘어야 인증서 유효기간 검사를 통과합니다(1970년이면 거부됨).
static const long     TIME_OK_EPOCH     = 1700000000L;

// ──────────────────────────────────────────────────── 출력 갈래 (2026-09-30)
// 모든 모듈의 `Serial.…` 을 TeeOut 으로 돌려 USB 와 무선 콘솔에 같은 로그가 나가게 합니다.
// 코어 3.x 는 원래 `#define Serial Serial0` 이므로 여기서 다시 정의합니다(config.h 를 포함한 파일에만 적용).
// 라이브러리 헤더들은 `Serial` 을 쓰지 않는 것을 확인했습니다(WiFi·Network·Wire·Preferences·ArduinoOTA·Update·PubSubClient).
#include "tee_serial.h"
#ifdef Serial
#undef Serial
#endif
#define Serial TeeOut

#endif  // CONFIG_H
