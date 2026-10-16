/*
 * 헬피아이 — ESP32 DevKitC(WROOM-32D) 통합판 (2026-09-29)
 * ==================================================================
 * esp32_cam_node(ESP32-CAM 단독 통합판 1.0.2)를 새 ESP32 DevKitC 로 옮긴 판입니다. 장치 제어·센서
 * 읽기·서버 통신·안전 규칙·원격 보정은 ESP32-CAM 판 그대로이고, 핀만 바뀌었으며, 핀이 넉넉해져
 * 09-22 병합본(esp32_cube_node)의 배기팬 모듈(fan1)을 다시 넣었습니다. 배경·핀·배선은 config.h 머리말.
 *
 *   젯슨 서버 --Wi-Fi/TLS MQTT--> ESP32 DevKitC --GPIO18 PWM--> 모터(펌프) MOSFET
 *                                               --GPIO16 PWM--> LED MOSFET
 *                                               --GPIO25/26/34--> 배기팬(릴레이 · PWM · TACH)
 *                                               --GPIO19/23/35--> 순환팬(릴레이 · PWM · TACH) (2026-10-13)
 *                                               --I2C 21/22--> ADS1115 --> 토양 센서 AOUT
 *                                               --UART2 TX17/RX27--> RS485 변환기 --> 토양 7종 센서 (2026-10-12)
 *                                               --UART1 TX32/RX33--> CO2 센서 EXSEN HX-2000U (2026-10-13)
 *
 *   ESP32-CAM 판에서 그대로 : motor(최소 duty 83% 보정 + 램프 + 진단 모드 d/f0~f5), led(감마 + 램프),
 *                             soil(ADS1115 10회 평균 · NVS 보정 · 원격 보정), comm(Wi-Fi + NTP + TLS MQTT +
 *                             VALUE v1 프레임 + 세션·순번), 제어 규칙·리스·펌프 안전 상수, 시리얼 명령 체계,
 *                             텔레메트리·status·ack 필드
 *   09-22 병합본에서 가져옴  : fan 모듈(릴레이 + PWM + TACH + 스톨 감지). fan1 이 가상값에서 실제 출력으로
 *   바뀐 것                 : 핀(모터 13→18, LED 2→16, I2C 14/15→21/22), 보드 "ESP32 Dev Module",
 *                             시리얼 팬 명령 e0~e100 · ef<Hz> 추가, 1초 상태 줄에 팬 표시
 *
 * ── 실물 구성 ────────────────────────────────────────────────────
 *   pump = 12V DC 모터(GPIO18). 켜면 모터 PUMP_MOTOR_PERCENT(1% = duty 83%), 30초 뒤 자동 정지
 *   led1 = LED(GPIO16). led2 = 하드웨어 없음
 *   fan1 = 배기팬(GPIO25/26/34, fan 모듈. TACH 는 10-06 27→34). 부팅 직후 SAFE_FAN_PERCENT(100%)로 돕니다(09-22 결정 J39)
 *   fan2 = 순환팬(GPIO19/23/35, fan 모듈 두 번째 인스턴스, 2026-10-13 사용자 결정). 배기팬과 같은 규칙으로 실제 출력.
 *          ENABLE_FAN2=0 으로 빌드하면 예전처럼 값만 받아 둡니다(가상값과 다르면 denied). 시리얼 a0~a100 / af<Hz>
 *   토양 = ADS1115 AIN0. 원시값 0~32767 (0.125mV 눈금), 전압은 moisture_v 로 함께 보고
 *   토양 7종 = RS485 Modbus 센서(UART2 TX17/RX27, 자동 흐름제어 변환기 SZH-CVBE-010). 2026-10-12 추가(rs485_soil 모듈).
 *            텔레메트리 soil_rs485_ok · soil_temp · soil_vwc · soil_ec · soil_salinity · soil_n · soil_p · soil_k · soil_ph,
 *            status soil_rs485_ok · soil_rs485_addr. 시리얼 b / b<addr> / bs / br. 기존 ADS1115 moisture_* 는 그대로.
 *   CO2  = EXSEN HX-2000U(UART1 TX32/RX33, 9600 8N1, 요청-응답). 2026-10-13 추가(co2_hx2000u 모듈).
 *            텔레메트리 co2_ok · co2_ppm(첫 응답 뒤 15분 예열 중·없음이면 null), status co2_ok. 시리얼 co / cor.
 *
 * ── 제어 규칙 (ESP32-CAM 판과 동일) ─────────────────────────────
 *   한 프레임에 7개 키(session, seq, pump, led1, led2, fan1, fan2)가 다 옵니다.
 *   하드웨어 없는 항목은 지금 값과 같을 때만 통과합니다(다르면 denied). fault 난 모듈도 같은 규칙.
 *   진단 모드(시리얼 d)에서는 모든 제어를 outputs_disabled 로 거부합니다.
 *   받아들인 명령은 LEASE_MS 동안 "리스 유효" 로 보고되지만, 만료돼도 출력을 끄지 않습니다
 *   (config.h LEASE_ENFORCED=false, 사용자 결정 2026-09-28). 배기팬도 마지막 명령값을 유지합니다.
 *   펌프는 PUMP_MAX_MS(30초) 뒤 스스로 멈춥니다. 재가동 대기(PUMP_COOLDOWN_MS)는 0 = 없음.
 *   ★ 시리얼·진단 모드로 켠 펌프에도 PUMP_MAX_MS 는 적용됩니다(쿨다운은 서버 명령에만).
 *
 * ── 서버 원격 보정 (…/notify/config, VALUE v1 프레임 안의 JSON) ──────
 *   {"air":N,"water":N}   보정값 직접 지정 (한쪽만 보내도 됨. ADS1115 원시값 0~32767)
 *   {"cal_air":1}         지금 센서값(10회 평균)을 공기 중 값(0%)으로 저장
 *   {"cal_water":1}       지금 센서값을 물 속 값(100%)으로 저장
 *   {"cal_reset":1}       config.h 초기값으로 되돌림
 *   받는 즉시 status 를 발행(cal_pending=true 면 측정 중)하고, 측정이 끝나면 status 와
 *   telemetry 를 다시 발행합니다. 젯슨 api: POST /cubes/{sn}/config (ESP32-CAM 판과 동일).
 *
 * ── 대상 환경 ─────────────────────────────────────────────────────
 *   Arduino-ESP32 core 3.x, 보드 "ESP32 Dev Module" (esp32:esp32:esp32)
 *   라이브러리: PubSubClient (knolleary). Wire · Preferences 는 코어 내장.
 *   같은 폴더: secrets.h (secrets.h.example 복사 후 채움) / certs.h (루트 인증서, 공개)
 */

#include <Arduino.h>
#include <WiFi.h>      // WiFi.RSSI(), localIP()
#include <Wire.h>      // I2C 버스는 여기서 한 번 엽니다 (ADS1115 · PCA9548A · SHT45 · BH1750 공유)
#include <math.h>

#include "config.h"
#include "secrets.h"
#include "comm.h"
#include "fan.h"
#include "motor.h"
#include "led.h"
#include "soil.h"
#include "i2c_mux.h"   // 2026-09-29 추가: PCA9548A
#include "sht45.h"     // 2026-09-29 추가: 온습도 (채널 경유 1~3개)
#include "bh1750.h"    // 2026-09-29 추가: 조도
#include "rs485_soil.h" // 2026-10-12 추가: RS485 Modbus 토양 7종 (UART2)
#include "co2_hx2000u.h" // 2026-10-13 추가: CO2 센서 EXSEN HX-2000U (UART1)
#if ENABLE_LAN_OTA
#include <ArduinoOTA.h>  // 2026-09-30 추가: 같은 Wi-Fi 에서 IDE 네트워크 포트 업로드 (코어 내장, 설치 불필요)
#endif

// ──────────────────────────────────────────────────── 무선 콘솔 상태 (2026-09-30, 사용자 결정 J87)
#if ENABLE_WIFI_CONSOLE
WiFiServer consoleServer(CONSOLE_PORT);
WiFiClient consoleClient;
#endif
bool     consoleStarted = false;
bool     consoleAuthed  = false;
uint32_t consoleConnectMs = 0;
uint32_t consoleLastInputMs = 0;
char     consoleLine[32];
uint8_t  consoleLen = 0;
char     consoleState[16] = "off";   // status 의 console 필드: off / ready / connected

// ──────────────────────────────────────────────────── LAN 원격 업로드 상태 (2026-09-30)
// otaActive 가 true 인 동안: 서버 제어는 ota_busy, 펌프 정지, 센서·텔레메트리 정지. 팬은 그대로(사용자 결정).
bool     otaStarted = false;   // ArduinoOTA.begin() 을 불렀는지 (Wi-Fi 가 붙은 뒤 한 번)
bool     otaActive  = false;   // 전송 진행 중
char     otaState[24] = "off"; // status 의 ota 필드: off / ready / uploading / error:<n>
uint32_t otaLastTickMs = 0;

// 소수 값 → JSON 텍스트. NAN 이면 null (센서 없음).
static void numText(char* out, size_t cap, float v, uint8_t decimals) {
  if (isnan(v)) snprintf(out, cap, "null");
  else          snprintf(out, cap, "%.*f", static_cast<int>(decimals), static_cast<double>(v));
}

// 정수 값 → JSON 텍스트. 음수(-1 = 없음)면 null. RS485 토양 센서의 EC·염분·N·P·K 용 (2026-10-12)
static void intText(char* out, size_t cap, int v) {
  if (v < 0) snprintf(out, cap, "null");
  else       snprintf(out, cap, "%d", v);
}

// ESP32 칩 내부 온도(°C). 코어 내장 temperatureRead(). 원래 ESP32 는 보정 없는 ROM 센서라 개체마다 수 °C 차이가 나고,
// 센서가 없는 개체는 53.3 이 고정으로 나온다(원시값 128). 절대값보다는 "평소보다 얼마나 올랐나" 를 보는 용도.
static float chipTemperatureC() {
  const float t = temperatureRead();
  if (isnan(t) || t < -40.0f || t > 150.0f) return NAN;
  return t;
}

// ──────────────────────────────────────────────────── 하드웨어 없는 출력의 가상값
// led2(없음)는 실제 핀이 없습니다. fan2(순환팬)도 ENABLE_FAN2=0 으로 빌드하면 여기로 떨어집니다.
// 서버가 "현재값 그대로" 를 보낼 때 통과시키려면 마지막으로 받아들인 값을 기억해 둬야 합니다.
// ★ 텔레메트리도 같은 값을 내보내야 제어판의 되돌림 값이 여기와 일치합니다.
uint8_t virtFan2 = SAFE_FAN_PERCENT;   // ENABLE_FAN2=1(2026-10-13) 이면 쓰지 않음
uint8_t virtLed2 = 0;
#if !ENABLE_FAN
uint8_t virtFan1 = SAFE_FAN_PERCENT;   // 팬 모듈을 끄면 ESP32-CAM 판처럼 가상값으로 동작
#endif

// fan1 을 다루는 얇은 도우미. ENABLE_FAN=0 이면 가상값으로 떨어집니다.
uint8_t fan1Percent() {
#if ENABLE_FAN
  return fan::percent();
#else
  return virtFan1;
#endif
}
bool fan1Set(uint8_t percent) {
#if ENABLE_FAN
  return fan::setPercent(percent);
#else
  virtFan1 = percent;
  return true;
#endif
}
int  fan1Rpm()     {
#if ENABLE_FAN
  return fan::rpm();
#else
  return -1;
#endif
}
bool fan1Stalled() {
#if ENABLE_FAN
  return fan::stalled();
#else
  return false;
#endif
}

// fan2(순환팬)를 다루는 얇은 도우미 (2026-10-13). ENABLE_FAN2=0 이면 예전처럼 가상값으로 떨어집니다.
uint8_t fan2Percent() {
#if ENABLE_FAN2
  return fan::percent(fan::FAN_CIRC);
#else
  return virtFan2;
#endif
}
bool fan2Set(uint8_t percent) {
#if ENABLE_FAN2
  return fan::setPercent(percent, fan::FAN_CIRC);
#else
  virtFan2 = percent;
  return true;
#endif
}
int  fan2Rpm()     {
#if ENABLE_FAN2
  return fan::rpm(fan::FAN_CIRC);
#else
  return -1;
#endif
}

// ──────────────────────────────────────────────────── 리스·펌프 안전
uint32_t leaseUntil = 0;      // 0 = 리스 없음
uint32_t appliedSeq = 0;      // 마지막으로 실제 적용한 명령의 순번

uint32_t pumpStartedMs = 0;   // 펌프(모터) 켠 시각. 0 = 꺼짐
uint32_t lastPumpStopMs = 0;
bool     pumpEverRan = false;

uint32_t lastTelemetryMs = 0;
uint32_t lastSerialMs = 0;
String   serialLine;
bool     serialOverflow = false;

void publishTelemetry();
void publishStatus();
void printStatus();

// 진단 모드에서는 모터 핀이 raw duty 로 돌므로 그것도 "펌프 가동" 입니다.
bool pumpOn()      { return motor::diagActive() ? (motor::diagDuty() > 0) : (motor::target() > 0); }
bool leaseActive() { return leaseUntil != 0 && static_cast<int32_t>(millis() - leaseUntil) < 0; }

// 팀 통합본 규칙: 펌프가 멈춘 뒤 PUMP_COOLDOWN_MS 동안은 다시 켜지 않습니다. 0 이면 규칙 없음.
bool pumpLocked() {
  if (PUMP_COOLDOWN_MS == 0) return false;
  return pumpEverRan && (millis() - lastPumpStopMs) < PUMP_COOLDOWN_MS;
}

bool pumpStart() {
  if (!motor::setPercent(PUMP_MOTOR_PERCENT)) return false;   // fault·진단 모드면 실패
  pumpStartedMs = millis();
  pumpEverRan = true;
  Serial.printf("  [PUMP] 급수 시작: 모터 %u%% (최대 %lu ms)\n",
                static_cast<unsigned>(PUMP_MOTOR_PERCENT), static_cast<unsigned long>(PUMP_MAX_MS));
  return true;
}

void pumpStop(const char* why) {
  if (motor::diagActive()) motor::diagSetDuty(0);
  else                     motor::setPercent(0);     // 램프로 내려갑니다
  pumpStartedMs = 0;
  lastPumpStopMs = millis();
  Serial.printf("  [PUMP] 정지 (%s), 쿨다운 %lu ms\n", why, static_cast<unsigned long>(PUMP_COOLDOWN_MS));
}

// 부팅 시(그리고 LEASE_STOPS_ALL 이면 리스 만료 시) 돌아가는 상태.
// 배기팬 SAFE_FAN_PERCENT(환기 유지) · 모터 0 · LED 0. 09-22 병합본과 같은 규칙.
void outputsSafe(const char* why) {
#if ENABLE_FAN
  if (!fan::faulted()) fan::setPercent(SAFE_FAN_PERCENT);
#else
  virtFan1 = SAFE_FAN_PERCENT;
#endif
#if ENABLE_FAN2
  if (!fan::faulted(fan::FAN_CIRC)) fan::setPercent(SAFE_FAN_PERCENT, fan::FAN_CIRC);   // 순환팬도 환기 유지 (2026-10-13)
#else
  virtFan2 = SAFE_FAN_PERCENT;
#endif
  led::setPercent(0);
  virtLed2 = 0;
  if (pumpOn()) pumpStop(why);
  Serial.printf("[안전] 안전값 적용 (%s): 팬 %u%% · 모터 0 · LED 0\n", why, static_cast<unsigned>(SAFE_FAN_PERCENT));
}

// 리스 만료. 기본(LEASE_ENFORCED=false)은 아무것도 끄지 않고 로그만 남깁니다.
void onLeaseExpired() {
  if (!LEASE_ENFORCED) {
    Serial.println("[리스] 만료 — 출력 유지 (LEASE_ENFORCED=false)");
    return;
  }
  if (LEASE_STOPS_ALL) {
    outputsSafe("리스 만료");
    return;
  }
  if (pumpOn()) pumpStop("리스 만료");
  Serial.println("[안전] 리스 만료 — 펌프만 정지, 팬·LED 는 마지막 명령값 유지");
}

// ──────────────────────────────────────────────────── 제어 명령 적용
// comm 이 프레임·세션·순번 검증을 끝낸 뒤 부릅니다. 반환값이 그대로 ack 의 result 입니다.
const char* applyControl(const comm::ControlCmd& c) {
  // 원격 업로드 중에는 출력을 바꾸지 않습니다 (2026-09-30).
  if (otaActive) return "ota_busy";
  // 진단 모드(시리얼 d)는 벤치 작업 중이라는 뜻입니다. 서버 제어를 받지 않습니다.
  if (motor::diagActive()) return "outputs_disabled";

  const uint8_t curFan1 = fan1Percent();
  const uint8_t curFan2 = fan2Percent();
  const uint8_t curLed1 = led::percent();
  const bool    curPump = pumpOn();

  // 하드웨어 없는 항목을 바꾸려 하면 거부 (팀 통합본의 ENABLE=0 → denied 규칙과 같음)
#if !ENABLE_FAN2
  if (c.fan2 != curFan2) return "denied";    // 순환팬 핀 미배정 빌드
#endif
  if (c.led2 != virtLed2) return "denied";   // led2 는 하드웨어 없음

  // 고장(fault) 난 모듈을 바꾸려 하면 거부. 같은 값(끄기 포함)은 통과합니다.
#if ENABLE_FAN
  if (fan::faulted() && c.fan1 != curFan1) return "denied";
#endif
#if ENABLE_FAN2
  if (fan::faulted(fan::FAN_CIRC) && c.fan2 != curFan2) return "denied";   // 순환팬 (2026-10-13)
#endif

  if (c.pump && !curPump && pumpLocked()) return "pump_locked";

  // ── 적용 ──
  if (c.fan1 != curFan1 && !fan1Set(c.fan1))       return "denied";   // 팬 fault
  if (c.fan2 != curFan2 && !fan2Set(c.fan2))       return "denied";   // 순환팬 fault (2026-10-13)
  if (c.led1 != curLed1 && !led::setPercent(c.led1)) return "denied";   // LED fault
  if (c.pump && !curPump) {
    if (!pumpStart()) return "denied";                                    // 모터 fault
  } else if (!c.pump && curPump) {
    pumpStop("서버 명령");
  }

  appliedSeq = c.seq;
  leaseUntil = millis() + LEASE_MS;
  return "ok";
}

// ──────────────────────────────────────────────────── 설정 프레임 = 원격 보정
// 키는 comm::jsonInt 로 꺼냅니다(따옴표 포함 "키" 를 찾으므로 cal_air 와 air 가 섞이지 않음).
void onConfig(const char* json, uint16_t len) {
  bool handled = false;
  int air = 0, water = 0, flag = 0, ledMode = 0;

  const bool hasAir   = comm::jsonInt(json, len, "air", &air);
  const bool hasWater = comm::jsonInt(json, len, "water", &water);
  if (hasAir && hasWater)  { soil::setCalibration(air, water); handled = true; }
  else if (hasAir)         { soil::setAir(air);                handled = true; }
  else if (hasWater)       { soil::setWater(water);            handled = true; }

  if (comm::jsonInt(json, len, "cal_reset", &flag) && flag) { soil::resetCalibration();   handled = true; }
  if (comm::jsonInt(json, len, "cal_air",   &flag) && flag) { soil::startCalibration(true);  handled = true; }
  if (comm::jsonInt(json, len, "cal_water", &flag) && flag) { soil::startCalibration(false); handled = true; }

  if (comm::jsonInt(json, len, "led_mode", &ledMode)) {
    led::setModeIndex(static_cast<uint8_t>(ledMode));   // 이 판은 25kHz 고정. 항상 거부·로그
    handled = true;
  }

  if (!handled) Serial.printf("[CFG] 아는 키가 없습니다: %.*s\n", static_cast<int>(len), json);
  publishStatus();   // 지금 보정값(air/water)과 측정 중 여부(cal_pending)를 알립니다
}

// ──────────────────────────────────────────────────── 발행
// 젯슨 api·대시보드가 읽는 필드(ESP32-CAM 판과 동일). fan1_rpm · fan1 · fan1_stall 이 실제 값으로 바뀜.
// ★ 값은 정수·소수·true/false·null 만. api 기록기는 숫자만 InfluxDB 로 넘기고 null 은 버립니다.
void publishTelemetry() {
  if (!comm::connected()) return;

  const int  soilRaw = soil::raw();          // -1 = 없음
  const int  soilPct = soil::moisture();     // -1 = 없음/미보정
  const int  soilMv  = soil::millivolts();
  const bool diag    = motor::diagActive();
  const int  motorOut = diag ? motor::diagDuty() : motor::output();
  const int  motorSet = diag ? motor::diagDuty() : motor::target();

  char rawText[12], pctText[12], vText[16];
  if (soilRaw < 0) snprintf(rawText, sizeof(rawText), "null"); else snprintf(rawText, sizeof(rawText), "%d", soilRaw);
  if (soilPct < 0) snprintf(pctText, sizeof(pctText), "null"); else snprintf(pctText, sizeof(pctText), "%d", soilPct);
  if (soilMv  < 0) snprintf(vText,   sizeof(vText),   "null"); else snprintf(vText,   sizeof(vText),   "%d.%03d", soilMv / 1000, soilMv % 1000);

  // 온습도(평균 + 개별 1~3) · 조도. 없는 것은 null (2026-09-29 추가)
  char tAvg[12], hAvg[12], luxText[16], tS[SHT45_MAX][12], hS[SHT45_MAX][12];
  numText(tAvg, sizeof(tAvg), sht45::avgTemperature(), 1);
  numText(hAvg, sizeof(hAvg), sht45::avgHumidity(), 1);
  numText(luxText, sizeof(luxText), light::lux(), 1);
  for (uint8_t i = 0; i < SHT45_MAX; i++) {
    numText(tS[i], sizeof(tS[i]), sht45::temperature(i), 1);
    numText(hS[i], sizeof(hS[i]), sht45::humidity(i), 1);
  }
  // ESP32 칩 내부 온도 (2026-09-30 추가, 사용자 지시). 원래 ESP32 는 보정 안 된 센서라 절대값보다 변화 추이용.
  // 젯슨 api 기록기가 숫자 필드를 그대로 InfluxDB 에 쓰므로 시계열은 서버 변경 없이 쌓인다.
  char chipText[12];
  numText(chipText, sizeof(chipText), chipTemperatureC(), 1);

  // RS485 토양 7종 (2026-10-12 추가, 사용자 결정 soil_ 접두어). 센서 없음·응답 끊김이면 전부 null.
  char sTemp[12], sVwc[12], sEc[12], sSal[12], sN[12], sP[12], sK[12], sPh[12];
  numText(sTemp, sizeof(sTemp), rs485soil::temperature(), 1);
  numText(sVwc,  sizeof(sVwc),  rs485soil::vwc(), 1);
  intText(sEc,   sizeof(sEc),   rs485soil::ec());
  intText(sSal,  sizeof(sSal),  rs485soil::salinity());
  intText(sN,    sizeof(sN),    rs485soil::nitrogen());
  intText(sP,    sizeof(sP),    rs485soil::phosphorus());
  intText(sK,    sizeof(sK),    rs485soil::potassium());
  numText(sPh,   sizeof(sPh),   rs485soil::ph(), 1);

  // CO2 HX-2000U (2026-10-13 추가, 사용자 결정: 예열 15분 동안과 없음은 null)
  char co2Text[12];
  intText(co2Text, sizeof(co2Text), co2::ppm());

  comm::publishf("/public",
    "{\"session\":\"%s\",\"uptime_ms\":%lu,\"applied_seq\":%lu,"
    "\"ads1115_ok\":%s,\"moisture_calibrated\":%s,"
    "\"moisture_raw\":%s,\"moisture_v\":%s,\"moisture_pct\":%s,"
    "\"fan1_rpm\":%d,\"fan2_rpm\":%d,\"led1\":%u,\"led2\":%u,\"pump\":%s,\"fan1\":%u,\"fan2\":%u,"
    "\"lease_active\":%s,\"pump_locked\":%s,\"rssi_dbm\":%d,"
    "\"fan1_stall\":%d,\"motor_duty\":%d,\"motor_set\":%d,\"led_freq\":%d,\"led_mode\":%d,"
    "\"diag_mode\":%s,"
    "\"temperature\":%s,\"humidity\":%s,\"lux\":%s,"
    "\"temp_1\":%s,\"hum_1\":%s,\"temp_2\":%s,\"hum_2\":%s,\"temp_3\":%s,\"hum_3\":%s,"
    "\"sht45_count\":%u,\"chip_temp\":%s,"
    "\"soil_rs485_ok\":%s,\"soil_temp\":%s,\"soil_vwc\":%s,\"soil_ec\":%s,\"soil_salinity\":%s,"
    "\"soil_n\":%s,\"soil_p\":%s,\"soil_k\":%s,\"soil_ph\":%s,"
    "\"co2_ok\":%s,\"co2_ppm\":%s}",
    comm::session(), static_cast<unsigned long>(millis()), static_cast<unsigned long>(appliedSeq),
    soil::ok() ? "true" : "false",
    soil::calibrated() ? "true" : "false",
    rawText, vText, pctText,
    fan1Rpm(),                               // -1 = 측정 불가(전원 OFF·안정화 대기·미검출·팬 모듈 없음)
    fan2Rpm(),                               // 순환팬 실측 (2026-10-13). ENABLE_FAN2=0 이면 -1
    static_cast<unsigned>(led::percent()), static_cast<unsigned>(virtLed2),
    pumpOn() ? "true" : "false",
    static_cast<unsigned>(fan1Percent()), static_cast<unsigned>(fan2Percent()),
    (LEASE_ENFORCED && leaseActive()) ? "true" : "false",   // 강제하지 않으면 항상 false
    pumpLocked() ? "true" : "false",
    static_cast<int>(WiFi.RSSI()),
    fan1Stalled() ? 1 : 0,
    motorOut,                                // 램프 중에는 motor_set 과 다릅니다
    motorSet,
    static_cast<int>(led::frequencyHz()),
    static_cast<int>(led::modeIndex()),
    diag ? "true" : "false",
    tAvg, hAvg, luxText,
    tS[0], hS[0], tS[1], hS[1], tS[2], hS[2],
    static_cast<unsigned>(sht45::count()),
    chipText,
    rs485soil::ok() ? "true" : "false",
    sTemp, sVwc, sEc, sSal, sN, sP, sK, sPh,
    co2::ok() ? "true" : "false",            // CO2 HX-2000U (2026-10-13)
    co2Text);
}

void publishStatus() {
  if (!comm::connected()) return;
  comm::publishf("/status",
    "{\"name\":\"%s\",\"vendor\":\"%s\",\"model\":\"%s\",\"sn\":\"%s\",\"fw\":\"%s\",\"ip\":\"%s\","
    "\"transport\":\"wifi-mqtt-tls\",\"session\":\"%s\",\"last_received_seq\":%lu,"
    "\"outputs_enabled\":%s,\"lease_ms\":%lu,\"pump_max_ms\":%lu,\"protocol\":\"ag-value-v1\","
    "\"fan_fault\":%d,\"fan2_enabled\":%d,\"motor_fault\":%d,\"motor_min_duty\":%d,"
    "\"led_fault\":%d,\"led_mode\":%d,\"led_modes\":\"%s\",\"air\":%d,\"water\":%d,"
    "\"ads1115_ok\":%s,\"cal_pending\":%s,\"diag_mode\":%s,"
    "\"sht45_count\":%u,\"sht45_mux\":%s,\"sht_model\":\"%s\",\"bh1750_ok\":%s,\"bh1750_addr\":%u,"
    "\"soil_rs485_ok\":%s,\"soil_rs485_addr\":%d,\"co2_ok\":%s,"
    "\"ota\":\"%s\",\"console\":\"%s\"}",
    DEVICE_NAME, DEVICE_VENDOR, DEVICE_MODEL, DEVICE_SN, FW_VERSION,
    WiFi.localIP().toString().c_str(),
    comm::session(), static_cast<unsigned long>(comm::lastReceivedSeq()),
    motor::diagActive() ? "false" : "true",   // 진단 모드면 서버 제어를 받지 않음
    static_cast<unsigned long>(LEASE_ENFORCED ? LEASE_MS : 0),   // 0 = 리스로 끄지 않음
    static_cast<unsigned long>(PUMP_MAX_MS),
    fan::faulted() ? 1 : 0,                   // ENABLE_FAN=0 이면 항상 0
    ENABLE_FAN2 ? 1 : 0,
    motor::faulted() ? 1 : 0,
    static_cast<int>(motor::minDutyPercent()),   // 슬라이더 1% 가 실제 duty 83% 인 것을 화면이 설명할 수 있게
    led::faulted() ? 1 : 0,
    static_cast<int>(led::modeIndex()),
    led::modeListCsv(),
    soil::airValue(), soil::waterValue(),
    soil::ok() ? "true" : "false",
    soil::calibrating() ? "true" : "false",
    motor::diagActive() ? "true" : "false",
    static_cast<unsigned>(sht45::count()),
    sht45::viaMux() ? "true" : "false",
    sht45::modelSummary(),
    light::ok() ? "true" : "false",
    static_cast<unsigned>(light::address()),
    rs485soil::ok() ? "true" : "false",      // RS485 토양 7종 (2026-10-12)
    rs485soil::address(),                     // 응답해 고정된 주소. -1 = 아직 없음
    co2::ok() ? "true" : "false",             // CO2 HX-2000U (2026-10-13)
    otaState, consoleState);
}

// ──────────────────────────────────────────────────── 무선 콘솔 (2026-09-30, 사용자 결정 J87)
// 같은 Wi-Fi 의 터미널(PuTTY Raw · Tera Term)이 <IP>:CONSOLE_PORT 로 붙어 비밀번호(OTA 와 동일)를 넣으면
// USB 시리얼과 같은 명령을 치고 같은 로그를 봅니다. 출력 복사는 TeeOut(tee_serial.*)이, 입력은 여기서 처리합니다.
// 동시 접속 1개, 비밀번호 60초 안, 인증 뒤 무입력 5분이면 끊습니다. 모든 처리는 비차단입니다.
void handleCommand(const String& in);

void consoleClose(const char* msg) {
#if ENABLE_WIFI_CONSOLE
  if (msg) TeeSerial::rawSend(consoleClient.fd(), msg, strlen(msg));
  TeeOut.attach(-1);
  consoleClient.stop();
  consoleAuthed = false;
  consoleLen = 0;
  snprintf(consoleState, sizeof(consoleState), "ready");
#else
  (void)msg;
#endif
}

void consoleTick() {
#if ENABLE_WIFI_CONSOLE
  if (strstr(OTA_PASSWORD, "여기에") != nullptr || OTA_PASSWORD[0] == '\0') return;   // 비밀번호 없으면 켜지 않음
  const uint32_t now = millis();

  if (!consoleStarted) {
    if (!comm::wifiConnected()) return;
    consoleServer.begin();
    consoleServer.setNoDelay(true);
    consoleStarted = true;
    snprintf(consoleState, sizeof(consoleState), "ready");
    Serial.printf("[콘솔] 무선 콘솔 대기: %s:%u (PuTTY Raw, 비밀번호 = OTA 와 동일)\n",
                  WiFi.localIP().toString().c_str(), static_cast<unsigned>(CONSOLE_PORT));
    return;
  }

  // 새 접속. 이미 하나 붙어 있으면 거절.
  if (consoleServer.hasClient()) {
    WiFiClient incoming = consoleServer.accept();
    if (consoleClient && consoleClient.connected()) {
      const char busy[] = "이미 다른 접속이 있습니다\n";
      TeeSerial::rawSend(incoming.fd(), busy, sizeof(busy) - 1);
      incoming.stop();
    } else {
      consoleClient = incoming;
      consoleClient.setNoDelay(true);
      consoleAuthed = false;
      consoleLen = 0;
      consoleConnectMs = consoleLastInputMs = now;
      snprintf(consoleState, sizeof(consoleState), "connected");
      const char prompt[] = "비밀번호: ";
      TeeSerial::rawSend(consoleClient.fd(), prompt, sizeof(prompt) - 1);
      Serial.printf("[콘솔] 접속: %s\n", consoleClient.remoteIP().toString().c_str());
    }
  }

  if (!consoleClient) return;
  if (!consoleClient.connected()) { consoleClose(nullptr); Serial.println("[콘솔] 끊김"); return; }

  if (!consoleAuthed && now - consoleConnectMs > CONSOLE_AUTH_MS) {
    consoleClose("비밀번호 입력 시간 초과\n");
    Serial.println("[콘솔] 인증 시간 초과 — 끊음");
    return;
  }
  if (consoleAuthed && now - consoleLastInputMs > CONSOLE_IDLE_MS) {
    consoleClose("5분 무입력 — 접속을 끊습니다\n");
    Serial.println("[콘솔] 5분 무입력 — 끊음");
    return;
  }

  while (consoleClient.available()) {
    const int b = consoleClient.read();
    if (b < 0) break;
    const char ch = static_cast<char>(b);
    if (static_cast<uint8_t>(ch) >= 0x80 || ch == '\r' || ch == '\0') continue;   // 텔넷 협상 바이트·CR 무시
    if (ch != '\n') {
      if (consoleLen < sizeof(consoleLine) - 1) consoleLine[consoleLen++] = ch;
      else consoleLen = 0;   // 너무 길면 버림
      continue;
    }
    consoleLine[consoleLen] = '\0';
    consoleLastInputMs = now;
    if (!consoleAuthed) {
      if (strcmp(consoleLine, OTA_PASSWORD) == 0) {
        consoleAuthed = true;
        TeeOut.attach(consoleClient.fd());   // 이제부터 로그가 이쪽으로도 나감
        Serial.println("[콘솔] 인증 완료 — h: 도움말, 5분 무입력 시 끊김");
      } else {
        consoleClose("비밀번호가 틀립니다\n");
        Serial.println("[콘솔] 인증 실패 — 끊음");
        return;
      }
    } else if (consoleLen > 0) {
      handleCommand(String(consoleLine));
    }
    consoleLen = 0;
  }
#endif
}

// ──────────────────────────────────────────────────── LAN 원격 업로드 (2026-09-30, 사용자 결정 J86)
// 같은 Wi-Fi 의 아두이노 IDE 가 네트워크 포트("cube-<SN> at <IP>")로 새 판을 올립니다. 코어 내장 ArduinoOTA.
// Wi-Fi 가 붙은 뒤 한 번 begin() 하고, 루프마다 handle() 을 부릅니다. 전송은 handle() 안에서 끝까지 진행되므로
// 그동안 루프의 다른 일은 멈추고, MQTT keepalive 만 진행 콜백에서 1초마다 대신 돌립니다(접속 유지 결정).
void otaSetup() {
#if ENABLE_LAN_OTA
  if (strstr(OTA_PASSWORD, "여기에") != nullptr || OTA_PASSWORD[0] == '\0') {
    Serial.println("[OTA] secrets.h 의 OTA_PASSWORD 가 비어 있어 LAN 업로드를 켜지 않습니다");
    snprintf(otaState, sizeof(otaState), "off");
    return;
  }
  static char host[40];
  snprintf(host, sizeof(host), "%s%s", OTA_HOSTNAME_PREFIX, DEVICE_SN);
  ArduinoOTA.setHostname(host);
  ArduinoOTA.setPort(OTA_PORT);
  ArduinoOTA.setPassword(OTA_PASSWORD);
  ArduinoOTA.setRebootOnSuccess(true);

  ArduinoOTA.onStart([]() {
    otaActive = true;
    snprintf(otaState, sizeof(otaState), "uploading");
    if (pumpOn()) pumpStop("원격 업로드");   // 펌프만 정지. 팬·LED 는 그대로 (사용자 결정)
    Serial.printf("\n[OTA] 업로드 시작 (%s). 펌프 정지, 서버 제어 거부, 센서 정지\n",
                  ArduinoOTA.getCommand() == U_FLASH ? "펌웨어" : "파일시스템");
    publishStatus();
    otaLastTickMs = millis();
  });
  ArduinoOTA.onProgress([](unsigned int done, unsigned int total) {
    static uint8_t lastPct = 255;
    const uint8_t pct = total ? static_cast<uint8_t>(done * 100UL / total) : 0;
    if (pct != lastPct && pct % 10 == 0) { Serial.printf("[OTA] %u%% (%u/%u B)\n", pct, done, total); lastPct = pct; }
    // 전송 중에도 MQTT 접속을 잃지 않도록 keepalive·수신을 1초마다 돌립니다.
    if (millis() - otaLastTickMs >= 1000) { otaLastTickMs = millis(); comm::tick(); }
  });
  ArduinoOTA.onEnd([]() {
    Serial.println("[OTA] 전송 완료 — 검증 뒤 재부팅합니다");
  });
  ArduinoOTA.onError([](ota_error_t e) {
    otaActive = false;
    snprintf(otaState, sizeof(otaState), "error:%u", static_cast<unsigned>(e));
    const char* why = e == OTA_AUTH_ERROR ? "인증 실패(비밀번호)" : e == OTA_BEGIN_ERROR ? "시작 실패(공간 부족?)" :
                      e == OTA_CONNECT_ERROR ? "연결 실패" : e == OTA_RECEIVE_ERROR ? "수신 실패(Wi-Fi 끊김?)" :
                      e == OTA_END_ERROR ? "마무리 실패(이미지 검증)" : "알 수 없음";
    Serial.printf("[OTA] 실패: %s — 현재 판으로 계속 운전\n", why);
    publishStatus();
  });
  snprintf(otaState, sizeof(otaState), "ready");
  Serial.printf("[OTA] LAN 업로드 준비: 호스트 %s, 포트 %u (Wi-Fi 접속 뒤 활성)\n", host, static_cast<unsigned>(OTA_PORT));
#else
  snprintf(otaState, sizeof(otaState), "off");
#endif
}

void otaTick() {
#if ENABLE_LAN_OTA
  if (strcmp(otaState, "off") == 0) return;
  if (!otaStarted) {
    if (!comm::wifiConnected()) return;
    ArduinoOTA.begin();          // mDNS 등록 포함. IDE 포트 목록에 뜨기 시작
    otaStarted = true;
    Serial.printf("[OTA] 활성: %s.local / %s:%u\n", ArduinoOTA.getHostname().c_str(),
                  WiFi.localIP().toString().c_str(), static_cast<unsigned>(OTA_PORT));
    return;
  }
  ArduinoOTA.handle();
#endif
}

// ──────────────────────────────────────────────────── 시리얼 콘솔 (ESP32-CAM 판 명령 + 팬 명령 e)
void printHelp() {
  Serial.println();
  Serial.printf("=== ESP32 DevKitC 통합판 (Soil ADS1115 SDA%u/SCL%u / Motor GPIO%u / LED GPIO%u / Fan %u·%u·%u) ===\n",
                static_cast<unsigned>(PIN_I2C_SDA), static_cast<unsigned>(PIN_I2C_SCL),
                static_cast<unsigned>(PIN_MOTOR_PWM), static_cast<unsigned>(PIN_LED_PWM),
                static_cast<unsigned>(PIN_FAN_RELAY), static_cast<unsigned>(PIN_FAN_PWM), static_cast<unsigned>(PIN_FAN_TACH));
  Serial.println(" n : normal mode      d : diagnostic mode");
  Serial.println(" q : stop all (fan 포함)   ? : status      h : help");
  Serial.println(" [normal] m0-m100 : motor target %   (m+ / m- / m+5 / m-5)   ※ 펌프 최대 30초");
  Serial.println("          l0-l100 : LED target %     (l+ / l- / l+5 / l-5)");
  Serial.printf (" [diag]   0-100   : raw GPIO%u duty %% (+ / - / +5 / -5)   ※ 서버 제어 거부됨\n",
                 static_cast<unsigned>(PIN_MOTOR_PWM));
  Serial.println("          f       : next PWM frequency");
  Serial.println("          f0-f5   : 25kHz / 4kHz / 1kHz / 500Hz / 100Hz / 30Hz");
  Serial.println(" [fan]    e0-e100 : 배기팬 duty % (e+ / e- / e+5 / e-5). e0 = 릴레이로 12V 차단. 두 모드 공통");
  Serial.println("          ef21000 / ef25000 / ef28000 : 팬 PWM 주파수 진단 (duty 유지, 두 팬 공통)");
  Serial.println(" [fan2]   a0-a100 : 순환팬 duty % (a+ / a- / a+5 / a-5). a0 = 릴레이로 12V 차단. a : 팬 상태. af<Hz> 는 ef 와 같음");
  Serial.println(" [soil]   ca / cw : measure AIR / WATER raw (10-sample avg, NVS 저장)");
  Serial.println("          ca22520 / cw11100 : set raw directly (0-32767)");
  Serial.println("          c : show cal      cr : reset to defaults");
  Serial.println(" [net]    s : 상태+모듈 상세   t : telemetry 발행   k : status 발행");
  Serial.println("          i : 네트워크 상태    r : Wi-Fi·MQTT 다시 접속");
  Serial.println(" [i2c]    x : 주소 스캔 (유휴 레벨 + 버스 + 멀티플렉서 채널별 0x08~0x77)");
  Serial.println("          xr : 버스 복구(SCL 9번 토글로 물린 슬레이브 풀기) 뒤 스캔");
  Serial.println(" [pin]    g16 : 그 GPIO 를 3초 HIGH(3.3V) 로 — 배선/PWM 구분용 (2 4 5 13 14 16 17 18 19 23 25 26 32 33)");
  Serial.printf (" [rs485]  b : 토양 7종(RS485, TX%u/RX%u) 주소·값·마지막 프레임   b2 : 주소 지정(0~247)   bs : 주소 다시 탐색   br : 즉시 읽기\n",
                 static_cast<unsigned>(PIN_RS485_TX), static_cast<unsigned>(PIN_RS485_RX));
  Serial.printf (" [co2]    co : CO2 센서(HX-2000U, TX%u/RX%u) 통계·값·Cal·마지막 프레임   cor : 즉시 읽기   ※ 첫 응답 뒤 15분은 예열(텔레메트리 null)\n",
                 static_cast<unsigned>(PIN_CO2_TX), static_cast<unsigned>(PIN_CO2_RX));
#if ENABLE_LAN_OTA
  Serial.println(" [ota]    LAN 업로드 켜짐: 같은 Wi-Fi 의 IDE 포트 목록에서 cube-<SN> 선택 → 업로드 (비밀번호 = secrets.h OTA_PASSWORD)");
#endif
#if ENABLE_WIFI_CONSOLE
  Serial.printf(" [con]    무선 콘솔 켜짐: PuTTY(Raw) 로 <보드 IP>:%u 접속 → 같은 비밀번호 → 이 명령들 그대로\n", static_cast<unsigned>(CONSOLE_PORT));
#endif
  Serial.println(" status: Motor out/target%   LED out/target%   | Fan duty% (rpm) | Soil raw (V, %)");
  Serial.printf (" ※ 부팅 직후 배기팬 %u%%. 리스 만료로 끄지 않음(LEASE_ENFORCED=false). 펌프는 최대 30초 뒤 자동 정지만.\n",
                 static_cast<unsigned>(SAFE_FAN_PERCENT));
  Serial.println();
}

void printOneFan(const char* label, uint8_t id) {
  if (fan::faulted(id)) { Serial.printf("  | %s FAULT", label); return; }
  Serial.printf("  | %s %u%%", label, static_cast<unsigned>(fan::percent(id)));
  if (!fan::relayOn(id))        Serial.print(" (OFF)");
  else if (fan::rpm(id) >= 0)   Serial.printf(" (%d rpm)", fan::rpm(id));
  else if (fan::stalled(id))    Serial.print(" (스톨 의심)");
  else                          Serial.print(" (rpm --)");
}

void printFanPart() {
#if ENABLE_FAN
  printOneFan("Fan", fan::FAN_EXHAUST);
#else
  Serial.printf("  | Fan(가상) %u%%", static_cast<unsigned>(virtFan1));
#endif
#if ENABLE_FAN2
  printOneFan("Fan2", fan::FAN_CIRC);   // 순환팬 (2026-10-13)
#endif
}

void printSoilPart() {
  Serial.print("  | Soil raw: ");
  if (!soil::ok()) { Serial.print("-- (ADS1115 응답 없음)"); return; }
  if (soil::raw() < 0) { Serial.print("--"); return; }     // 부팅 직후 첫 묶음이 아직 안 모임
  const int mv = soil::millivolts();
  Serial.printf("%d (%d.%03dV, ", soil::raw(), mv / 1000, mv % 1000);
  if (soil::moisture() < 0) Serial.print("미보정");
  else                      Serial.printf("%d%%", soil::moisture());
  Serial.print(soil::calibrating() ? ") [보정 측정 중]" : ")");
}

// 온습도(평균, 응답 개수) · 조도 (2026-09-29 추가)
void printEnvLine() {
  if (sht45::anyOk()) Serial.printf("  | T/RH %.1f°C %.1f%% (%u)", sht45::avgTemperature(), sht45::avgHumidity(), static_cast<unsigned>(sht45::count()));
  else                Serial.print("  | T/RH --");
  if (light::ok()) Serial.printf("  | Lux %.0f", light::lux());
  else             Serial.print("  | Lux --");
  // RS485 토양 7종 (2026-10-12). 자세한 값(염분·N·P·K)은 s / b 로.
#if ENABLE_RS485_SOIL
  if (rs485soil::ok()) Serial.printf("  | Soil485 %.1f°C %.1f%% EC%d pH%.1f",
                                     static_cast<double>(rs485soil::temperature()), static_cast<double>(rs485soil::vwc()),
                                     rs485soil::ec(), static_cast<double>(rs485soil::ph()));
  else                 Serial.printf("  | Soil485 --%s", rs485soil::scanning() ? "(주소 탐색)" : "");
#endif
  // CO2 HX-2000U (2026-10-13). 예열 중에는 값과 남은 분을 같이 보이되 텔레메트리는 null.
#if ENABLE_CO2
  if (!co2::ok())            Serial.print("  | CO2 --");
  else if (co2::warmingUp()) Serial.printf("  | CO2 %dppm(예열 %lu분)", co2::rawPpm(),
                                           static_cast<unsigned long>((co2::warmupRemainingMs() + 59999UL) / 60000UL));
  else                       Serial.printf("  | CO2 %dppm", co2::ppm());
#endif
  Serial.println();
}

void printStatus() {
  if (motor::diagActive()) {
    Serial.printf("[DIAG] PWM %s (f%u)  duty %u%%  raw=%lu/%lu",
                  motor::diagModeLabel(), static_cast<unsigned>(motor::diagModeIndex()),
                  static_cast<unsigned>(motor::diagDuty()),
                  static_cast<unsigned long>(motor::rawDuty()), static_cast<unsigned long>(motor::rawMax()));
  } else {
    Serial.printf("[NORM] Motor %u/%u%% (raw %lu)   LED %u/%u%% (raw %lu)",
                  static_cast<unsigned>(motor::output()), static_cast<unsigned>(motor::target()),
                  static_cast<unsigned long>(motor::rawDuty()),
                  static_cast<unsigned>(led::output()), static_cast<unsigned>(led::percent()),
                  static_cast<unsigned long>(led::rawDuty()));
  }
  printFanPart();
  printSoilPart();
  printEnvLine();
}

void enterNormalMode() {
  led::stopNow();
  if (motor::diagActive()) motor::diagExit();
  else                     motor::stopNow();
  Serial.println(">> NORMAL mode (motor & LED stopped, 25 kHz). 팬은 그대로.");
}

void enterDiagMode() {
  led::stopNow();
  if (!motor::diagEnter()) { Serial.println("진단 모드 진입 실패 (모터 fault)"); return; }
  Serial.println(">> DIAG mode (off, 25 kHz). LED unused. 팬은 그대로. 서버 제어는 outputs_disabled 로 거부됩니다.");
}

// 문자열이 0~100 범위의 정수인지 검사해서 out 에 담습니다 (우노 parsePercent)
bool parsePercent(const String& s, int& out) {
  if (s.length() == 0 || s.length() > 3) return false;
  for (unsigned int i = 0; i < s.length(); i++) {
    if (!isDigit(s.charAt(i))) return false;
  }
  long v = s.toInt();
  if (v < 0 || v > 100) return false;
  out = static_cast<int>(v);
  return true;
}

// "50" → 50,  "+" → current+1,  "-5" → current-5   (결과는 0~100 으로 제한) (우노 parseValueArg)
bool parseValueArg(String arg, int current, int& out) {
  arg.trim();
  if (arg.length() == 0) return false;

  char c = arg.charAt(0);
  if (c == '+' || c == '-') {
    int step = 1;
    if (arg.length() > 1) {
      if (!parsePercent(arg.substring(1), step) || step == 0) return false;
    }
    out = constrain(current + ((c == '+') ? step : -step), 0, 100);
    return true;
  }
  return parsePercent(arg, out);
}

// "22520" 같은 0~32767 원시값 문자열 검사 (우노 parseRaw. 눈금만 다름)
bool parseRaw(String s, int& out) {
  s.trim();
  if (s.length() == 0 || s.length() > 5) return false;
  for (unsigned int i = 0; i < s.length(); i++) {
    if (!isDigit(s.charAt(i))) return false;
  }
  long v = s.toInt();
  if (v < 0 || v > SOIL_RAW_MAX) return false;
  out = static_cast<int>(v);
  return true;
}

// c / cr / ca / ca<raw> / cw / cw<raw> (우노 handleSoilCalCommand)
void handleSoilCalCommand(const String& in) {
  if (in.length() == 1) { soil::printCal(); return; }

  char sub = in.charAt(1);
  if (sub == 'r' || sub == 'R') {
    if (in.length() != 2) { Serial.println("Invalid. Use cr"); return; }
    soil::resetCalibration();
    return;
  }

  bool isAir;
  if      (sub == 'a' || sub == 'A') isAir = true;
  else if (sub == 'w' || sub == 'W') isAir = false;
  else { Serial.println("Invalid. Use c, cr, ca, ca<raw>, cw, cw<raw>"); return; }

  // ca / cw : 새 묶음 10개를 측정해서 저장 (약 0.1초 뒤 결과 출력)
  if (in.length() == 2) {
    soil::startCalibration(isAir);
    return;
  }

  // ca22520 / cw11100 : 원시값 직접 지정
  int raw;
  if (!parseRaw(in.substring(2), raw)) {
    Serial.printf("Invalid raw value. Use 0-%d (e.g. ca22520, cw11100)\n", SOIL_RAW_MAX);
    return;
  }
  if (isAir) soil::setAir(raw);
  else       soil::setWater(raw);
}

// I2C 버스 복구 (2026-09-29 추가). 유휴 상태에서 SDA 가 LOW 로 잡혀 있을 때 씁니다.
//   마스터(ESP32)가 읽기 도중 리셋되면 슬레이브는 아직 0 비트를 내보내는 중이라 SDA 를 문 채 멈춥니다.
//   표준 절차: SDA 를 풀어 둔 채 SCL 을 최대 9번 토글해 남은 비트를 밀어내고, STOP 조건을 만든 뒤 버스를 다시 엽니다.
//   SDA 가 그래도 LOW 면 물린 슬레이브가 아니라 배선(단락·오배선)이나 전원(무전원 장치의 보호 다이오드)입니다.
//   부팅 때 자동으로 한 번, 시리얼 xr 로 언제든.
bool i2cRecover(bool verbose) {
  Wire.end();
  pinMode(PIN_I2C_SDA, INPUT_PULLUP);
  pinMode(PIN_I2C_SCL, OUTPUT_OPEN_DRAIN);
  digitalWrite(PIN_I2C_SCL, HIGH);
  delayMicroseconds(10);
  uint8_t pulses = 0;
  while (digitalRead(PIN_I2C_SDA) == LOW && pulses < 9) {
    digitalWrite(PIN_I2C_SCL, LOW);
    delayMicroseconds(10);
    digitalWrite(PIN_I2C_SCL, HIGH);
    delayMicroseconds(10);
    pulses++;
  }
  const bool freed = digitalRead(PIN_I2C_SDA) == HIGH;
  if (freed) {   // STOP: SCL HIGH 인 채 SDA LOW → HIGH
    pinMode(PIN_I2C_SDA, OUTPUT_OPEN_DRAIN);
    digitalWrite(PIN_I2C_SDA, LOW);
    delayMicroseconds(10);
    digitalWrite(PIN_I2C_SCL, HIGH);
    delayMicroseconds(10);
    digitalWrite(PIN_I2C_SDA, HIGH);
    delayMicroseconds(10);
  }
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, I2C_HZ);
  Wire.setTimeOut(I2C_TIMEOUT_MS);
  if (verbose) {
    if (freed) Serial.printf("[I2C] 버스 복구: SCL %u번 토글 뒤 SDA HIGH — 물려 있던 슬레이브를 풀었습니다\n", static_cast<unsigned>(pulses));
    else       Serial.println("[I2C] 버스 복구 실패: SCL 9번 토글 뒤에도 SDA LOW — 슬레이브가 문 것이 아니라 배선(단락·오배선)이나 센서 전원 문제입니다");
  }
  return freed;
}

// x — I2C 주소 스캔 (2026-09-29 추가, 배선 진단용). 버스에 직접 붙은 장치와, 멀티플렉서가 있으면
//     config.h 의 SHT45_MUX_CHANNELS 채널마다 한 번씩 0x08~0x77 을 두드려 응답하는 주소를 나열합니다.
//     ★ 채널 스캔 결과에는 버스 직결 장치(ADS1115·BH1750·멀티플렉서 자신)도 같이 보입니다 — 채널 뒤에만
//       있는 것은 "버스" 줄에는 없고 그 채널 줄에만 있는 주소입니다.
//     버스가 물려 있으면(어느 장치가 SDA 를 LOW 로 잡고 있음) 주소마다 I2C 시간 초과(25ms)가 걸려
//     한 줄에 3초쯤 걸리므로, 먼저 유휴 레벨을 보여 줍니다. SDA·SCL 둘 다 HIGH 여야 정상입니다.
const char* i2cKnownName(uint8_t a) {
  switch (a) {
    case 0x23: case 0x5C: return " BH1750";
    case 0x44:            return " SHT45";
    case 0x48: case 0x49: case 0x4A: case 0x4B: return " ADS1115";
    case 0x70: case 0x71: case 0x72: case 0x73:
    case 0x74: case 0x75: case 0x76: case 0x77: return " PCA9548A";
    default: return "";
  }
}

uint8_t i2cScanOnce(const char* label) {
  uint8_t found = 0;
  Serial.printf("  %-8s:", label);
  for (uint8_t a = 0x08; a <= 0x77; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      Serial.printf(" 0x%02X%s", static_cast<unsigned>(a), i2cKnownName(a));
      found++;
    }
    yield();
  }
  Serial.println(found ? "" : " (응답 없음)");
  return found;
}

void i2cScan() {
  Serial.printf("--- I2C 스캔 (SDA GPIO%u / SCL GPIO%u, %lu kHz) ---\n",
                static_cast<unsigned>(PIN_I2C_SDA), static_cast<unsigned>(PIN_I2C_SCL),
                static_cast<unsigned long>(I2C_HZ / 1000));
  const int sda = digitalRead(PIN_I2C_SDA);
  const int scl = digitalRead(PIN_I2C_SCL);
  Serial.printf("  유휴 레벨: SDA=%s SCL=%s %s\n", sda ? "HIGH" : "LOW", scl ? "HIGH" : "LOW",
                (sda && scl) ? "(정상)" : "(★ LOW 면 풀업 없음·배선 오류·장치가 버스를 잡고 있음)");

  // 외부 풀업 검사 (2026-09-30 추가): 버스를 잠깐 닫고 보드 내부 풀다운(약 45kΩ)만 켠 채 읽는다.
  // 센서 모듈이 전원을 받고 SDA/SCL 이 이어져 있으면 모듈 풀업(4.7k~10kΩ)이 이겨 HIGH,
  // 선이 끊겼거나 모듈 전원이 없으면 LOW. 보드 자체 풀업만으로 HIGH 였던 경우를 걸러낸다.
  Wire.end();
  pinMode(PIN_I2C_SDA, INPUT_PULLDOWN);
  pinMode(PIN_I2C_SCL, INPUT_PULLDOWN);
  delay(2);
  const int extSda = digitalRead(PIN_I2C_SDA);
  const int extScl = digitalRead(PIN_I2C_SCL);
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, I2C_HZ);
  Wire.setTimeOut(I2C_TIMEOUT_MS);
  Serial.printf("  외부 풀업: SDA=%s SCL=%s", extSda ? "있음" : "없음", extScl ? "있음" : "없음");
  if (extSda && extScl)       Serial.println("  → 센서 모듈에 전원이 있고 두 선이 이어져 있음. 응답이 없다면 센서 GND 선 또는 SDA/SCL 뒤바뀜");
  else if (!extSda && !extScl) Serial.println("  → 센서 쪽 풀업이 안 보임. 3V3 선이 빠졌거나 SDA·SCL 이 보드에서 끊김");
  else                         Serial.println("  → 한 선만 이어짐. 없음 쪽 선(보드 21/22 자리 또는 케이블)이 빠짐");
  i2cScanOnce("버스");
  if (mux::present()) {
    for (uint8_t i = 0; i < SHT45_MAX; i++) {
      const uint8_t ch = SHT45_MUX_CHANNELS[i];
      if (!mux::select(ch)) { Serial.printf("  채널 %u 선택 실패\n", static_cast<unsigned>(ch)); break; }
      char label[12];
      snprintf(label, sizeof(label), "채널 %u", static_cast<unsigned>(ch));
      i2cScanOnce(label);
    }
    mux::deselectAll();
  } else {
    Serial.printf("  (PCA9548A 0x%02X 없음 — 채널 스캔 생략)\n", static_cast<unsigned>(I2C_MUX_ADDR));
  }
  Serial.println("  기대값: 버스 0x48 ADS1115 · 0x23/0x5C BH1750 · 0x70 PCA9548A, 채널마다 0x44 SHT45 (직결이면 버스에 0x44)");
  if (!sda) Serial.println("  → xr 로 버스 복구를 시도해 보세요. 그래도 LOW 면 SDA 선을 하나씩 빼 가며 x 로 어느 장치가 잡는지 찾습니다.");
}

// g<pin> — 핀 시험 (2026-09-29 추가). 그 GPIO 를 PWM 과 무관한 일반 출력으로 3초 HIGH(3.3V) 했다가 LOW 로 내리고,
//          모터·LED·팬 핀이었으면 그 모듈을 다시 초기화합니다. LED 바가 안 켜질 때 "핀→게이트 배선" 과 "PWM 출력" 을 가릅니다:
//          g16 에 LED 가 3초 켜지면 배선은 맞고 PWM 쪽 문제, 안 켜지면 GPIO16 → 게이트 선(또는 핀 자리) 문제.
//          허용 핀: 2 4 5 13 14 16 17 18 19 23 25 26 32 33 (I2C 21/22 · 플래시 6~11 · 입력 전용 34~39(TACH 34 포함) · 부팅 0/12/15 제외.
//          27 은 10-06 TACH 를 34 로 옮기며 비었지만 목록에는 아직 넣지 않음)
void pinTest(const String& in) {
  const int pin = in.substring(1).toInt();
  static const uint8_t allowed[] = { 2, 4, 5, 13, 14, 16, 17, 18, 19, 23, 25, 26, 32, 33 };
  bool okPin = false;
  for (uint8_t a : allowed) if (pin == a) okPin = true;
  if (!okPin) { Serial.println("Invalid. g<pin>: 2 4 5 13 14 16 17 18 19 23 25 26 32 33 만"); return; }
  const uint8_t p = static_cast<uint8_t>(pin);
  const bool isLed = (p == PIN_LED_PWM), isMotor = (p == PIN_MOTOR_PWM),
             isFan = (p == PIN_FAN_PWM || p == PIN_FAN_RELAY || p == PIN_FAN2_PWM || p == PIN_FAN2_RELAY);   // 순환팬 핀 포함 (2026-10-13)
  if (isMotor && pumpOn()) pumpStop("핀 시험");
  ledcDetach(p);                       // PWM 에 붙어 있었으면 뗀다 (아니면 무시됨)
  pinMode(p, OUTPUT);
  digitalWrite(p, HIGH);
  Serial.printf("[PIN] GPIO%u = HIGH(3.3V) 3초 …\n", static_cast<unsigned>(p));
  const uint32_t until = millis() + 3000;
  while (static_cast<int32_t>(millis() - until) < 0) { comm::tick(); delay(10); }
  digitalWrite(p, LOW);
  Serial.printf("[PIN] GPIO%u = LOW. ", static_cast<unsigned>(p));
  if (isLed)        { led::begin();   Serial.println("LED 모듈 다시 초기화(0%)"); }
  else if (isMotor) { motor::begin(); Serial.println("모터 모듈 다시 초기화(0%)"); }
  else if (isFan)   { fan::begin(); fan::setPercent(fan1Percent()); Serial.println("팬 모듈 다시 초기화"); }
  else if (p == PIN_RS485_TX) { rs485soil::begin(); Serial.println("RS485 UART2 다시 초기화"); }   // 2026-10-12
  else if (p == PIN_CO2_TX)   { co2::begin();       Serial.println("CO2 UART1 다시 초기화"); }     // 2026-10-13
  else              { Serial.println("끝"); }
}

// e / e<value> / e+ / e-5 / ef<Hz>  — 배기팬 (두 모드 공통, 벤치용. 리스와 무관)
// id 0 = 배기팬(e…), id 1 = 순환팬(a…) — 2026-10-13 두 팬 공용으로 바꿈. 로직은 그대로.
void handleFanCommand(const String& in, uint8_t id) {
  const char* label = (id == fan::FAN_CIRC) ? "FAN2" : "FAN";
  const char  key   = (id == fan::FAN_CIRC) ? 'a' : 'e';
  if (!fan::enabled(id)) {
    Serial.printf("[%s] ENABLE_FAN%s=0 — 이 팬 모듈이 꺼져 있습니다 (가상값)\n", label, (id == fan::FAN_CIRC) ? "2" : "");
    return;
  }
  if (in.length() == 1) { fan::reportSerial(); return; }

  const char sub = in.charAt(1);
  if (sub == 'f' || sub == 'F') {
    String arg = in.substring(2);
    arg.trim();
    long hz = arg.toInt();
    bool allowed = false;
    for (uint8_t i = 0; i < FAN_DIAG_FREQ_COUNT; i++) {
      if (hz == static_cast<long>(FAN_DIAG_FREQS[i])) { allowed = true; break; }
    }
    if (!allowed) { Serial.printf("Invalid. Fan freq: %cf21000 / %cf25000 / %cf28000\n", key, key, key); return; }
    fan::setFrequency(static_cast<uint32_t>(hz));   // 타이머 공유 → 두 팬 모두에 적용
    return;
  }

  int v;
  if (!parseValueArg(in.substring(1), fan::percent(id), v)) {
    Serial.printf("Invalid. Fan: %c0-%c100, %c+, %c-, %c+5, %c-5, %cf<Hz>\n", key, key, key, key, key, key, key);
    return;
  }
  if (!fan::setPercent(static_cast<uint8_t>(v), id)) Serial.printf("[%s] fault — 명령 무시\n", label);
}

// b / b<addr> / bs / br — RS485 토양 7종 센서 (2026-10-12). 두 모드 공통, 벤치용.
void handleRs485Command(const String& in) {
  if (in.length() == 1) { rs485soil::reportSerial(); return; }
  const char sub = in.charAt(1);
  if (in.length() == 2 && (sub == 's' || sub == 'S')) { rs485soil::rescan();  return; }
  if (in.length() == 2 && (sub == 'r' || sub == 'R')) { rs485soil::readNow(); Serial.println("[RS485] 즉시 읽기 요청"); return; }
  String arg = in.substring(1);
  arg.trim();
  bool digits = arg.length() >= 1 && arg.length() <= 3;
  for (unsigned i = 0; digits && i < arg.length(); i++) if (!isDigit(arg.charAt(i))) digits = false;
  if (!digits) {
    Serial.println("Invalid. b : 상태   b<0-247> : 주소 지정   bs : 다시 탐색   br : 즉시 읽기");
    return;
  }
  if (!rs485soil::setAddress(arg.toInt())) Serial.println("Invalid. 주소는 0~247");
}

// co / cor — CO2 센서 HX-2000U (2026-10-13, 사용자 결정: 명령 글자 "co"). 두 모드 공통, 벤치용.
// 토양 보정 c… 와 첫 글자가 같으므로 handleCommand 에서 두 번째 글자 o 를 먼저 가립니다.
void handleCo2Command(const String& in) {
  if (in.length() == 2) { co2::reportSerial(); return; }
  const char sub = in.charAt(2);
  if (in.length() == 3 && (sub == 'r' || sub == 'R')) { co2::readNow(); Serial.println("[CO2] 즉시 읽기 요청"); return; }
  Serial.println("Invalid. co : 상태·값·마지막 프레임   cor : 즉시 읽기");
}

void handleCommand(const String& in) {
  char c = in.charAt(0);

  // ---- 공통: CO2 센서 (co, cor) — 2026-10-13. 토양 보정 c… 보다 먼저 가림 ----
  if ((c == 'c' || c == 'C') && in.length() >= 2 && (in.charAt(1) == 'o' || in.charAt(1) == 'O')) { handleCo2Command(in); return; }

  // ---- 공통: 토양 센서 보정 (c, cr, ca, ca<raw>, cw, cw<raw>) ----
  if (c == 'c' || c == 'C') { handleSoilCalCommand(in); return; }

  // ---- 공통: 배기팬 (e, e<value>, e+, e-, ef<Hz>) ----
  if (c == 'e' || c == 'E') { handleFanCommand(in, fan::FAN_EXHAUST); return; }

  // ---- 공통: 순환팬 (a, a<value>, a+, a-, af<Hz>) — 2026-10-13 ----
  if (c == 'a' || c == 'A') { handleFanCommand(in, fan::FAN_CIRC); return; }

  // ---- 공통: RS485 토양 7종 센서 (b, b<addr>, bs, br) — 2026-10-12 ----
  if (c == 'b' || c == 'B') { handleRs485Command(in); return; }

  // ---- 공통: 핀 시험 (g<pin>) ----
  if ((c == 'g' || c == 'G') && in.length() >= 2) { pinTest(in); return; }

  // ---- 공통: I2C 버스 복구 + 스캔 (xr) ----
  if ((c == 'x' || c == 'X') && in.length() == 2 && (in.charAt(1) == 'r' || in.charAt(1) == 'R')) {
    i2cRecover(true);
    i2cScan();
    return;
  }

  // ---- 공통 한 글자 명령 ----
  if (in.length() == 1) {
    if (c == 'n' || c == 'N') { enterNormalMode(); return; }
    if (c == 'd' || c == 'D') { enterDiagMode();   return; }
    if (c == '?')             { printStatus();     return; }
    if (c == 'h' || c == 'H') { printHelp();       return; }
    if (c == 'q' || c == 'Q') {
      led::stopNow();
      motor::stopNow();                 // 램프 없이 즉시 정지 (진단 모드면 raw duty 0)
#if ENABLE_FAN
      fan::setPercent(0);               // 릴레이로 12V 차단
#endif
#if ENABLE_FAN2
      fan::setPercent(0, fan::FAN_CIRC);   // 순환팬도 차단 (2026-10-13)
#endif
      Serial.println("STOP ALL");
      printStatus();
      return;
    }
    if (c == 's' || c == 'S') {
      printStatus();
#if ENABLE_FAN
      fan::reportSerial();
#endif
      motor::reportSerial();
      led::reportSerial();
      soil::reportSerial();
      mux::reportSerial();
      sht45::reportSerial();
      light::reportSerial();
      rs485soil::reportSerial();
      co2::reportSerial();
      Serial.printf("[MQTT] %s | 리스 %s | 펌프 %s%s | 발행 %lu\n",
                    comm::connected() ? "연결됨" : (comm::networkEnabled() ? "미연결" : "네트워크 꺼짐"),
                    leaseActive() ? "유효" : "없음",
                    pumpOn() ? "가동" : "정지", pumpLocked() ? "(쿨다운)" : "",
                    static_cast<unsigned long>(comm::publishedCount()));
      return;
    }
    if (c == 't' || c == 'T') { publishTelemetry(); Serial.println("telemetry 발행 시도"); return; }
    if (c == 'k' || c == 'K') { publishStatus();    Serial.println("status 발행 시도");    return; }
    if (c == 'i' || c == 'I') {
      comm::printNetInfo();
      Serial.printf("  LAN 업로드: %s%s\n", otaState,
                    (ENABLE_LAN_OTA && otaStarted) ? " (IDE 포트 목록에 cube-<SN> 으로 표시)" : "");
      Serial.printf("  무선 콘솔 : %s (포트 %u)\n", consoleState, static_cast<unsigned>(CONSOLE_PORT));
      Serial.printf("  칩 온도   : %.1f °C (보정 없는 내부 센서, 추이 참고용)\n", static_cast<double>(chipTemperatureC()));
      return;
    }
    if (c == 'r' || c == 'R') { comm::reconnect();    return; }
    if (c == 'x' || c == 'X') { i2cScan();            return; }
  }

  // ---- 일반 모드: m… = 모터, l… = LED ----
  if (!motor::diagActive()) {
    int v;
    if (c == 'm' || c == 'M') {
      if (!parseValueArg(in.substring(1), motor::target(), v)) {
        Serial.println("Invalid. Motor: m0-m100, m+, m-, m+5, m-5");
        return;
      }
      // 벤치에서 펌프를 직접 돌리는 경로. 최대 가동 시간은 loop() 에서 똑같이 적용됩니다.
      if (v > 0) {
        if (!motor::setPercent(static_cast<uint8_t>(v))) Serial.println("[MOTOR] fault — 명령 무시");
      } else {
        pumpStop("시리얼");
      }
      return;
    }
    if (c == 'l' || c == 'L') {
      if (!parseValueArg(in.substring(1), led::percent(), v)) {
        Serial.println("Invalid. LED: l0-l100, l+, l-, l+5, l-5");
        return;
      }
      if (!led::setPercent(static_cast<uint8_t>(v))) Serial.println("[LED] fault — 명령 무시");
      return;
    }
    Serial.println("Normal mode: m<value> for motor, l<value> for LED, e<value> for fan (h: help)");
    return;
  }

  // ---- 진단 모드: 주파수 변경 ----
  if (c == 'f' || c == 'F') {
    if (in.length() == 1) {
      motor::diagNextMode();
    } else {
      int idx;
      if (!parsePercent(in.substring(1), idx) || idx >= static_cast<int>(DIAG_PWM_MODE_COUNT)) {
        Serial.printf("Invalid freq index. Use f0..f%u\n", static_cast<unsigned>(DIAG_PWM_MODE_COUNT - 1));
        return;
      }
      motor::diagSetModeIndex(static_cast<uint8_t>(idx));
    }
    printStatus();
    return;
  }

  // ---- 진단 모드: duty 설정 ----
  if (c == 'm' || c == 'M' || c == 'l' || c == 'L') {
    Serial.println("Diag mode uses bare values (0-100, +, -). Enter n for normal mode.");
    return;
  }
  int v;
  if (parseValueArg(in, motor::diagDuty(), v)) {
    motor::diagSetDuty(static_cast<uint8_t>(v));   // 펌프 가동 시각은 loop() 가 잡습니다
    printStatus();
    return;
  }

  Serial.println("Unknown command. Type h for help.");
}

void processSerialLine() {
  serialLine.trim();
  if (serialOverflow) {
    Serial.println("입력이 너무 깁니다. 기존 설정 유지.");
  } else if (serialLine.length() > 0) {
    handleCommand(serialLine);
  }
  serialLine = "";
  serialOverflow = false;
}

void readSerialInput() {
  for (uint8_t i = 0; i < 64 && Serial.available() > 0; ++i) {
    char ch = static_cast<char>(Serial.read());
    if (ch == '\n' || ch == '\r') {
      processSerialLine();
    } else if (serialLine.length() < 24 && !serialOverflow) {
      serialLine += ch;
    } else {
      serialOverflow = true;
    }
  }
}

// ──────────────────────────────────────────────────── setup / loop

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(1000);
  serialLine.reserve(24);

  Serial.println("\n=== 헬피아이 큐브 노드 (ESP32 DevKitC WROOM-32D 통합판) ===");
  Serial.printf("FW %s | 앱 %s | SN %s\n", FW_VERSION, DEVICE_NAME, DEVICE_SN);
  Serial.printf("모터 GPIO%u · LED GPIO%u · 토양 ADS1115(SDA%u/SCL%u)\n",
                static_cast<unsigned>(PIN_MOTOR_PWM), static_cast<unsigned>(PIN_LED_PWM),
                static_cast<unsigned>(PIN_I2C_SDA), static_cast<unsigned>(PIN_I2C_SCL));
#if ENABLE_FAN
  Serial.printf("배기팬(fan1): 릴레이=GPIO%u PWM=GPIO%u TACH=GPIO%u @ %luHz | 순환팬(fan2): %s\n",
                static_cast<unsigned>(PIN_FAN_RELAY), static_cast<unsigned>(PIN_FAN_PWM),
                static_cast<unsigned>(PIN_FAN_TACH), static_cast<unsigned long>(FAN_PWM_HZ),
                ENABLE_FAN2 ? "활성" : "핀 미배정 (가상값)");
#if ENABLE_FAN2
  Serial.printf("순환팬(fan2): 릴레이=GPIO%u PWM=GPIO%u TACH=GPIO%u (입력 전용, 외부 10kΩ 풀업 3.3V) — 2026-10-13\n",
                static_cast<unsigned>(PIN_FAN2_RELAY), static_cast<unsigned>(PIN_FAN2_PWM), static_cast<unsigned>(PIN_FAN2_TACH));
#endif
#else
  Serial.println("팬: 없음 (fan1·fan2 가상값)");
#endif
  Serial.printf("리스 %lu ms | 펌프 최대 %lu ms · 쿨다운 %lu ms\n",
                static_cast<unsigned long>(LEASE_MS), static_cast<unsigned long>(PUMP_MAX_MS),
                static_cast<unsigned long>(PUMP_COOLDOWN_MS));

  // I2C 버스는 여기서 한 번만. 네 장치(ADS1115 · PCA9548A · SHT45 · BH1750)가 공유합니다.
  // 버스를 열기 전에 SDA 가 LOW 로 잡혀 있으면(리셋 중 물린 슬레이브) 먼저 풀어 봅니다.
  pinMode(PIN_I2C_SDA, INPUT_PULLUP);
  pinMode(PIN_I2C_SCL, INPUT_PULLUP);
  delayMicroseconds(50);
  if (digitalRead(PIN_I2C_SDA) == LOW) {
    Serial.println("[I2C] 부팅 시 SDA 가 LOW — 버스 복구 시도");
    i2cRecover(true);
  } else {
    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, I2C_HZ);
    Wire.setTimeOut(I2C_TIMEOUT_MS);
  }
  Serial.printf("I2C SDA%u/SCL%u %lukHz: ADS1115 0x%02X · PCA9548A 0x%02X → SHT45 0x%02X ×%u(ch %u·%u·%u) · BH1750 0x%02X/0x%02X\n",
                static_cast<unsigned>(PIN_I2C_SDA), static_cast<unsigned>(PIN_I2C_SCL), static_cast<unsigned long>(I2C_HZ / 1000),
                static_cast<unsigned>(ADS1115_ADDR), static_cast<unsigned>(I2C_MUX_ADDR), static_cast<unsigned>(SHT45_ADDR),
                static_cast<unsigned>(SHT45_MAX), static_cast<unsigned>(SHT45_MUX_CHANNELS[0]), static_cast<unsigned>(SHT45_MUX_CHANNELS[1]),
                static_cast<unsigned>(SHT45_MUX_CHANNELS[2]), static_cast<unsigned>(BH1750_ADDRS[0]), static_cast<unsigned>(BH1750_ADDRS[1]));

  fan::begin();
  motor::begin();
  led::begin();
  mux::begin();      // 멀티플렉서 먼저 (SHT45 가 경유 여부를 물음)
  soil::begin();
  sht45::begin();
  light::begin();
  rs485soil::begin();   // UART2 TX17/RX27, 주소 2 → 1 → 0 자동 탐색 (2026-10-12)
  co2::begin();         // UART1 TX32/RX33, 5초마다 요청-응답, 첫 응답 뒤 15분 예열 (2026-10-13)
  outputsSafe("부팅");

  comm::Callbacks callbacks = { applyControl, onConfig, publishTelemetry, publishStatus };
  comm::begin(callbacks);
  otaSetup();        // LAN 원격 업로드 (ENABLE_LAN_OTA, 2026-09-30)
  printHelp();
  printStatus();
}

void loop() {
  comm::tick();      // 재접속·keepalive·수신. 자주 불러야 합니다.
  otaTick();         // LAN 원격 업로드 대기·처리 (전송 중에는 여기서 끝까지 진행됨)
  consoleTick();     // 무선 콘솔 접속·인증·명령 (비차단)
  fan::tick();       // TACH 안정화·1초 RPM 창·스톨 판정
  motor::tick();     // 모터 램프
  led::tick();       // LED 램프
  soil::tick();      // ADS1115 샘플링 (10ms 간격, 10회 평균, 논블로킹)
  mux::tick();       // PCA9548A 재탐색 (없을 때만)
  sht45::tick();     // 온습도: 2초마다 한 바퀴, 센서마다 명령 → 10ms 뒤 읽기
  light::tick();     // 조도: 1초마다 2바이트 읽기
  rs485soil::tick(); // RS485 토양 7종: 5초마다 Modbus 요청 → 응답 대기(0.5초 시간 초과), 논블로킹 (2026-10-12)
  co2::tick();       // CO2 HX-2000U: 5초마다 요청 7바이트 → 응답 12바이트 대기(0.5초 시간 초과), 논블로킹 (2026-10-13)
  if (soil::pollCalibrationDone()) {   // 측정 보정(ca/cw/cal_air/cal_water)이 끝남 → 서버에 결과
    publishStatus();
    publishTelemetry();
  }
  readSerialInput();

  const uint32_t now = millis();

  // 펌프 가동 시각 정리. 서버·시리얼·진단 어느 경로로 켜졌든 같은 규칙을 받습니다.
  if (pumpOn()) {
    if (pumpStartedMs == 0) { pumpStartedMs = now; pumpEverRan = true; }
  } else if (pumpStartedMs != 0) {
    pumpStartedMs = 0;
    lastPumpStopMs = now;
  }

  // 펌프 최대 가동 시간 (서버 가드와 독립. 브로커에 직접 명령을 넣는 경로가 있으므로 여기서도 막습니다)
  if (pumpStartedMs != 0 && now - pumpStartedMs >= PUMP_MAX_MS) {
    pumpStop("최대 가동 시간");
  }

  // 제어 리스 만료 → 기본은 로그만 (LEASE_ENFORCED=false)
  if (leaseUntil != 0 && !leaseActive()) {
    leaseUntil = 0;
    onLeaseExpired();
  }

  if (now - lastTelemetryMs >= TELEMETRY_INTERVAL_MS) {
    lastTelemetryMs = now;
    publishTelemetry();
  }

  // 우노처럼 1초마다 상태 한 줄
  if (now - lastSerialMs >= SERIAL_REPORT_MS) {
    lastSerialMs = now;
    printStatus();
  }
}
