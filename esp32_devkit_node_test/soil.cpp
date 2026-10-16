/*
 * 우노 통합 스케치(토양 A0) → ESP32 + ADS1115 이식(ESP32-CAM 판에서 DevKitC 판으로 무수정 이관). 우노 대비 바뀐 점:
 *
 *  1) analogRead(A0) → ADS1115 단발 변환 (I2C). 변환은 4ms 걸리므로 "시작 → 다음 루프에서
 *     완료 확인 → 읽기" 의 두 단계로 나눠 루프를 막지 않습니다.
 *  2) 눈금 0~1023(5V) → 0~32767(0.125mV). 보정 초기값은 config.h 에서 환산했습니다.
 *  3) 보정값이 NVS 에 남습니다. 우노는 리셋하면 초기값으로 돌아갔습니다.
 *  4) ADS1115 가 없거나 떨어지면 값 없음(-1)으로 보고하고 주기적으로 다시 찾습니다.
 *
 * ADS1115 레지스터 (데이터시트 §9.6):
 *   0x00 변환 결과 16bit 부호 있는 정수
 *   0x01 설정: OS(15) MUX(14:12) PGA(11:9) MODE(8) DR(7:5) COMP_MODE(4) COMP_POL(3)
 *              COMP_LAT(2) COMP_QUE(1:0)
 *   단발 변환 시작 = OS 1, MODE 1. 완료 여부 = 설정 레지스터를 읽어 OS 비트가 1.
 */

#include "soil.h"
#include "config.h"

#include <Wire.h>
#include <Preferences.h>

namespace soil {
namespace {

enum CalTarget : uint8_t { CAL_NONE, CAL_AIR, CAL_WATER };
enum Phase : uint8_t { PH_IDLE, PH_CONVERTING };

int airVal   = SOIL_AIR_VALUE;
int waterVal = SOIL_WATER_VALUE;

bool     adsOk = false;
uint32_t lastProbeMs = 0;

Phase    phase = PH_IDLE;
uint32_t phaseMs = 0;
long     accum = 0;
uint8_t  sampleIndex = 0;
uint32_t lastSampleMs = 0;

int lastRaw = -1;
int lastMoisture = -1;

CalTarget calPending = CAL_NONE;
bool      calDoneLatch = false;

Preferences prefs;

// ── ADS1115 레지스터 접근 ──

bool writeReg(uint8_t reg, uint16_t value) {
  Wire.beginTransmission(ADS1115_ADDR);
  Wire.write(reg);
  Wire.write(static_cast<uint8_t>(value >> 8));
  Wire.write(static_cast<uint8_t>(value & 0xFF));
  return Wire.endTransmission() == 0;
}

bool readReg(uint8_t reg, uint16_t* out) {
  Wire.beginTransmission(ADS1115_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission() != 0) return false;
  if (Wire.requestFrom(static_cast<uint8_t>(ADS1115_ADDR), static_cast<uint8_t>(2)) != 2) return false;
  uint16_t hi = Wire.read();
  uint16_t lo = Wire.read();
  *out = static_cast<uint16_t>((hi << 8) | lo);
  return true;
}

bool probe() {
  Wire.beginTransmission(ADS1115_ADDR);
  return Wire.endTransmission() == 0;
}

// 단발 변환 시작: OS=1, MUX=1xx(AINx↔GND), PGA, MODE=1, DR, 비교기 끔(11)
bool startConversion() {
  const uint16_t mux = static_cast<uint16_t>((0x4u | (ADS1115_CHANNEL & 0x3u)) << 12);
  const uint16_t cfg = 0x8000u | mux | ADS1115_PGA_BITS | 0x0100u | ADS1115_DR_BITS | 0x0003u;
  return writeReg(0x01, cfg);
}

bool conversionDone(bool* done) {
  uint16_t cfg = 0;
  if (!readReg(0x01, &cfg)) return false;
  *done = (cfg & 0x8000u) != 0;
  return true;
}

bool readResult(int* out) {
  uint16_t v = 0;
  if (!readReg(0x00, &v)) return false;
  const int16_t s = static_cast<int16_t>(v);
  *out = (s < 0) ? 0 : s;          // 단극 배선에서 음수는 접지 잡음. 0 으로 자릅니다
  return true;
}

// ── 내부 상태 ──

void restartSampling() {
  accum = 0;
  sampleIndex = 0;
  phase = PH_IDLE;
  lastSampleMs = millis() - SOIL_SAMPLE_GAP_MS;   // 다음 tick 에서 바로 첫 샘플
}

void markFault(const char* why) {
  if (adsOk) Serial.printf("[SOIL][오류] ADS1115 %s — 다시 찾을 때까지 값 없음\n", why);
  adsOk = false;
  phase = PH_IDLE;
  accum = 0;
  sampleIndex = 0;
  lastRaw = -1;
  lastMoisture = -1;
  if (calPending != CAL_NONE) {
    Serial.println("[SOIL] 보정 측정 취소 (ADS1115 응답 없음)");
    calPending = CAL_NONE;
  }
  lastProbeMs = millis();
}

int computeMoisture(int rawValue) {
  if (rawValue < 0 || airVal == waterVal) return -1;
  long m = map(rawValue, airVal, waterVal, 0, 100);
  return static_cast<int>(constrain(m, 0L, 100L));
}

bool inRange(int v) { return v >= 0 && v <= SOIL_RAW_MAX; }

void persist() {
  prefs.putInt("air", airVal);
  prefs.putInt("water", waterVal);
}

void applyAndReport(const char* what) {
  lastMoisture = computeMoisture(lastRaw);
  Serial.printf("[SOIL] %s\n", what);
  printCal();
}

}  // namespace

// ──────────────────────────────────────────────────── 공개 API

void begin() {
  prefs.begin(SOIL_NVS_NAMESPACE, false);
  const bool fromNvs = prefs.isKey("air") || prefs.isKey("water");
  airVal   = prefs.getInt("air",   SOIL_AIR_VALUE);
  waterVal = prefs.getInt("water", SOIL_WATER_VALUE);
  if (!inRange(airVal) || !inRange(waterVal)) {   // 손상된 저장값 방어
    airVal = SOIL_AIR_VALUE;
    waterVal = SOIL_WATER_VALUE;
  }

#if ENABLE_SOIL
  // I2C 버스는 .ino 의 setup() 이 한 번 엽니다(ADS1115·PCA9548A·SHT45·BH1750 공유, 2026-09-29).
  adsOk = probe();
  Serial.printf("[SOIL] ADS1115 0x%02X (SDA GPIO%u / SCL GPIO%u) %s | AIN%u, ±4.096V(0.125mV), 250SPS, %u회 평균\n",
                static_cast<unsigned>(ADS1115_ADDR),
                static_cast<unsigned>(PIN_I2C_SDA), static_cast<unsigned>(PIN_I2C_SCL),
                adsOk ? "응답" : "응답 없음 — 5초마다 다시 찾음",
                static_cast<unsigned>(ADS1115_CHANNEL), static_cast<unsigned>(SOIL_SAMPLE_COUNT));
  if (adsOk) restartSampling();
  else       lastProbeMs = millis();
#else
  Serial.println("[SOIL] ENABLE_SOIL=0 — 센서 기능이 꺼져 있습니다");
#endif
  Serial.printf("[SOIL] 보정값 출처: %s\n", fromNvs ? "NVS(저장된 실측값)" : "config.h 초기값");
  printCal();
}

bool tick() {
#if !ENABLE_SOIL
  return false;
#else
  const uint32_t now = millis();

  if (!adsOk) {
    if (now - lastProbeMs < ADS1115_RETRY_MS) return false;
    lastProbeMs = now;
    if (!probe()) return false;
    adsOk = true;
    Serial.println("[SOIL] ADS1115 응답 — 측정 재개");
    restartSampling();
    return false;
  }

  if (phase == PH_IDLE) {
    if (now - lastSampleMs < SOIL_SAMPLE_GAP_MS) return false;
    lastSampleMs = now;
    if (!startConversion()) { markFault("변환 시작 실패"); return false; }
    phase = PH_CONVERTING;
    phaseMs = now;
    return false;
  }

  // PH_CONVERTING
  if (now - phaseMs < ADS1115_CONV_WAIT_MS) return false;
  bool done = false;
  if (!conversionDone(&done)) { markFault("설정 레지스터 읽기 실패"); return false; }
  if (!done) {
    if (now - phaseMs > ADS1115_CONV_TIMEOUT_MS) markFault("변환 완료 대기 시간 초과");
    return false;
  }
  int v = 0;
  if (!readResult(&v)) { markFault("변환값 읽기 실패"); return false; }
  phase = PH_IDLE;

  accum += v;
  sampleIndex++;
  if (sampleIndex < SOIL_SAMPLE_COUNT) return false;

  // 반올림 평균 (우노와 동일)
  const int avg = static_cast<int>((accum + SOIL_SAMPLE_COUNT / 2) / SOIL_SAMPLE_COUNT);
  accum = 0;
  sampleIndex = 0;
  lastRaw = avg;

  // 보정 대기 중이었다면 이 평균을 저장 (우노 updateSoilSampler 와 동일)
  if (calPending == CAL_AIR) {
    airVal = avg;
    persist();
    calPending = CAL_NONE;
    calDoneLatch = true;
    Serial.printf("[SOIL] AIR_VALUE measured: %d\n", airVal);
    printCal();
  } else if (calPending == CAL_WATER) {
    waterVal = avg;
    persist();
    calPending = CAL_NONE;
    calDoneLatch = true;
    Serial.printf("[SOIL] WATER_VALUE measured: %d\n", waterVal);
    printCal();
  }

  lastMoisture = computeMoisture(avg);
  return true;
#endif
}

bool setCalibration(int airValue, int waterValue) {
  if (!inRange(airValue) || !inRange(waterValue)) {
    Serial.printf("[SOIL][거부] 범위 밖 (0~%d): air=%d water=%d\n", SOIL_RAW_MAX, airValue, waterValue);
    return false;
  }
  if (airValue == waterValue) {
    Serial.println("[SOIL][거부] air 와 water 가 같아 보정값을 바꾸지 않습니다");
    return false;
  }
  airVal = airValue;
  waterVal = waterValue;
  calPending = CAL_NONE;
  persist();
  applyAndReport("보정값 변경 (직접 지정)");
  return true;
}

bool setAir(int value) {
  if (!inRange(value)) {
    Serial.printf("[SOIL][거부] 범위 밖 (0~%d): air=%d\n", SOIL_RAW_MAX, value);
    return false;
  }
  if (value == waterVal) {
    Serial.println("[SOIL][거부] air 가 water 와 같아집니다");
    return false;
  }
  airVal = value;
  calPending = CAL_NONE;
  persist();
  applyAndReport("AIR_VALUE set");
  return true;
}

bool setWater(int value) {
  if (!inRange(value)) {
    Serial.printf("[SOIL][거부] 범위 밖 (0~%d): water=%d\n", SOIL_RAW_MAX, value);
    return false;
  }
  if (value == airVal) {
    Serial.println("[SOIL][거부] water 가 air 와 같아집니다");
    return false;
  }
  waterVal = value;
  calPending = CAL_NONE;
  persist();
  applyAndReport("WATER_VALUE set");
  return true;
}

bool startCalibration(bool air) {
#if !ENABLE_SOIL
  (void)air;
  return false;
#else
  if (!adsOk) {
    Serial.println("[SOIL][거부] ADS1115 응답 없음 — 보정 측정 불가");
    return false;
  }
  calPending = air ? CAL_AIR : CAL_WATER;
  restartSampling();
  Serial.println(air ? "[SOIL] Measuring AIR (keep sensor in air)..."
                     : "[SOIL] Measuring WATER (keep sensor in water)...");
  return true;
#endif
}

bool calibrating()            { return calPending != CAL_NONE; }
const char* calibratingWhat() { return calPending == CAL_AIR ? "air" : (calPending == CAL_WATER ? "water" : ""); }

bool pollCalibrationDone() {
  if (!calDoneLatch) return false;
  calDoneLatch = false;
  return true;
}

void resetCalibration() {
  airVal = SOIL_AIR_VALUE;
  waterVal = SOIL_WATER_VALUE;
  calPending = CAL_NONE;
  prefs.clear();
  applyAndReport("보정값을 config.h 초기값으로 되돌림 (NVS 삭제)");
}

bool ok()         { return adsOk; }
int  raw()        { return lastRaw; }
int  millivolts() { return (lastRaw < 0) ? -1 : static_cast<int>(lastRaw * SOIL_MV_PER_LSB + 0.5f); }
int  moisture()   { return lastMoisture; }
bool calibrated() { return airVal != waterVal; }
int  airValue()   { return airVal; }
int  waterValue() { return waterVal; }

void printCal() {
  Serial.printf("[SOIL] cal AIR_VALUE=%d  WATER_VALUE=%d  (default %d / %d)\n",
                airVal, waterVal, SOIL_AIR_VALUE, SOIL_WATER_VALUE);
  int span = airVal - waterVal;
  if (span < 0) span = -span;
  if (span == 0) {
    Serial.println("  !! AIR_VALUE == WATER_VALUE : % cannot be computed");
  } else if (span < SOIL_MIN_SPAN) {
    Serial.println("  !! span too small : check sensor / wiring, re-run ca / cw");
  }
}

void reportSerial() {
#if ENABLE_SOIL
  if (!adsOk) {
    Serial.println("[SOIL] ADS1115 응답 없음");
  } else if (lastRaw < 0) {
    Serial.println("[SOIL] 측정 대기 중");
  } else {
    const int mv = millivolts();
    Serial.printf("[SOIL] raw=%d (%d.%03dV) | 상대 수분=", lastRaw, mv / 1000, mv % 1000);
    if (lastMoisture < 0) Serial.println("미보정");
    else                  Serial.printf("%d%%\n", lastMoisture);
  }
  if (calPending != CAL_NONE) Serial.printf("[SOIL] 보정 측정 중: %s\n", calibratingWhat());
#endif
}

}  // namespace soil
