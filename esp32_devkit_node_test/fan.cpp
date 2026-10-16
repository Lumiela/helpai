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
 *  4) (2026-10-13) 팬 두 대. 파일 범위 전역이던 상태를 팬별 구조체(Unit) 두 개로 묶고, ISR 은
 *     attachInterruptArg 로 자기 Unit 을 받습니다. 동작 코드는 한 벌이며 핀·상태만 팬별입니다.
 *     사용자 결정: 순환팬 fan2 = 4핀 PWM 팬, 릴레이 19 · PWM 23 · TACH 35.
 *
 * 나머지 — ISR 의 에지 중복 제거, MIN_TACH_PULSE_US 필터, 릴레이/PWM 순서,
 * 전원 OFF 시 인터럽트 분리와 풀업 해제 — 는 원본 그대로입니다.
 */

#include "fan.h"
#include "config.h"

namespace fan {
namespace {

portMUX_TYPE tachMux0 = portMUX_INITIALIZER_UNLOCKED;
portMUX_TYPE tachMux1 = portMUX_INITIALIZER_UNLOCKED;

struct Unit {
  const char*   tag;        // 로그 머리말
  uint8_t       pinRelay;
  uint8_t       pinPwm;
  uint8_t       pinTach;
  bool          enabled;    // 컴파일 플래그
  portMUX_TYPE* mux;

  // ISR 과 일반 코드가 함께 쓰는 값들
  volatile uint32_t tachPulses;          // 진단용 원시 하강 에지
  volatile uint32_t tachLongLowPulses;   // 유효 펄스 (RPM 계산용)
  volatile uint32_t tachLongHighPulses;
  volatile uint32_t tachLastEdgeUs;
  volatile int      tachLastLevel;
  volatile bool     tachEdgeSeen;

  bool     relayIsOn;
  bool     tachReady;
  uint8_t  fanPercent;
  uint32_t pwmFrequencyHz;
  uint32_t lastReportMs;
  uint32_t powerOnMs;
  uint32_t dutyOnSinceMs;

  int  lastRpm;
  bool fanStalled;
  bool fanFaulted;
  const char* faultMsg;
};

Unit units[FAN_COUNT] = {
  { "FAN",  PIN_FAN_RELAY,  PIN_FAN_PWM,  PIN_FAN_TACH,  ENABLE_FAN  != 0, &tachMux0,
    0, 0, 0, 0, HIGH, false, false, false, 0, FAN_PWM_HZ, 0, 0, 0, -1, false, false, "" },
  { "FAN2", PIN_FAN2_RELAY, PIN_FAN2_PWM, PIN_FAN2_TACH, ENABLE_FAN2 != 0, &tachMux1,
    0, 0, 0, 0, HIGH, false, false, false, 0, FAN_PWM_HZ, 0, 0, 0, -1, false, false, "" },
};

inline bool valid(uint8_t id) { return id < FAN_COUNT && units[id].enabled; }

void ARDUINO_ISR_ATTR onTachPulse(void* arg) {
  Unit& u = *static_cast<Unit*>(arg);
  uint32_t nowUs = micros();
  int level = digitalRead(u.pinTach);
  portENTER_CRITICAL_ISR(u.mux);
  // 지연된 인터럽트에서 같은 상태를 읽었으면 새 전이로 중복 처리하지 않습니다.
  if (!u.tachEdgeSeen || level != u.tachLastLevel) {
    if (level == LOW) ++u.tachPulses;
    if (u.tachEdgeSeen &&
        static_cast<uint32_t>(nowUs - u.tachLastEdgeUs) >= FAN_MIN_TACH_PULSE_US) {
      if (u.tachLastLevel == LOW) ++u.tachLongLowPulses;
      else ++u.tachLongHighPulses;
    }
    u.tachLastLevel = level;
    u.tachLastEdgeUs = nowUs;
    u.tachEdgeSeen = true;
  }
  portEXIT_CRITICAL_ISR(u.mux);
}

void resetTachWindow(Unit& u) {
  portENTER_CRITICAL(u.mux);
  u.tachPulses = 0;
  u.tachLongLowPulses = 0;
  u.tachLongHighPulses = 0;
  // 설정 변경 전에 시작한 불완전한 펄스는 다음 측정으로 넘기지 않습니다.
  u.tachEdgeSeen = false;
  portEXIT_CRITICAL(u.mux);
  u.lastReportMs = millis();
}

// 원본 stopOnError 의 대체. 영구 정지 대신 그 팬만 비활성화합니다.
void enterFault(Unit& u, const char* message) {
  detachInterrupt(digitalPinToInterrupt(u.pinTach));
  pinMode(u.pinTach, INPUT);
  digitalWrite(u.pinRelay, LOW);
  u.relayIsOn = false;
  u.tachReady = false;
  u.fanPercent = 0;
  u.lastRpm = -1;
  u.fanFaulted = true;
  u.faultMsg = message;
  Serial.printf("[%s][FAULT] %s — 이 팬을 비활성화하고 계속 동작합니다\n", u.tag, message);
}

bool beginUnit(Unit& u) {
  digitalWrite(u.pinRelay, LOW);
  pinMode(u.pinRelay, OUTPUT);

  // 시작은 전원 OFF 이므로 풀업과 인터럽트를 사용하지 않습니다.
  // ON 에서만 표준 오픈 컬렉터 TACH 를 3.3V 로 풀업합니다.
  // ★ 외부 5V/12V 풀업은 ESP32 입력을 태웁니다. 절대 금지.
  // (2026-10-06) TACH 가 GPIO34(입력 전용)로 옮겨졌습니다. 34·35 는 내부 풀업이 없어 아래 INPUT_PULLUP 이
  // 효과가 없고, 풀업은 외부 10kΩ(3.3V)이 맡습니다. 내부 풀업이 있는 핀으로 되돌릴 때를 위해 코드는 그대로 둡니다.
  pinMode(u.pinTach, INPUT);

  if (!ledcAttach(u.pinPwm, FAN_PWM_HZ, FAN_PWM_BITS)) {
    enterFault(u, "PWM 초기화 실패");
    return false;
  }
  // 초기 듀티 0 에서는 ledcReadFreq 도 0을 반환하므로 오류 판정에 쓰지 않습니다.
  u.pwmFrequencyHz = FAN_PWM_HZ;
  return true;
}

bool setPercentUnit(Unit& u, uint8_t percent) {
  if (u.fanFaulted) return false;
  if (percent > 100) percent = 100;

  bool on = percent > 0;
  bool wasOn = u.relayIsOn;

  // 0% 에서는 PWM 만 낮추는 대신 릴레이로 12V 를 차단합니다.
  if (!on) {
    // 전원 차단 전에 계수를 멈춰 OFF 중 인터럽트 폭주를 막습니다.
    detachInterrupt(digitalPinToInterrupt(u.pinTach));
    // 팬 전원이 없을 때 ESP32 내부 풀업으로 전류를 공급하지 않습니다.
    pinMode(u.pinTach, INPUT);
    digitalWrite(u.pinRelay, LOW);
  }

  // 비반전 배선에서는 입력 비율 그대로 HIGH 듀티를 출력합니다.
  uint32_t fanDuty = (static_cast<uint32_t>(percent) * FAN_PWM_MAX + 50) / 100;
  uint32_t gpioDuty = on ? (FAN_PWM_INVERTED ? FAN_PWM_MAX - fanDuty : fanDuty) : 0;
  if (!ledcWrite(u.pinPwm, gpioDuty)) {
    enterFault(u, "PWM 설정 실패");
    return false;
  }

  // 켤 때는 PWM 설정이 끝난 뒤에 팬 전원을 연결합니다.
  // 순서를 바꾸면 듀티가 확정되기 전에 12V 가 들어갑니다.
  digitalWrite(u.pinRelay, on ? HIGH : LOW);
  u.relayIsOn = on;
  u.fanPercent = percent;

  if (!on) {
    u.tachReady = false;
    u.lastRpm = -1;
    u.fanStalled = false;
    u.dutyOnSinceMs = 0;
  } else {
    if (!wasOn) {
      pinMode(u.pinTach, INPUT_PULLUP);   // GPIO34·35 에서는 내부 풀업 없음 — 외부 10kΩ 풀업 사용
      u.tachReady = false;
      u.powerOnMs = millis();
    }
    u.dutyOnSinceMs = millis();
    u.fanStalled = false;
  }

  resetTachWindow(u);
  Serial.printf("[%s] 설정 변경: PWM=%u%% | 릴레이=%s\n",
                u.tag, static_cast<unsigned>(u.fanPercent), u.relayIsOn ? "ON" : "OFF");
  return true;
}

void tickUnit(Unit& u) {
  if (u.fanFaulted) return;

  // 전원 인가 후 안정화 시간이 지나면 TACH 계수를 시작합니다.
  if (u.relayIsOn && !u.tachReady && millis() - u.powerOnMs >= FAN_TACH_SETTLE_MS) {
    resetTachWindow(u);
    u.tachReady = true;
    attachInterruptArg(digitalPinToInterrupt(u.pinTach), onTachPulse, &u, CHANGE);
    Serial.printf("[%s] TACH 측정 시작\n", u.tag);
  }

  uint32_t now = millis();
  if (now - u.lastReportMs < SERIAL_REPORT_MS) return;

  portENTER_CRITICAL(u.mux);
  uint32_t pulses = u.tachLongLowPulses;
  u.tachPulses = 0;
  u.tachLongLowPulses = 0;
  u.tachLongHighPulses = 0;
  portEXIT_CRITICAL(u.mux);

  uint32_t elapsedMs = now - u.lastReportMs;
  u.lastReportMs = now;

  if (!u.relayIsOn || !u.tachReady) {
    u.lastRpm = -1;
    return;
  }

  if (pulses == 0) {
    u.lastRpm = -1;
    // 전원 ON + duty>0 인데 계속 0펄스면 팬이 안 도는 것입니다.
    if (u.fanPercent > 0 && u.dutyOnSinceMs != 0 &&
        now - u.dutyOnSinceMs >= FAN_STALL_TIMEOUT_MS) {
      if (!u.fanStalled) {
        Serial.printf("[%s][경고] duty>0 인데 TACH 미검출 — 팬 미연결 또는 스톨\n", u.tag);
      }
      u.fanStalled = true;
    }
    return;
  }

  float tachHz = pulses * 1000.0f / elapsedMs;
  u.lastRpm = static_cast<int>(tachHz * 60.0f / FAN_PULSES_PER_REV + 0.5f);
  u.fanStalled = false;
}

void reportUnit(const Unit& u) {
  if (u.fanFaulted) {
    Serial.printf("[%s] FAULT: %s\n", u.tag, u.faultMsg);
    return;
  }
  Serial.printf("[%s] 릴레이=%s | 듀티=%u%% | 주파수=%luHz | ",
                u.tag, u.relayIsOn ? "ON" : "OFF", static_cast<unsigned>(u.fanPercent),
                static_cast<unsigned long>(u.pwmFrequencyHz));
  if (u.lastRpm < 0) {
    Serial.printf("RPM=N/A (%s) | GPIO%u=%s\n",
                  !u.relayIsOn ? "전원 OFF"
                               : (!u.tachReady ? "안정화 대기"
                                               : (u.fanStalled ? "스톨 의심" : "TACH 미검출")),
                  static_cast<unsigned>(u.pinTach),
                  digitalRead(u.pinTach) == HIGH ? "HIGH" : "LOW");
  } else {
    Serial.printf("RPM=%d\n", u.lastRpm);
  }
}

}  // namespace

// ──────────────────────────────────────────────────── 공개 API

bool begin() {
  bool ok = true;
#if !ENABLE_FAN
  Serial.println("[FAN] ENABLE_FAN=0 — 배기팬 기능이 꺼져 있습니다");
#endif
#if !ENABLE_FAN2
  Serial.println("[FAN2] ENABLE_FAN2=0 — 순환팬 핀 미배정, 가상값으로 동작");
#endif
  for (uint8_t i = 0; i < FAN_COUNT; i++) {
    if (!units[i].enabled) continue;
    if (!beginUnit(units[i])) { ok = false; continue; }
    setPercentUnit(units[i], 0);
  }
  return ok;
}

bool enabled(uint8_t id) { return valid(id); }

bool setPercent(uint8_t percent, uint8_t id) {
  if (!valid(id)) return false;
  return setPercentUnit(units[id], percent);
}

// 두 팬은 같은 주파수·분해능이라 LEDC 타이머를 공유합니다(config.h 타이머 배정 메모). 한쪽 타이머 주파수를 바꾸면
// 다른 쪽도 같이 바뀌므로, 켜진 팬 모두에 적용하고 각자 듀티·전원 상태를 다시 씁니다.
bool setFrequency(uint32_t frequencyHz) {
  bool any = false;
  for (uint8_t i = 0; i < FAN_COUNT; i++) {
    Unit& u = units[i];
    if (!u.enabled || u.fanFaulted) continue;
    uint32_t actualHz = ledcChangeFrequency(u.pinPwm, frequencyHz, FAN_PWM_BITS);
    if (actualHz == 0) {
      enterFault(u, "PWM 주파수 변경 실패");
      continue;
    }
    u.pwmFrequencyHz = actualHz;
    setPercentUnit(u, u.fanPercent);  // 듀티와 전원 상태를 다시 적용
    Serial.printf("[%s] 주파수 변경: 요청=%lu | 실제=%lu | 듀티=%u%% 유지\n",
                  u.tag, static_cast<unsigned long>(frequencyHz),
                  static_cast<unsigned long>(u.pwmFrequencyHz),
                  static_cast<unsigned>(u.fanPercent));
    any = true;
  }
  return any;
}

void tick() {
  for (uint8_t i = 0; i < FAN_COUNT; i++) {
    if (units[i].enabled) tickUnit(units[i]);
  }
}

void reportSerial() {
  for (uint8_t i = 0; i < FAN_COUNT; i++) {
    if (units[i].enabled) reportUnit(units[i]);
  }
}

uint8_t  percent(uint8_t id)      { return valid(id) ? units[id].fanPercent : 0; }
bool     relayOn(uint8_t id)      { return valid(id) ? units[id].relayIsOn : false; }
uint32_t frequencyHz(uint8_t id)  { return valid(id) ? units[id].pwmFrequencyHz : 0; }
int      rpm(uint8_t id)          { return valid(id) ? units[id].lastRpm : -1; }
bool     stalled(uint8_t id)      { return valid(id) ? units[id].fanStalled : false; }
bool     faulted(uint8_t id)      { return valid(id) ? units[id].fanFaulted : false; }
const char* faultReason(uint8_t id) { return valid(id) ? units[id].faultMsg : ""; }

}  // namespace fan
