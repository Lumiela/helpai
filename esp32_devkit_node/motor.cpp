/*
 * 우노 통합 스케치(모터 D9 + 진단 모드) → ESP32 LEDC 이식. 우노 대비 바뀐 점:
 *
 *  1) Timer1 직접 제어 → LEDC.
 *     우노는 ICR1=639(16MHz/25kHz-1) 모드 14 PWM. ESP32 는 LEDC 25kHz/10bit(1024단계).
 *     duty 계산식 자체(motorOcr / writeDiagOutputs)는 그대로 옮겼습니다.
 *  2) 0% 처리. 우노는 OCR=0 이어도 1카운트 펄스가 나가 COM1A1 을 떼어 핀을 LOW 로 묶었지만
 *     LEDC 는 duty 0 이 곧 상시 LOW 라 그 처리가 필요 없습니다.
 *  3) 진단 모드의 분해능. 우노는 분주(1/8/64/256/1024)와 ICR1 로 6단계를 만들었지만
 *     LEDC 는 30Hz 만 12bit 가 필요합니다. 분해능이 바뀌는 전환은 detach → attach 로,
 *     같은 분해능끼리는 ledcChangeFrequency 로 바꿉니다.
 *  4) 실패 시 fault 상태 + 루프 유지. 원격 진단이 가능해야 하므로 영구 정지하지 않습니다.
 */

#include "motor.h"
#include "config.h"

namespace motor {
namespace {

uint8_t  setPct = 0;       // [일반] 목표 (우노 motorSet)
uint8_t  curPct = 0;       // [일반] 현재 출력 (우노 motorOut)
uint32_t curRaw = 0;       // 현재 LEDC duty 카운트 (우노 OCR1A)
uint32_t lastRampMs = 0;
bool     attached = false;

bool     diag = false;     // 진단 모드 (우노 diagMode)
uint8_t  diagPct = 0;      // [진단] raw duty (우노 diagDuty)
uint8_t  diagIdx = 0;      // [진단] 주파수 단계 (우노 modeIndex)

bool motorFaulted = false;
const char* faultMsg = "";

uint32_t curBits() { return diag ? DIAG_PWM_MODES[diagIdx].bits : MOTOR_PWM_BITS; }
uint32_t curHz()   { return diag ? DIAG_PWM_MODES[diagIdx].hz   : MOTOR_PWM_HZ; }

// [일반] 속도(%) → LEDC duty 카운트. 우노 motorOcr() 그대로.
//   실제 duty = (raw + 1) / MOTOR_PWM_FULL
uint32_t rawForPercent(uint8_t percent) {
  if (percent == 0) return 0;
  if (percent > 100) percent = 100;

  const uint32_t full = MOTOR_PWM_FULL;                              // 1024
  const uint32_t base = full * MOTOR_MIN_DUTY_PERCENT;               // 하한 duty
  const uint32_t span = full * (100u - MOTOR_MIN_DUTY_PERCENT);      // 가변 구간 폭
  uint32_t v = (base + span * percent / 100u) / 100u;                // 카운트 수

  if (v == 0)    return 0;
  if (v > full)  v = full;
  return v - 1;
}

// [진단] raw duty(%) → 카운트. 우노 writeDiagOutputs() 그대로 (보정 없음).
uint32_t diagRawForPercent(uint8_t percent) {
  if (percent == 0) return 0;
  if (percent > 100) percent = 100;
  const uint32_t full = 1UL << DIAG_PWM_MODES[diagIdx].bits;
  uint32_t v = full * percent / 100u;
  if (v == 0)    return 0;
  if (v > full)  v = full;
  return v - 1;
}

bool attachPwm(uint32_t hz, uint8_t bits) {
  if (attached) {
    ledcDetach(PIN_MOTOR_PWM);
    attached = false;
  }
  if (!ledcAttach(PIN_MOTOR_PWM, hz, bits)) return false;
  attached = true;
  return true;
}

void enterFault(const char* message) {
  // 게이트를 확실히 내립니다. 실패해도 10kΩ 풀다운이 마지막으로 막습니다.
  if (attached) {
    ledcDetach(PIN_MOTOR_PWM);
    attached = false;
  }
  pinMode(PIN_MOTOR_PWM, OUTPUT);
  digitalWrite(PIN_MOTOR_PWM, LOW);
  setPct = 0;
  curPct = 0;
  curRaw = 0;
  diagPct = 0;
  motorFaulted = true;
  faultMsg = message;
  Serial.printf("[MOTOR][FAULT] %s — 모터를 비활성화하고 계속 동작합니다\n", message);
}

bool writeRaw(uint32_t raw) {
  if (!attached) return false;
  if (!ledcWrite(PIN_MOTOR_PWM, raw)) {
    enterFault("PWM 설정 실패");
    return false;
  }
  curRaw = raw;
  return true;
}

// [일반] 현재 출력값을 PWM 에 반영 (우노 writeNormalOutputs 의 모터 부분).
bool writeOutput() { return writeRaw(rawForPercent(curPct)); }

}  // namespace

// ──────────────────────────────────────────────────── 공개 API

bool begin() {
#if !ENABLE_MOTOR
  Serial.println("[MOTOR] ENABLE_MOTOR=0 — 모터 기능이 꺼져 있습니다");
  return true;
#else
  // 부팅 직후 확실히 LOW. LEDC 부착 전에 GPIO 가 떠 있는 시간을 줄입니다.
  pinMode(PIN_MOTOR_PWM, OUTPUT);
  digitalWrite(PIN_MOTOR_PWM, LOW);

  if (!attachPwm(MOTOR_PWM_HZ, MOTOR_PWM_BITS)) {
    enterFault("PWM 초기화 실패");
    return false;
  }
  setPct = 0;
  curPct = 0;
  lastRampMs = millis();
  if (!writeOutput()) return false;

  Serial.printf("[MOTOR] GPIO%u, %luHz, %ubit | 최소 duty 보정 %u%% (입력 1~100%% → duty %u~100%%) | 램프 %lums/1%%\n",
                static_cast<unsigned>(PIN_MOTOR_PWM),
                static_cast<unsigned long>(MOTOR_PWM_HZ),
                static_cast<unsigned>(MOTOR_PWM_BITS),
                static_cast<unsigned>(MOTOR_MIN_DUTY_PERCENT),
                static_cast<unsigned>(MOTOR_MIN_DUTY_PERCENT),
                static_cast<unsigned long>(MOTOR_RAMP_STEP_MS));
  return true;
#endif
}

bool setPercent(uint8_t percent) {
#if !ENABLE_MOTOR
  (void)percent;
  return false;
#else
  if (motorFaulted || diag) return false;
  if (percent > 100) percent = 100;
  setPct = percent;
  Serial.printf("[MOTOR] 목표 속도: %u%% (현재 출력 %u%%)\n",
                static_cast<unsigned>(setPct), static_cast<unsigned>(curPct));
  return true;
#endif
}

void stopNow() {
#if ENABLE_MOTOR
  if (motorFaulted) return;
  setPct = 0;
  curPct = 0;
  if (diag) {
    diagPct = 0;
    writeRaw(0);
  } else {
    writeOutput();
  }
#endif
}

void tick() {
#if ENABLE_MOTOR
  if (motorFaulted || diag) return;
  if (curPct == setPct) return;

  uint32_t now = millis();
  if (now - lastRampMs < MOTOR_RAMP_STEP_MS) return;
  lastRampMs = now;

  if (curPct < setPct)      curPct++;
  else if (curPct > setPct) curPct--;
  writeOutput();
#endif
}

// ── 진단 모드 ──

bool diagEnter() {
#if !ENABLE_MOTOR
  return false;
#else
  if (motorFaulted) return false;
  setPct = 0;
  curPct = 0;
  diagPct = 0;
  diagIdx = 0;
  if (!attachPwm(DIAG_PWM_MODES[diagIdx].hz, DIAG_PWM_MODES[diagIdx].bits)) {
    enterFault("진단 PWM 초기화 실패");
    return false;
  }
  diag = true;
  return writeRaw(0);
#endif
}

bool diagExit() {
#if !ENABLE_MOTOR
  return false;
#else
  if (!diag) return true;
  diag = false;
  diagPct = 0;
  diagIdx = 0;
  setPct = 0;
  curPct = 0;
  if (motorFaulted) return false;
  if (!attachPwm(MOTOR_PWM_HZ, MOTOR_PWM_BITS)) {
    enterFault("일반 PWM 복귀 실패");
    return false;
  }
  return writeOutput();
#endif
}

bool diagActive() { return diag; }

bool diagSetDuty(uint8_t percent) {
#if !ENABLE_MOTOR
  (void)percent;
  return false;
#else
  if (!diag || motorFaulted) return false;
  if (percent > 100) percent = 100;
  diagPct = percent;
  return writeRaw(diagRawForPercent(diagPct));
#endif
}

bool diagSetModeIndex(uint8_t index) {
#if !ENABLE_MOTOR
  (void)index;
  return false;
#else
  if (!diag || motorFaulted) return false;
  if (index >= DIAG_PWM_MODE_COUNT) {
    Serial.printf("[MOTOR] 주파수 단계 범위 밖: %u (f0~f%u)\n",
                  static_cast<unsigned>(index), static_cast<unsigned>(DIAG_PWM_MODE_COUNT - 1));
    return false;
  }
  const uint8_t prevBits = DIAG_PWM_MODES[diagIdx].bits;
  const uint8_t prevIdx  = diagIdx;
  diagIdx = index;

  if (DIAG_PWM_MODES[diagIdx].bits == prevBits) {
    // 분해능이 같으면 주파수만 바꿉니다.
    if (ledcChangeFrequency(PIN_MOTOR_PWM, DIAG_PWM_MODES[diagIdx].hz, DIAG_PWM_MODES[diagIdx].bits) == 0) {
      diagIdx = prevIdx;
      enterFault("PWM 주파수 변경 실패");
      return false;
    }
  } else {
    // ★ 분해능이 다르면(25kHz 10bit ↔ 30Hz 12bit) 반드시 다시 붙여야 합니다.
    if (!attachPwm(DIAG_PWM_MODES[diagIdx].hz, DIAG_PWM_MODES[diagIdx].bits)) {
      enterFault("PWM 주파수 변경 실패 (재부착)");
      return false;
    }
  }
  return writeRaw(diagRawForPercent(diagPct));   // duty % 를 새 분해능으로 다시 계산
#endif
}

bool diagNextMode() {
  return diagSetModeIndex(static_cast<uint8_t>((diagIdx + 1) % DIAG_PWM_MODE_COUNT));
}

void printDiagModes() {
  Serial.println("--- 진단 PWM 주파수 ---");
  for (uint8_t i = 0; i < DIAG_PWM_MODE_COUNT; i++) {
    Serial.printf("  f%u  %-7s (%ubit, 0~%lu)%s\n",
                  static_cast<unsigned>(i), DIAG_PWM_MODES[i].label,
                  static_cast<unsigned>(DIAG_PWM_MODES[i].bits),
                  static_cast<unsigned long>((1UL << DIAG_PWM_MODES[i].bits) - 1),
                  (diag && i == diagIdx) ? "  ← 현재" : "");
  }
}

void reportSerial() {
#if ENABLE_MOTOR
  if (motorFaulted) {
    Serial.printf("[MOTOR] FAULT: %s\n", faultMsg);
    return;
  }
  if (diag) {
    Serial.printf("[MOTOR] 진단 | %s (f%u) | raw duty=%u%% | 카운트=%lu/%lu\n",
                  DIAG_PWM_MODES[diagIdx].label, static_cast<unsigned>(diagIdx),
                  static_cast<unsigned>(diagPct),
                  static_cast<unsigned long>(curRaw), static_cast<unsigned long>(rawMax()));
    return;
  }
  Serial.printf("[MOTOR] 목표=%u%% | 출력=%u%% | 카운트=%lu/%lu | %luHz%s\n",
                static_cast<unsigned>(setPct), static_cast<unsigned>(curPct),
                static_cast<unsigned long>(curRaw), static_cast<unsigned long>(rawMax()),
                static_cast<unsigned long>(MOTOR_PWM_HZ),
                (curPct != setPct) ? " | 램프 중" : "");
#endif
}

uint8_t  target()         { return setPct; }
uint8_t  output()         { return curPct; }
bool     ramping()        { return curPct != setPct; }
uint32_t rawDuty()        { return curRaw; }
uint32_t rawMax()         { return (1UL << curBits()) - 1; }
uint32_t frequencyHz()    { return curHz(); }
uint8_t  minDutyPercent() { return MOTOR_MIN_DUTY_PERCENT; }
bool     faulted()        { return motorFaulted; }
const char* faultReason() { return faultMsg; }
uint8_t  diagDuty()       { return diagPct; }
uint8_t  diagModeIndex()  { return diagIdx; }
const char* diagModeLabel() { return DIAG_PWM_MODES[diagIdx].label; }

}  // namespace motor
