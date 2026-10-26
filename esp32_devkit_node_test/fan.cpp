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
 *  4) (2026-10-13) 팬 두 대. 파일 범위 전역이던 상태를 모듈별 구조체(Unit) 두 개로 묶고, ISR 은
 *     attachInterruptArg 로 자기 상태를 받습니다. 동작 코드는 한 벌이며 핀·상태만 모듈별입니다.
 *
 *  5) (2026-10-23, 1.7.0) 모듈마다 팬 2대 = TACH 2개. TACH 계수 상태를 Tach 구조체로 떼어 모듈당 두 벌 두고,
 *     ISR 은 자기 Tach 를 받습니다. RPM·스톨은 팬마다 따로, 모듈 스톨 = 어느 한 팬이라도.
 *     릴레이 없는 모듈(순환팬, PIN_NONE)은 12V 상시 — 0% 는 PWM 0 만, TACH 는 부팅 뒤 안정화 시간이 지나면 늘 셉니다.
 *     핀·플래그는 begin() 에서 config.h 상수로 채웁니다(거대한 집합체 초기화 대신).
 *
 * 나머지 — ISR 의 에지 중복 제거, MIN_TACH_PULSE_US 필터, 릴레이/PWM 순서,
 * 전원 OFF 시 인터럽트 분리와 풀업 해제 — 는 원본 그대로입니다.
 */

#include "fan.h"
#include "config.h"

namespace fan {
namespace {

portMUX_TYPE tachMux00 = portMUX_INITIALIZER_UNLOCKED;
portMUX_TYPE tachMux01 = portMUX_INITIALIZER_UNLOCKED;
portMUX_TYPE tachMux10 = portMUX_INITIALIZER_UNLOCKED;
portMUX_TYPE tachMux11 = portMUX_INITIALIZER_UNLOCKED;

// 팬 한 대의 TACH 계수 상태 (ISR 과 일반 코드가 함께 씀)
struct Tach {
  uint8_t       pin = PIN_NONE;
  portMUX_TYPE* mux = nullptr;
  volatile uint32_t pulses = 0;          // 진단용 원시 하강 에지
  volatile uint32_t longLowPulses = 0;   // 유효 펄스 (RPM 계산용)
  volatile uint32_t longHighPulses = 0;
  volatile uint32_t lastEdgeUs = 0;
  volatile int      lastLevel = HIGH;
  volatile bool     edgeSeen = false;
  int  lastRpm = -1;
  bool stalled = false;
};

struct Unit {
  const char* tag = "";       // 로그 머리말
  uint8_t     pinRelay = PIN_NONE;
  uint8_t     pinPwm = PIN_NONE;
  bool        enabled = false;  // 컴파일 플래그
  Tach        tach[FAN_TACH_COUNT];

  bool     powerOn = false;     // 릴레이 ON (릴레이 없는 모듈은 begin 뒤 항상 true)
  bool     tachReady = false;
  uint8_t  fanPercent = 0;
  uint32_t pwmFrequencyHz = FAN_PWM_HZ;
  uint32_t lastReportMs = 0;
  uint32_t powerOnMs = 0;
  uint32_t dutyOnSinceMs = 0;

  bool fanFaulted = false;
  const char* faultMsg = "";
};

Unit units[FAN_COUNT];

inline bool valid(uint8_t id) { return id < FAN_COUNT && units[id].enabled; }
inline bool hasRelayPin(const Unit& u) { return u.pinRelay != PIN_NONE; }

void configureUnits() {
  Unit& e = units[FAN_EXHAUST];
  e.tag = "FAN";  e.pinRelay = PIN_FAN_RELAY;  e.pinPwm = PIN_FAN_PWM;  e.enabled = ENABLE_FAN != 0;
  e.tach[0].pin = PIN_FAN_TACH1;  e.tach[0].mux = &tachMux00;
  e.tach[1].pin = PIN_FAN_TACH2;  e.tach[1].mux = &tachMux01;
  Unit& c = units[FAN_CIRC];
  c.tag = "FAN2"; c.pinRelay = PIN_FAN2_RELAY; c.pinPwm = PIN_FAN2_PWM; c.enabled = ENABLE_FAN2 != 0;
  c.tach[0].pin = PIN_FAN2_TACH1; c.tach[0].mux = &tachMux10;
  c.tach[1].pin = PIN_FAN2_TACH2; c.tach[1].mux = &tachMux11;
}

void ARDUINO_ISR_ATTR onTachPulse(void* arg) {
  Tach& t = *static_cast<Tach*>(arg);
  uint32_t nowUs = micros();
  int level = digitalRead(t.pin);
  portENTER_CRITICAL_ISR(t.mux);
  // 지연된 인터럽트에서 같은 상태를 읽었으면 새 전이로 중복 처리하지 않습니다.
  if (!t.edgeSeen || level != t.lastLevel) {
    if (level == LOW) ++t.pulses;
    if (t.edgeSeen &&
        static_cast<uint32_t>(nowUs - t.lastEdgeUs) >= FAN_MIN_TACH_PULSE_US) {
      if (t.lastLevel == LOW) ++t.longLowPulses;
      else ++t.longHighPulses;
    }
    t.lastLevel = level;
    t.lastEdgeUs = nowUs;
    t.edgeSeen = true;
  }
  portEXIT_CRITICAL_ISR(t.mux);
}

void resetTachWindow(Unit& u) {
  for (uint8_t i = 0; i < FAN_TACH_COUNT; i++) {
    Tach& t = u.tach[i];
    portENTER_CRITICAL(t.mux);
    t.pulses = 0;
    t.longLowPulses = 0;
    t.longHighPulses = 0;
    // 설정 변경 전에 시작한 불완전한 펄스는 다음 측정으로 넘기지 않습니다.
    t.edgeSeen = false;
    portEXIT_CRITICAL(t.mux);
  }
  u.lastReportMs = millis();
}

void detachTachs(Unit& u) {
  for (uint8_t i = 0; i < FAN_TACH_COUNT; i++) {
    detachInterrupt(digitalPinToInterrupt(u.tach[i].pin));
    pinMode(u.tach[i].pin, INPUT);   // 팬 전원이 없을 때 ESP32 내부 풀업으로 전류를 공급하지 않습니다
  }
}

void attachTachs(Unit& u) {
  for (uint8_t i = 0; i < FAN_TACH_COUNT; i++) {
    attachInterruptArg(digitalPinToInterrupt(u.tach[i].pin), onTachPulse, &u.tach[i], CHANGE);
  }
}

// 원본 stopOnError 의 대체. 영구 정지 대신 그 모듈만 비활성화합니다.
void enterFault(Unit& u, const char* message) {
  detachTachs(u);
  if (hasRelayPin(u)) digitalWrite(u.pinRelay, LOW);
  u.powerOn = false;
  u.tachReady = false;
  u.fanPercent = 0;
  for (uint8_t i = 0; i < FAN_TACH_COUNT; i++) { u.tach[i].lastRpm = -1; u.tach[i].stalled = false; }
  u.fanFaulted = true;
  u.faultMsg = message;
  Serial.printf("[%s][FAULT] %s — 이 모듈을 비활성화하고 계속 동작합니다\n", u.tag, message);
}

bool beginUnit(Unit& u) {
  if (hasRelayPin(u)) {
    digitalWrite(u.pinRelay, LOW);
    pinMode(u.pinRelay, OUTPUT);
  }

  // 시작은 전원 OFF 이므로 풀업과 인터럽트를 사용하지 않습니다(릴레이 없는 모듈은 setPercentUnit 에서 바로 ON 처리).
  // ON 에서만 표준 오픈 컬렉터 TACH 를 3.3V 로 풀업합니다.
  // ★ 외부 5V/12V 풀업은 ESP32 입력을 태웁니다. 절대 금지.
  // 34·35 는 입력 전용·내부 풀업 없음 → INPUT_PULLUP 이 효과가 없고 외부 10kΩ(3.3V)이 맡습니다. 14·27 은 내부 풀업이 듣습니다.
  for (uint8_t i = 0; i < FAN_TACH_COUNT; i++) pinMode(u.tach[i].pin, INPUT);

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

  const bool relay = hasRelayPin(u);
  bool on = relay ? (percent > 0) : true;   // 릴레이 없는 모듈은 12V 상시 = 늘 ON
  bool wasOn = u.powerOn;

  // 0% 에서는 PWM 만 낮추는 대신 릴레이로 12V 를 차단합니다(릴레이 있는 모듈).
  if (!on) {
    // 전원 차단 전에 계수를 멈춰 OFF 중 인터럽트 폭주를 막습니다.
    detachTachs(u);
    digitalWrite(u.pinRelay, LOW);
  }

  // 비반전 배선에서는 입력 비율 그대로 HIGH 듀티를 출력합니다.
  uint32_t fanDuty = (static_cast<uint32_t>(percent) * FAN_PWM_MAX + 50) / 100;
  uint32_t gpioDuty = (percent > 0) ? (FAN_PWM_INVERTED ? FAN_PWM_MAX - fanDuty : fanDuty) : 0;
  if (!ledcWrite(u.pinPwm, gpioDuty)) {
    enterFault(u, "PWM 설정 실패");
    return false;
  }

  // 켤 때는 PWM 설정이 끝난 뒤에 팬 전원을 연결합니다.
  // 순서를 바꾸면 듀티가 확정되기 전에 12V 가 들어갑니다.
  if (relay) digitalWrite(u.pinRelay, on ? HIGH : LOW);
  u.powerOn = on;
  u.fanPercent = percent;

  if (!on) {
    u.tachReady = false;
    for (uint8_t i = 0; i < FAN_TACH_COUNT; i++) { u.tach[i].lastRpm = -1; u.tach[i].stalled = false; }
    u.dutyOnSinceMs = 0;
  } else {
    if (!wasOn) {
      for (uint8_t i = 0; i < FAN_TACH_COUNT; i++) pinMode(u.tach[i].pin, INPUT_PULLUP);   // 34·35 는 외부 10kΩ 풀업이 맡음
      u.tachReady = false;
      u.powerOnMs = millis();
    }
    u.dutyOnSinceMs = (percent > 0) ? millis() : 0;   // duty 0(릴레이 없는 모듈)이면 스톨 판정 안 함
    for (uint8_t i = 0; i < FAN_TACH_COUNT; i++) u.tach[i].stalled = false;
  }

  resetTachWindow(u);
  if (relay) Serial.printf("[%s] 설정 변경: PWM=%u%% | 릴레이=%s\n", u.tag, static_cast<unsigned>(u.fanPercent), u.powerOn ? "ON" : "OFF");
  else       Serial.printf("[%s] 설정 변경: PWM=%u%% (릴레이 없음, 12V 상시)\n", u.tag, static_cast<unsigned>(u.fanPercent));
  return true;
}

void tickUnit(Unit& u) {
  if (u.fanFaulted) return;

  // 전원 인가 후 안정화 시간이 지나면 TACH 계수를 시작합니다.
  if (u.powerOn && !u.tachReady && millis() - u.powerOnMs >= FAN_TACH_SETTLE_MS) {
    resetTachWindow(u);
    u.tachReady = true;
    attachTachs(u);
    Serial.printf("[%s] TACH 측정 시작 (GPIO%u·%u)\n", u.tag, static_cast<unsigned>(u.tach[0].pin), static_cast<unsigned>(u.tach[1].pin));
  }

  uint32_t now = millis();
  if (now - u.lastReportMs < SERIAL_REPORT_MS) return;

  uint32_t pulses[FAN_TACH_COUNT];
  for (uint8_t i = 0; i < FAN_TACH_COUNT; i++) {
    Tach& t = u.tach[i];
    portENTER_CRITICAL(t.mux);
    pulses[i] = t.longLowPulses;
    t.pulses = 0;
    t.longLowPulses = 0;
    t.longHighPulses = 0;
    portEXIT_CRITICAL(t.mux);
  }

  uint32_t elapsedMs = now - u.lastReportMs;
  u.lastReportMs = now;

  if (!u.powerOn || !u.tachReady) {
    for (uint8_t i = 0; i < FAN_TACH_COUNT; i++) u.tach[i].lastRpm = -1;
    return;
  }

  for (uint8_t i = 0; i < FAN_TACH_COUNT; i++) {
    Tach& t = u.tach[i];
    if (pulses[i] == 0) {
      t.lastRpm = -1;
      // 전원 ON + duty>0 인데 계속 0펄스면 그 팬이 안 도는 것입니다.
      if (u.fanPercent > 0 && u.dutyOnSinceMs != 0 &&
          now - u.dutyOnSinceMs >= FAN_STALL_TIMEOUT_MS) {
        if (!t.stalled) {
          Serial.printf("[%s][경고] duty>0 인데 팬 %u TACH(GPIO%u) 미검출 — 팬 미연결 또는 스톨\n",
                        u.tag, static_cast<unsigned>(i + 1), static_cast<unsigned>(t.pin));
        }
        t.stalled = true;
      }
      continue;
    }
    float tachHz = pulses[i] * 1000.0f / elapsedMs;
    t.lastRpm = static_cast<int>(tachHz * 60.0f / FAN_PULSES_PER_REV + 0.5f);
    t.stalled = false;
  }
}

const char* rpmWhy(const Unit& u, const Tach& t) {
  if (!u.powerOn)  return "전원 OFF";
  if (!u.tachReady) return "안정화 대기";
  if (t.stalled)    return "스톨 의심";
  return (u.fanPercent == 0) ? "duty 0" : "TACH 미검출";
}

void reportUnit(const Unit& u) {
  if (u.fanFaulted) {
    Serial.printf("[%s] FAULT: %s\n", u.tag, u.faultMsg);
    return;
  }
  if (hasRelayPin(u)) Serial.printf("[%s] 릴레이=%s | 듀티=%u%% | 주파수=%luHz", u.tag, u.powerOn ? "ON" : "OFF",
                                    static_cast<unsigned>(u.fanPercent), static_cast<unsigned long>(u.pwmFrequencyHz));
  else                Serial.printf("[%s] 릴레이 없음(12V 상시) | 듀티=%u%% | 주파수=%luHz", u.tag,
                                    static_cast<unsigned>(u.fanPercent), static_cast<unsigned long>(u.pwmFrequencyHz));
  for (uint8_t i = 0; i < FAN_TACH_COUNT; i++) {
    const Tach& t = u.tach[i];
    if (t.lastRpm < 0) Serial.printf(" | 팬%u RPM=N/A (%s, GPIO%u=%s)", static_cast<unsigned>(i + 1), rpmWhy(u, t),
                                     static_cast<unsigned>(t.pin), digitalRead(t.pin) == HIGH ? "HIGH" : "LOW");
    else               Serial.printf(" | 팬%u RPM=%d", static_cast<unsigned>(i + 1), t.lastRpm);
  }
  Serial.println();
}

}  // namespace

// ──────────────────────────────────────────────────── 공개 API

bool begin() {
  bool ok = true;
  configureUnits();
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

// 두 모듈은 같은 주파수·분해능이라 LEDC 타이머를 공유합니다(config.h 타이머 배정 메모). 한쪽 타이머 주파수를 바꾸면
// 다른 쪽도 같이 바뀌므로, 켜진 모듈 모두에 적용하고 각자 듀티·전원 상태를 다시 씁니다.
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
bool     hasRelay(uint8_t id)     { return valid(id) ? hasRelayPin(units[id]) : false; }
bool     relayOn(uint8_t id)      { return valid(id) ? units[id].powerOn : false; }
uint32_t frequencyHz(uint8_t id)  { return valid(id) ? units[id].pwmFrequencyHz : 0; }
int      rpm(uint8_t id, uint8_t tach) { return (valid(id) && tach < FAN_TACH_COUNT) ? units[id].tach[tach].lastRpm : -1; }
bool     stalledTach(uint8_t id, uint8_t tach) { return (valid(id) && tach < FAN_TACH_COUNT) ? units[id].tach[tach].stalled : false; }
bool     stalled(uint8_t id) {
  if (!valid(id)) return false;
  for (uint8_t i = 0; i < FAN_TACH_COUNT; i++) if (units[id].tach[i].stalled) return true;
  return false;
}
bool     faulted(uint8_t id)      { return valid(id) ? units[id].fanFaulted : false; }
const char* faultReason(uint8_t id) { return valid(id) ? units[id].faultMsg : ""; }

}  // namespace fan
