#include "i2c_mux.h"
#include "config.h"

#include <Wire.h>

namespace mux {
namespace {

bool     found = false;
uint8_t  cur = 0xFF;
uint32_t lastProbeMs = 0;

bool probe() {
  Wire.beginTransmission(I2C_MUX_ADDR);
  return Wire.endTransmission() == 0;
}

bool writeMask(uint8_t mask) {
  Wire.beginTransmission(I2C_MUX_ADDR);
  Wire.write(mask);
  if (Wire.endTransmission() != 0) {
    if (found) Serial.println("[MUX][오류] PCA9548A 응답 없음 — 다시 찾을 때까지 SHT45 값 없음");
    found = false;
    cur = 0xFF;
    lastProbeMs = millis();
    return false;
  }
  return true;
}

}  // namespace

void begin() {
  found = probe();
  lastProbeMs = millis();
  if (found) {
    writeMask(0x00);
    cur = 0xFF;
  }
  Serial.printf("[MUX] PCA9548A 0x%02X %s\n", static_cast<unsigned>(I2C_MUX_ADDR),
                found ? "응답 — SHT45 는 채널 경유" : "응답 없음 — SHT45 는 버스 직결 1개로 시도, 5초마다 다시 찾음");
}

void tick() {
  if (found) return;
  const uint32_t now = millis();
  if (now - lastProbeMs < I2C_MUX_RETRY_MS) return;
  lastProbeMs = now;
  if (!probe()) return;
  found = true;
  writeMask(0x00);
  cur = 0xFF;
  Serial.println("[MUX] PCA9548A 응답 — 채널 경유로 전환");
}

bool present() { return found; }

bool select(uint8_t channel) {
  if (!found || channel > 7) return false;
  if (cur == channel) return true;
  if (!writeMask(static_cast<uint8_t>(1u << channel))) return false;
  cur = channel;
  return true;
}

bool deselectAll() {
  if (!found) return false;
  if (!writeMask(0x00)) return false;
  cur = 0xFF;
  return true;
}

uint8_t current() { return cur; }

void reportSerial() {
  if (!found) { Serial.println("[MUX] PCA9548A 없음 (SHT45 직결 모드)"); return; }
  if (cur == 0xFF) Serial.println("[MUX] PCA9548A 응답 | 켜진 채널 없음");
  else             Serial.printf("[MUX] PCA9548A 응답 | 채널 %u 켜짐\n", static_cast<unsigned>(cur));
}

}  // namespace mux
