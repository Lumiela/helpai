#include "bh1750.h"
#include "config.h"
#include "i2c_mux.h"

#include <Wire.h>
#include <math.h>

namespace light {
namespace {

struct Slot {
  uint8_t  addr = 0;
  bool     found = false;
  uint8_t  fails = 0;
  float    lastLux = NAN;
  uint32_t startedMs = 0;     // 연속 측정 시작 시각 (첫 읽기는 BH1750_FIRST_WAIT_MS 뒤)
  uint32_t lastReadMs = 0;
  uint32_t lastProbeMs = 0;
};

Slot slots[BH1750_MAX];
bool useMux = false;

uint8_t chOf(uint8_t slot) { return slot < BH1750_MAX ? BH1750_MUX_CHANNELS[slot] : 0xFF; }

// 자리 표시용 ("ch4" 또는 "직결")
const char* whereText(uint8_t slot, char* buf, size_t cap) {
  if (useMux) snprintf(buf, cap, "ch%u", static_cast<unsigned>(chOf(slot)));
  else        snprintf(buf, cap, "직결");
  return buf;
}

// 그 자리의 센서가 보이게 채널을 켭니다. 직결 모드면 할 일 없음.
bool route(uint8_t slot) {
  if (!useMux) return true;
  return mux::select(chOf(slot));
}

bool writeCommand(uint8_t a, uint8_t cmd) {
  Wire.beginTransmission(a);
  Wire.write(cmd);
  return Wire.endTransmission() == 0;
}

bool probe(uint8_t a) {
  Wire.beginTransmission(a);
  return Wire.endTransmission() == 0;
}

// 직결 모드에서 다른 자리가 이미 쓰는 주소인지 (한 주소는 한 자리만). 경유 모드는 채널이 달라 겹치지 않음.
bool takenByOther(uint8_t slot, uint8_t a) {
  if (useMux) return false;
  for (uint8_t i = 0; i < BH1750_MAX; i++) {
    if (i != slot && slots[i].found && slots[i].addr == a) return true;
  }
  return false;
}

// 자리의 주소를 찾고 Power On → 연속 H-Resolution 을 겁니다.
bool configure(uint8_t slot) {
  Slot& s = slots[slot];
  if (!route(slot)) return false;
  for (uint8_t i = 0; i < BH1750_ADDR_COUNT; i++) {
    const uint8_t a = BH1750_ADDRS[i];
    if (takenByOther(slot, a)) continue;
    if (!probe(a)) continue;
    if (!writeCommand(a, BH1750_CMD_POWER_ON) || !writeCommand(a, BH1750_CMD_CONT_HRES)) continue;
    s.addr = a;
    s.found = true;
    s.fails = 0;
    s.startedMs = millis();
    s.lastReadMs = s.startedMs;
    return true;
  }
  return false;
}

void markLost(uint8_t slot, const char* why) {
  Slot& s = slots[slot];
  char w[8];
  if (s.found) {
    Serial.printf("[BH1750] #%u (%s) %s — 없음으로 처리, 5초마다 다시 찾음\n",
                  static_cast<unsigned>(slot + 1), whereText(slot, w, sizeof(w)), why);
  }
  s.found = false;
  s.addr = 0;
  s.fails = 0;
  s.lastLux = NAN;
  s.lastProbeMs = millis();
}

bool readLux(uint8_t slot, float* out) {
  if (!route(slot)) return false;
  if (Wire.requestFrom(static_cast<uint8_t>(slots[slot].addr), static_cast<uint8_t>(2)) != 2) return false;
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
  useMux = mux::present();
  uint8_t n = 0;
  char w[8];
  Serial.printf("[BH1750] 조도 ×%u, %s | 연속 H-Resolution(1 lx), %lu ms 마다 읽음\n", static_cast<unsigned>(BH1750_MAX),
                useMux ? "PCA9548A 채널 경유" : "버스 직결(멀티플렉서 없음 — 자리마다 주소가 달라야 2개)",
                static_cast<unsigned long>(BH1750_PERIOD_MS));
  for (uint8_t i = 0; i < BH1750_MAX; i++) {
    slots[i].lastProbeMs = millis();
    if (configure(i)) {
      n++;
      Serial.printf("[BH1750] #%u (%s) 0x%02X 응답\n", static_cast<unsigned>(i + 1), whereText(i, w, sizeof(w)), static_cast<unsigned>(slots[i].addr));
    } else {
      Serial.printf("[BH1750] #%u (%s) 0x23·0x5C 응답 없음 — 5초마다 다시 찾음\n", static_cast<unsigned>(i + 1), whereText(i, w, sizeof(w)));
    }
  }
  if (useMux) mux::deselectAll();
  Serial.printf("[BH1750] 응답 %u개\n", static_cast<unsigned>(n));
#endif
}

void tick() {
#if ENABLE_BH1750
  const uint32_t now = millis();

  // 멀티플렉서가 나중에 나타나면(재탐색 성공) 경유 모드로 전환하고 자리를 다시 찾습니다
  if (!useMux && mux::present()) {
    useMux = true;
    Serial.println("[BH1750] 멀티플렉서 발견 — 채널 경유 모드로 전환, 자리 재탐색");
    for (uint8_t i = 0; i < BH1750_MAX; i++) {
      markLost(i, "모드 전환");
      slots[i].lastProbeMs = now - BH1750_RETRY_MS;
    }
  }

  char w[8];
  for (uint8_t i = 0; i < BH1750_MAX; i++) {
    Slot& s = slots[i];
    if (!s.found) {
      if (now - s.lastProbeMs < BH1750_RETRY_MS) continue;
      s.lastProbeMs = now;
      if (configure(i)) {
        Serial.printf("[BH1750] #%u (%s) 0x%02X 응답 — 측정 재개\n", static_cast<unsigned>(i + 1), whereText(i, w, sizeof(w)), static_cast<unsigned>(s.addr));
      }
      continue;
    }
    if (now - s.startedMs < BH1750_FIRST_WAIT_MS) continue;      // 첫 변환(최대 180ms)
    if (now - s.lastReadMs < BH1750_PERIOD_MS) continue;
    s.lastReadMs = now;
    float v = NAN;
    if (readLux(i, &v)) {
      s.lastLux = v;
      s.fails = 0;
    } else if (++s.fails >= BH1750_MAX_FAILS) {
      markLost(i, "읽기 실패 반복");
    }
  }
#endif
}

bool    viaMux()                { return useMux; }
bool    ok(uint8_t slot)        { return slot < BH1750_MAX && slots[slot].found && !isnan(slots[slot].lastLux); }
float   lux(uint8_t slot)       { return ok(slot) ? slots[slot].lastLux : NAN; }
uint8_t address(uint8_t slot)   { return (slot < BH1750_MAX && slots[slot].found) ? slots[slot].addr : 0; }
uint8_t channelOf(uint8_t slot) { return chOf(slot); }

uint8_t count() {
  uint8_t n = 0;
  for (uint8_t i = 0; i < BH1750_MAX; i++) if (ok(i)) n++;
  return n;
}

bool ok() { return count() > 0; }

float lux() {
  float sum = 0; uint8_t n = 0;
  for (uint8_t i = 0; i < BH1750_MAX; i++) if (ok(i)) { sum += slots[i].lastLux; n++; }
  return n ? sum / n : NAN;
}

uint8_t address() {
  for (uint8_t i = 0; i < BH1750_MAX; i++) if (slots[i].found) return slots[i].addr;
  return 0;
}

void reportSerial() {
#if ENABLE_BH1750
  char w[8];
  Serial.printf("[BH1750] %s | 값 %u개", useMux ? "채널 경유" : "직결", static_cast<unsigned>(count()));
  for (uint8_t i = 0; i < BH1750_MAX; i++) {
    const Slot& s = slots[i];
    if (ok(i))        Serial.printf(" | #%u %s 0x%02X %.1f lx", static_cast<unsigned>(i + 1), whereText(i, w, sizeof(w)), static_cast<unsigned>(s.addr), s.lastLux);
    else if (s.found) Serial.printf(" | #%u %s 0x%02X 첫 측정 대기(실패 %u회)", static_cast<unsigned>(i + 1), whereText(i, w, sizeof(w)), static_cast<unsigned>(s.addr), static_cast<unsigned>(s.fails));
    else              Serial.printf(" | #%u %s --", static_cast<unsigned>(i + 1), whereText(i, w, sizeof(w)));
  }
  if (ok()) Serial.printf(" | 평균 %.1f lx", lux());
  Serial.println();
#endif
}

}  // namespace light
