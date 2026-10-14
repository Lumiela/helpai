/*
 * fan_control.ino 에서 옮겨온 로직. 원본 대비 바뀐 점:
 *
 *  1) stopOnError() 의 `while(true) delay(1000)` 를 제거했습니다.
 *     원본은 단독 스케치라 영구 정지가 안전한 선택이었지만, MQTT 통합 후에는
 *     그 순간 네트워크도 같이 죽어 원격 진단이 불가능해집니다.
 *     대신 팬만 비활성화(fault)하고 루프는 계속 돌려서, 서버가
 *     "팬 PWM 고장"을 status 로 받아볼 수 있게 했습니다.
 *     릴레이를 내려 팬 전원을 끊는 동작은 그대로입니다.
 *
 *  2) reportStatus() 가 계산하던 RPM 을 모듈 내부 상태(lastRpm)로 보관합니다.
 *     MQTT telemetry 에 실어 보내야 하기 때문입니다.
 *
 *  3) 스톨 감지(stalled)를 추가했습니다. TACH 가 있으니 "명령은 먹었는데
 *     실제로 안 돈다"를 구분할 수 있습니다. 원본에는 없던 기능입니다.
 *
 * 나머지 — ISR 의 에지 중복 제거, MIN_TACH_PULSE_US 필터, 릴레이/PWM 순서,
 * 전원 OFF 시 인터럽트 분리와 풀업 해제 — 는 원본 그대로입니다.
 */

#include "fan.h"
#include "config.h"

namespace fan {
namespace {

// ISR 과 일반 코드가 함께 쓰는 값들
volatile uint32_t tachPulses = 0;          // 진단용 원시 하강 에지
volatile uint32_t tachLongLowPulses = 0;   // 유효 펄스 (RPM 계산용)
volatile uint32_t tachLongHighPulses = 0;
volatile uint32_t tachLastEdgeUs = 0;
volatile int      tachLastLevel = HIGH;
volatile bool     tachEdgeSeen = false;
portMUX_TYPE tachMux = portMUX_INITIALIZER_UNLOCKED;

bool     relayIsOn = false;
bool     tachReady = false;
uint8_t  fanPercent = 0;
uint32_t pwmFrequencyHz = FAN_PWM_HZ;
uint32_t lastReportMs = 0;
uint32_t powerOnMs = 0;
uint32_t dutyOnSinceMs = 0;

int  lastRpm = -1;
bool fanStalled = false;
bool fanFaulted = false;
const char* faultMsg = "";

void ARDUINO_ISR_ATTR onTachPulse() {
  uint32_t nowUs = micros();
  int level = digitalRead(PIN_FAN_TACH);
  portENTER_CRITICAL_ISR(&tachMux);
  // 지연된 인터럽트에서 같은 상태를 읽었으면 새 전이로 중복 처리하지 않습니다.
  if (!tachEdgeSeen || level != tachLastLevel) {
    if (level == LOW) ++tachPulses;
    if (tachEdgeSeen &&
        static_cast<uint32_t>(nowUs - tachLastEdgeUs) >= FAN_MIN_TACH_PULSE_US) {
      if (tachLastLevel == LOW) ++tachLongLowPulses;
      else ++tachLongHighPulses;
    }
    tachLastLevel = level;
    tachLastEdgeUs = nowUs;
    tachEdgeSeen = true;
  }
  portEXIT_CRITICAL_ISR(&tachMux);
}

void resetTachWindow() {
  portENTER_CRITICAL(&tachMux);
  tachPulses = 0;
  tachLongLowPulses = 0;
  tachLongHighPulses = 0;
  // 설정 변경 전에 시작한 불완전한 펄스는 다음 측정으로 넘기지 않습니다.
  tachEdgeSeen = false;
  portEXIT_CRITICAL(&tachMux);
  lastReportMs = millis();
}

// 원본 stopOnError 의 대체. 영구 정지 대신 팬만 비활성화합니다.
void enterFault(const char* message) {
  detachInterrupt(digitalPinToInterrupt(PIN_FAN_TACH));
  pinMode(PIN_FAN_TACH, INPUT);
  digitalWrite(PIN_FAN_RELAY, LOW);
  relayIsOn = false;
  tachReady = false;
  fanPercent = 0;
  lastRpm = -1;
  fanFaulted = true;
  faultMsg = message;
  Serial.printf("[FAN][FAULT] %s — 팬을 비활성화하고 계속 동작합니다\n", message);
}

}  // namespace

// ──────────────────────────────────────────────────── 공개 API

bool begin() {
#if !ENABLE_FAN
  Serial.println("[FAN] ENABLE_FAN=0 — 팬 기능이 꺼져 있습니다");
  return true;
#else
  digitalWrite(PIN_FAN_RELAY, LOW);
  pinMode(PIN_FAN_RELAY, OUTPUT);

  // 시작은 전원 OFF 이므로 풀업과 인터럽트를 사용하지 않습니다.
  // ON 에서만 표준 오픈 컬렉터 TACH 를 3.3V 로 풀업합니다.
  // ★ 외부 5V/12V 풀업은 ESP32 입력을 태웁니다. 절대 금지.
  // (2026-10-06) TACH 가 GPIO34(입력 전용)로 옮겨졌습니다. 34 는 내부 풀업이 없어 아래 INPUT_PULLUP 이
  // 효과가 없고, 풀업은 외부 10kΩ(3.3V)이 맡습니다. 내부 풀업이 있는 핀으로 되돌릴 때를 위해 코드는 그대로 둡니다.
  pinMode(PIN_FAN_TACH, INPUT);

  if (!ledcAttach(PIN_FAN_PWM, FAN_PWM_HZ, FAN_PWM_BITS)) {
    enterFault("PWM 초기화 실패");
    return false;
  }
  // 초기 듀티 0 에서는 ledcReadFreq 도 0을 반환하므로 오류 판정에 쓰지 않습니다.
  pwmFrequencyHz = FAN_PWM_HZ;

  setPercent(0);
  return true;
#endif
}

bool setPercent(uint8_t percent) {
#if !ENABLE_FAN
  (void)percent;
  return false;
#else
  if (fanFaulted) return false;
  if (percent > 100) percent = 100;

  bool on = percent > 0;
  bool wasOn = relayIsOn;

  // 0% 에서는 PWM 만 낮추는 대신 릴레이로 12V 를 차단합니다.
  if (!on) {
    // 전원 차단 전에 계수를 멈춰 OFF 중 인터럽트 폭주를 막습니다.
    detachInterrupt(digitalPinToInterrupt(PIN_FAN_TACH));
    // 팬 전원이 없을 때 ESP32 내부 풀업으로 전류를 공급하지 않습니다.
    pinMode(PIN_FAN_TACH, INPUT);
    digitalWrite(PIN_FAN_RELAY, LOW);
  }

  // 비반전 배선에서는 입력 비율 그대로 HIGH 듀티를 출력합니다.
  uint32_t fanDuty = (static_cast<uint32_t>(percent) * FAN_PWM_MAX + 50) / 100;
  uint32_t gpioDuty = on ? (FAN_PWM_INVERTED ? FAN_PWM_MAX - fanDuty : fanDuty) : 0;
  if (!ledcWrite(PIN_FAN_PWM, gpioDuty)) {
    enterFault("PWM 설정 실패");
    return false;
  }

  // 켤 때는 PWM 설정이 끝난 뒤에 팬 전원을 연결합니다.
  // 순서를 바꾸면 듀티가 확정되기 전에 12V 가 들어갑니다.
  digitalWrite(PIN_FAN_RELAY, on ? HIGH : LOW);
  relayIsOn = on;
  fanPercent = percent;

  if (!on) {
    tachReady = false;
    lastRpm = -1;
    fanStalled = false;
    dutyOnSinceMs = 0;
  } else {
    if (!wasOn) {
      pinMode(PIN_FAN_TACH, INPUT_PULLUP);   // GPIO34 에서는 내부 풀업 없음 — 외부 10kΩ 풀업 사용
      tachReady = false;
      powerOnMs = millis();
    }
    dutyOnSinceMs = millis();
    fanStalled = false;
  }

  resetTachWindow();
  Serial.printf("[FAN] 설정 변경: PWM=%u%% | 릴레이=%s\n",
                static_cast<unsigned>(fanPercent), relayIsOn ? "ON" : "OFF");
  return true;
#endif
}

bool setFrequency(uint32_t frequencyHz) {
#if !ENABLE_FAN
  (void)frequencyHz;
  return false;
#else
  if (fanFaulted) return false;
  uint32_t actualHz = ledcChangeFrequency(PIN_FAN_PWM, frequencyHz, FAN_PWM_BITS);
  if (actualHz == 0) {
    enterFault("PWM 주파수 변경 실패");
    return false;
  }
  pwmFrequencyHz = actualHz;
  setPercent(fanPercent);  // 듀티와 전원 상태를 다시 적용
  Serial.printf("[FAN] 주파수 변경: 요청=%lu | 실제=%lu | 듀티=%u%% 유지\n",
                static_cast<unsigned long>(frequencyHz),
                static_cast<unsigned long>(pwmFrequencyHz),
                static_cast<unsigned>(fanPercent));
  return true;
#endif
}

void tick() {
#if ENABLE_FAN
  if (fanFaulted) return;

  // 전원 인가 후 안정화 시간이 지나면 TACH 계수를 시작합니다.
  if (relayIsOn && !tachReady && millis() - powerOnMs >= FAN_TACH_SETTLE_MS) {
    resetTachWindow();
    tachReady = true;
    attachInterrupt(digitalPinToInterrupt(PIN_FAN_TACH), onTachPulse, CHANGE);
    Serial.println("[FAN] TACH 측정 시작");
  }

  uint32_t now = millis();
  if (now - lastReportMs < SERIAL_REPORT_MS) return;

  portENTER_CRITICAL(&tachMux);
  uint32_t pulses = tachLongLowPulses;
  tachPulses = 0;
  tachLongLowPulses = 0;
  tachLongHighPulses = 0;
  portEXIT_CRITICAL(&tachMux);

  uint32_t elapsedMs = now - lastReportMs;
  lastReportMs = now;

  if (!relayIsOn || !tachReady) {
    lastRpm = -1;
    return;
  }

  if (pulses == 0) {
    lastRpm = -1;
    // 전원 ON + duty>0 인데 계속 0펄스면 팬이 안 도는 것입니다.
    if (fanPercent > 0 && dutyOnSinceMs != 0 &&
        now - dutyOnSinceMs >= FAN_STALL_TIMEOUT_MS) {
      if (!fanStalled) {
        Serial.println("[FAN][경고] duty>0 인데 TACH 미검출 — 팬 미연결 또는 스톨");
      }
      fanStalled = true;
    }
    return;
  }

  float tachHz = pulses * 1000.0f / elapsedMs;
  lastRpm = static_cast<int>(tachHz * 60.0f / FAN_PULSES_PER_REV + 0.5f);
  fanStalled = false;
#endif
}

void reportSerial() {
#if ENABLE_FAN
  if (fanFaulted) {
    Serial.printf("[FAN] FAULT: %s\n", faultMsg);
    return;
  }
  Serial.printf("[FAN] 릴레이=%s | 듀티=%u%% | 주파수=%luHz | ",
                relayIsOn ? "ON" : "OFF", static_cast<unsigned>(fanPercent),
                static_cast<unsigned long>(pwmFrequencyHz));
  if (lastRpm < 0) {
    Serial.printf("RPM=N/A (%s) | GPIO%u=%s\n",
                  !relayIsOn ? "전원 OFF"
                             : (!tachReady ? "안정화 대기"
                                           : (fanStalled ? "스톨 의심" : "TACH 미검출")),
                  static_cast<unsigned>(PIN_FAN_TACH),
                  digitalRead(PIN_FAN_TACH) == HIGH ? "HIGH" : "LOW");
  } else {
    Serial.printf("RPM=%d\n", lastRpm);
  }
#endif
}

uint8_t  percent()      { return fanPercent; }
bool     relayOn()      { return relayIsOn; }
uint32_t frequencyHz()  { return pwmFrequencyHz; }
int      rpm()          { return lastRpm; }
bool     stalled()      { return fanStalled; }
bool     faulted()      { return fanFaulted; }
const char* faultReason() { return faultMsg; }

}  // namespace fan
