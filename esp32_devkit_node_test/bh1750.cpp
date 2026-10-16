#include "bh1750.h"
#include "config.h"

#include <Wire.h>
#include <math.h>

namespace light {
namespace {

uint8_t  addr = 0;
bool     found = false;
uint8_t  fails = 0;
float    lastLux = NAN;
uint32_t startedMs = 0;     // 연속 측정 시작 시각 (첫 읽기는 BH1750_FIRST_WAIT_MS 뒤)
uint32_t lastReadMs = 0;
uint32_t lastProbeMs = 0;

bool writeCommand(uint8_t a, uint8_t cmd) {
  Wire.beginTransmission(a);
  Wire.write(cmd);
  return Wire.endTransmission() == 0;
}

bool probe(uint8_t a) {
  Wire.beginTransmission(a);
  return Wire.endTransmission() == 0;
}

// 주소를 찾고 Power On → 연속 H-Resolution 을 겁니다.
bool configure() {
  for (uint8_t i = 0; i < BH1750_ADDR_COUNT; i++) {
    const uint8_t a = BH1750_ADDRS[i];
    if (!probe(a)) continue;
    if (!writeCommand(a, BH1750_CMD_POWER_ON) || !writeCommand(a, BH1750_CMD_CONT_HRES)) continue;
    addr = a;
    found = true;
    fails = 0;
    startedMs = millis();
    lastReadMs = startedMs;
    return true;
  }
  return false;
}

void markLost(const char* why) {
  if (found) Serial.printf("[BH1750] %s — 없음으로 처리, 5초마다 다시 찾음\n", why);
  found = false;
  addr = 0;
  fails = 0;
  lastLux = NAN;
  lastProbeMs = millis();
}

bool readLux(float* out) {
  if (Wire.requestFrom(static_cast<uint8_t>(addr), static_cast<uint8_t>(2)) != 2) return false;
  const uint16_t raw = static_cast<uint16_t>((Wire.read() << 8) | Wire.read());
  *out = raw / BH1750_COUNTS_PER_LUX;
  return true;
}

}  // namespace

// ──────────────────────────────────────────────────── 공개 API

void begin() {
#if !ENABLE_BH1750
  Serial.println("[BH1750] ENABLE_BH1750=0 — 조도 기능이 꺼져 있습니다");
  return;
#else
  lastProbeMs = millis();
  if (configure()) {
    Serial.printf("[BH1750] 0x%02X 응답 — 연속 H-Resolution(1 lx), %lu ms 마다 읽음\n",
                  static_cast<unsigned>(addr), static_cast<unsigned long>(BH1750_PERIOD_MS));
  } else {
    Serial.println("[BH1750] 0x23·0x5C 응답 없음 — 5초마다 다시 찾음");
  }
#endif
}

void tick() {
#if ENABLE_BH1750
  const uint32_t now = millis();
  if (!found) {
    if (now - lastProbeMs < BH1750_RETRY_MS) return;
    lastProbeMs = now;
    if (configure()) Serial.printf("[BH1750] 0x%02X 응답 — 측정 재개\n", static_cast<unsigned>(addr));
    return;
  }
  if (now - startedMs < BH1750_FIRST_WAIT_MS) return;      // 첫 변환(최대 180ms)
  if (now - lastReadMs < BH1750_PERIOD_MS) return;
  lastReadMs = now;
  float v = NAN;
  if (readLux(&v)) {
    lastLux = v;
    fails = 0;
  } else if (++fails >= BH1750_MAX_FAILS) {
    markLost("읽기 실패 반복");
  }
#endif
}

bool    ok()      { return found && !isnan(lastLux); }
float   lux()     { return ok() ? lastLux : NAN; }
uint8_t address() { return found ? addr : 0; }

void reportSerial() {
#if ENABLE_BH1750
  if (!found)             Serial.println("[BH1750] 없음 (0x23·0x5C 응답 없음)");
  else if (isnan(lastLux)) Serial.printf("[BH1750] 0x%02X 응답 | 첫 측정 대기\n", static_cast<unsigned>(addr));
  else                    Serial.printf("[BH1750] 0x%02X | %.1f lx\n", static_cast<unsigned>(addr), lastLux);
#endif
}

}  // namespace light
