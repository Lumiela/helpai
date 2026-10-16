/*
 * 우노 통합 스케치(LED D10, 일반 모드) → ESP32 LEDC 이식. 우노 대비 바뀐 점:
 *
 *  1) Timer1 OC1B → LEDC 25kHz/10bit. 감마식(ledOcr)과 램프는 그대로.
 *     우노: OCR1B = 639 × p² / 10000  →  여기: 카운트 = 1024 × p² / 10000.
 *     100% 는 카운트 1024 = 상시 HIGH (코어가 최대 카운트를 완전 점등으로 처리).
 *  2) 0% 처리. 우노는 COM1B1 을 떼어 핀을 LOW 로 묶었지만 LEDC 는 duty 0 이 곧 상시 LOW.
 *  3) 실패 시 fault 상태 + 루프 유지 (motor 모듈과 같은 규칙).
 */

#include "led.h"
#include "config.h"

namespace led {
namespace {

uint8_t  setPct = 0;       // 목표 (우노 ledSet)
uint8_t  curPct = 0;       // 현재 출력 (우노 ledOut)
uint32_t curRaw = 0;       // 현재 LEDC duty 카운트 (우노 OCR1B)
uint32_t lastRampMs = 0;
bool     attached = false;

bool ledFaulted = false;
const char* faultMsg = "";

// 밝기(%) → LEDC duty 카운트. 우노 ledOcr() 그대로.
uint32_t rawForPercent(uint8_t percent) {
  if (percent == 0) return 0;
  if (percent > 100) percent = 100;
  if (LED_GAMMA_CORRECTION) {
    return LED_PWM_FULL * percent * percent / 10000u;
  }
  return LED_PWM_FULL * percent / 100u;
}

void enterFault(const char* message) {
  if (attached) {
    ledcDetach(PIN_LED_PWM);
    attached = false;
  }
  pinMode(PIN_LED_PWM, OUTPUT);
  digitalWrite(PIN_LED_PWM, LOW);
  setPct = 0;
  curPct = 0;
  curRaw = 0;
  ledFaulted = true;
  faultMsg = message;
  Serial.printf("[LED][FAULT] %s — LED 를 비활성화하고 계속 동작합니다\n", message);
}

bool writeOutput() {
  if (!attached) return false;
  uint32_t raw = rawForPercent(curPct);
  if (!ledcWrite(PIN_LED_PWM, raw)) {
    enterFault("PWM 설정 실패");
    return false;
  }
  curRaw = raw;
  return true;
}

}  // namespace

// ──────────────────────────────────────────────────── 공개 API

bool begin() {
#if !ENABLE_LED
  Serial.println("[LED] ENABLE_LED=0 — LED 기능이 꺼져 있습니다");
  return true;
#else
  pinMode(PIN_LED_PWM, OUTPUT);
  digitalWrite(PIN_LED_PWM, LOW);   // 부팅 직후 확실히 소등 (우노와 동일)

  if (!ledcAttach(PIN_LED_PWM, LED_PWM_HZ, LED_PWM_BITS)) {
    enterFault("PWM 초기화 실패");
    return false;
  }
  attached = true;
  setPct = 0;
  curPct = 0;
  lastRampMs = millis();
  if (!writeOutput()) return false;

  Serial.printf("[LED] GPIO%u, %luHz, %ubit | 감마 보정 %s | 램프 %lums/1%%\n",
                static_cast<unsigned>(PIN_LED_PWM),
                static_cast<unsigned long>(LED_PWM_HZ),
                static_cast<unsigned>(LED_PWM_BITS),
                LED_GAMMA_CORRECTION ? "켬" : "끔",
                static_cast<unsigned long>(LED_RAMP_STEP_MS));
  return true;
#endif
}

bool setPercent(uint8_t percent) {
#if !ENABLE_LED
  (void)percent;
  return false;
#else
  if (ledFaulted) return false;
  if (percent > 100) percent = 100;
  setPct = percent;
  Serial.printf("[LED] 목표 밝기: %u%% (현재 출력 %u%%)\n",
                static_cast<unsigned>(setPct), static_cast<unsigned>(curPct));
  return true;
#endif
}

void stopNow() {
#if ENABLE_LED
  if (ledFaulted) return;
  setPct = 0;
  curPct = 0;
  writeOutput();
#endif
}

void tick() {
#if ENABLE_LED
  if (ledFaulted) return;
  if (curPct == setPct) return;

  uint32_t now = millis();
  if (now - lastRampMs < LED_RAMP_STEP_MS) return;
  lastRampMs = now;

  if (curPct < setPct)      curPct++;
  else if (curPct > setPct) curPct--;
  writeOutput();
#endif
}

bool setModeIndex(uint8_t index) {
  Serial.printf("[LED] 이 판은 %luHz 고정 — 주파수 모드 %u 무시\n",
                static_cast<unsigned long>(LED_PWM_HZ), static_cast<unsigned>(index));
  return false;
}

bool nextMode() { return setModeIndex(1); }

void printModes() {
  Serial.printf("--- LED 주파수: %luHz 고정 (%ubit) ---\n",
                static_cast<unsigned long>(LED_PWM_HZ), static_cast<unsigned>(LED_PWM_BITS));
}

void reportSerial() {
#if ENABLE_LED
  if (ledFaulted) {
    Serial.printf("[LED] FAULT: %s\n", faultMsg);
    return;
  }
  Serial.printf("[LED] 목표=%u%% | 출력=%u%% | 카운트=%lu/%lu | %luHz%s\n",
                static_cast<unsigned>(setPct), static_cast<unsigned>(curPct),
                static_cast<unsigned long>(curRaw), static_cast<unsigned long>(rawMax()),
                static_cast<unsigned long>(LED_PWM_HZ),
                (curPct != setPct) ? " | 램프 중" : "");
#endif
}

uint8_t  percent()        { return setPct; }
uint8_t  output()         { return curPct; }
bool     ramping()        { return curPct != setPct; }
uint32_t rawDuty()        { return curRaw; }
uint32_t rawMax()         { return LED_PWM_FULL - 1; }
uint32_t frequencyHz()    { return LED_PWM_HZ; }
uint8_t  modeIndex()      { return 0; }
const char* modeListCsv() { return "25kHz"; }
bool     faulted()        { return ledFaulted; }
const char* faultReason() { return faultMsg; }

}  // namespace led
