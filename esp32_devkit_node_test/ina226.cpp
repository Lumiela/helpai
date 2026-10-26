#include "ina226.h"
#include "config.h"
#include "i2c_mux.h"

#include <Wire.h>
#include <math.h>

namespace ina226 {
namespace {

enum Reg : uint8_t { REG_CONFIG = 0x00, REG_SHUNT = 0x01, REG_BUS = 0x02, REG_MFR_ID = 0xFE, REG_DIE_ID = 0xFF };

// 자리 하나(INA226_MAX = 1). sht45/bh1750 과 같은 "기억 채널 우선 복귀" 규칙을 위해 채널·주소를 잃은 뒤에도 보관.
struct Slot {
  uint8_t  addr = 0;                    // 배정된(또는 기억하는) 주소. 0 = 기억 없음
  uint8_t  channel = I2C_MUX_CH_NONE;   // 배정된(또는 기억하는) 채널. 직결 = I2C_MUX_CH_NONE
  bool     found = false;
  uint8_t  fails = 0;
  uint16_t dieId = 0;
  float    busV = NAN;
  float    shuntMv = NAN;
  uint32_t lastReadMs = 0;
};

Slot     slot;
bool     useMux = false;
uint32_t lastScanMs = 0;
char     lastErr[64] = "";

const char* whereText(char* buf, size_t cap) {
  if (!useMux)                                snprintf(buf, cap, "직결");
  else if (slot.channel == I2C_MUX_CH_NONE)   snprintf(buf, cap, "ch?");
  else                                        snprintf(buf, cap, "ch%u", static_cast<unsigned>(slot.channel));
  return buf;
}

bool routeCh(uint8_t ch) {
  if (!useMux) return true;
  if (ch == I2C_MUX_CH_NONE) return false;
  return mux::select(ch);
}

bool skipAddr(uint8_t a) {
  for (uint8_t i = 0; i < INA226_ADDR_SKIP_COUNT; i++) if (INA226_ADDR_SKIP[i] == a) return true;
  return false;
}

bool probe(uint8_t a) {
  Wire.beginTransmission(a);
  return Wire.endTransmission() == 0;
}

// 16비트 레지스터 읽기(상위 바이트 먼저). 포인터 쓰기 → 반복 시작 → 2바이트.
bool readReg(uint8_t a, uint8_t reg, uint16_t* out) {
  Wire.beginTransmission(a);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) { snprintf(lastErr, sizeof(lastErr), "reg 0x%02X 포인터 NACK", reg); return false; }
  if (Wire.requestFrom(a, static_cast<uint8_t>(2)) != 2) {
    snprintf(lastErr, sizeof(lastErr), "reg 0x%02X 읽기 바이트 부족", reg);
    while (Wire.available()) Wire.read();
    return false;
  }
  const uint8_t hi = static_cast<uint8_t>(Wire.read());
  const uint8_t lo = static_cast<uint8_t>(Wire.read());
  *out = static_cast<uint16_t>((hi << 8) | lo);
  return true;
}

bool writeReg(uint8_t a, uint8_t reg, uint16_t v) {
  Wire.beginTransmission(a);
  Wire.write(reg);
  Wire.write(static_cast<uint8_t>(v >> 8));
  Wire.write(static_cast<uint8_t>(v & 0xFF));
  if (Wire.endTransmission() != 0) { snprintf(lastErr, sizeof(lastErr), "reg 0x%02X 쓰기 NACK", reg); return false; }
  return true;
}

// 지금 켜진 채널의 주소 a 가 INA226 인지: 제조사 ID + 칩 ID
bool identify(uint8_t a, uint16_t* dieOut) {
  uint16_t mfr = 0, die = 0;
  if (!readReg(a, REG_MFR_ID, &mfr) || mfr != INA226_MFR_ID) return false;
  if (!readReg(a, REG_DIE_ID, &die) || (die & INA226_DIE_ID_MASK) != INA226_DIE_ID) return false;
  *dieOut = die;
  return true;
}

void markLost(const char* why) {
  char w[8];
  if (slot.found) {
    Serial.printf("[INA226] (%s 0x%02X) %s — 없음으로 처리, 5초마다 다시 찾음(같은 채널·주소면 같은 자리로)\n",
                  whereText(w, sizeof(w)), static_cast<unsigned>(slot.addr), why);
  }
  slot.found = false;
  slot.fails = 0;
  slot.busV = NAN;
  slot.shuntMv = NAN;
}

bool assign(uint8_t ch, uint8_t a, uint16_t die) {
  if (!writeReg(a, REG_CONFIG, INA226_CONFIG)) return false;
  slot.addr = a;
  slot.channel = ch;
  slot.dieId = die;
  slot.found = true;
  slot.fails = 0;
  slot.busV = NAN;
  slot.shuntMv = NAN;
  slot.lastReadMs = millis();
  char w[8];
  Serial.printf("[INA226] (%s 0x%02X) 제조사 0x%04X · 칩 ID 0x%04X 확인 — 설정 0x%04X(평균 16회), 션트 %.4f Ω, %lu ms 마다 읽기\n",
                whereText(w, sizeof(w)), static_cast<unsigned>(a), static_cast<unsigned>(INA226_MFR_ID), static_cast<unsigned>(die),
                static_cast<unsigned>(INA226_CONFIG), static_cast<double>(INA226_SHUNT_OHM), static_cast<unsigned long>(INA226_PERIOD_MS));
  return true;
}

// 탐색. 1단계: 기억하는 (채널, 주소) 복귀. 2단계: 채널 낮은 번호부터(직결은 버스 한 번) 0x40~0x4F 를 훑어 ID 가 맞는 첫 장치를 배정.
bool scan() {
  uint16_t die = 0;
  if (!slot.found && slot.addr != 0) {
    if (routeCh(slot.channel) && probe(slot.addr) && identify(slot.addr, &die) && assign(slot.channel, slot.addr, die)) {
      if (useMux) mux::deselectAll();
      return true;
    }
  }
  const uint8_t chCount = useMux ? I2C_MUX_CH_COUNT : 1;
  for (uint8_t ci = 0; ci < chCount && !slot.found; ci++) {
    const uint8_t ch = useMux ? ci : I2C_MUX_CH_NONE;
    if (!routeCh(ch)) continue;
    for (uint8_t a = INA226_ADDR_FIRST; a <= INA226_ADDR_LAST; a++) {
      if (skipAddr(a)) continue;
      if (!slot.found && slot.addr == a && slot.channel == ch) continue;   // 1단계에서 이미 물어본 짝
      if (!probe(a)) continue;
      if (!identify(a, &die)) continue;   // 주소는 응답하지만 INA226 이 아님(다른 칩)
      if (assign(ch, a, die)) break;
    }
  }
  if (useMux) mux::deselectAll();
  return slot.found;
}

bool readOnce() {
  if (!routeCh(slot.channel)) { snprintf(lastErr, sizeof(lastErr), "채널 선택 실패"); return false; }
  uint16_t sh = 0, bus = 0;
  if (!readReg(slot.addr, REG_SHUNT, &sh)) return false;
  if (!readReg(slot.addr, REG_BUS, &bus)) return false;
  const int16_t shS = static_cast<int16_t>(sh);                 // 부호 있음(양방향)
  slot.shuntMv = shS * (INA226_SHUNT_LSB_V * 1000.0f);
  slot.busV    = (bus & 0x7FFF) * INA226_BUS_LSB_V;             // D15 는 항상 0
  return true;
}

}  // namespace

// ──────────────────────────────────────────────────── 공개 API

void begin() {
#if !ENABLE_INA226
  Serial.println("[INA226] ENABLE_INA226=0 — 전압·전류 기능이 꺼져 있습니다");
  return;
#else
  useMux = mux::present();
  Serial.printf("[INA226] 0x%02X~0x%02X(0x44·0x48 제외) %s | 제조사·칩 ID 로 확인, 최대 %u개\n",
                static_cast<unsigned>(INA226_ADDR_FIRST), static_cast<unsigned>(INA226_ADDR_LAST),
                useMux ? "PCA9548A 채널 자동 탐색(0~7)" : "버스 직결(멀티플렉서 없음)", static_cast<unsigned>(INA226_MAX));
  lastScanMs = millis();
  if (!scan()) Serial.println("[INA226] 응답 없음 — 5초마다 다시 찾음");
#endif
}

void tick() {
#if ENABLE_INA226
  const uint32_t now = millis();

  if (!useMux && mux::present()) {
    useMux = true;
    Serial.println("[INA226] 멀티플렉서 발견 — 채널 경유 모드로 전환, 채널 자동 탐색");
    markLost("모드 전환");
    slot.addr = 0;
    slot.channel = I2C_MUX_CH_NONE;
    lastScanMs = now - INA226_RETRY_MS;
  }

  if (!slot.found) {
    if (now - lastScanMs < INA226_RETRY_MS) return;
    lastScanMs = now;
    scan();
    return;
  }

  if (now - slot.lastReadMs < INA226_PERIOD_MS) return;
  slot.lastReadMs = now;
  if (readOnce()) {
    if (slot.fails) Serial.printf("[INA226] 회복: %.2f V %.3f A\n", static_cast<double>(busVoltage()), static_cast<double>(current()));
    slot.fails = 0;
  } else if (++slot.fails >= INA226_MAX_FAILS) {
    markLost("읽기 실패 반복");
  } else {
    Serial.printf("[INA226] 읽기 실패 %u/%u: %s\n", static_cast<unsigned>(slot.fails), static_cast<unsigned>(INA226_MAX_FAILS), lastErr);
  }
  if (useMux) mux::deselectAll();
#endif
}

bool    viaMux()          { return useMux; }
bool    ok()              { return slot.found && !isnan(slot.busV); }
float   busVoltage()      { return ok() ? slot.busV : NAN; }
float   shuntMillivolts() { return ok() ? slot.shuntMv : NAN; }
float   current()         { return ok() ? (slot.shuntMv / 1000.0f) / INA226_SHUNT_OHM : NAN; }
uint8_t address()         { return slot.found ? slot.addr : 0; }
uint8_t channelOf()       { return slot.found ? slot.channel : I2C_MUX_CH_NONE; }
int     channelForStatus() {
  if (!slot.found) return -1;
  if (!useMux) return -2;
  return static_cast<int>(slot.channel);
}

void reportSerial() {
#if ENABLE_INA226
  char w[8];
  if (ok()) {
    Serial.printf("[INA226] %s 0x%02X (칩 ID 0x%04X) | 버스 %.2f V | 션트 %.3f mV | 전류 %.3f A (션트 %.4f Ω)\n",
                  whereText(w, sizeof(w)), static_cast<unsigned>(slot.addr), static_cast<unsigned>(slot.dieId),
                  static_cast<double>(slot.busV), static_cast<double>(slot.shuntMv), static_cast<double>(current()),
                  static_cast<double>(INA226_SHUNT_OHM));
  } else if (slot.found) {
    Serial.printf("[INA226] %s 0x%02X 응답은 하나 값 없음(실패 %u회) | 마지막 오류: %s\n",
                  whereText(w, sizeof(w)), static_cast<unsigned>(slot.addr), static_cast<unsigned>(slot.fails), lastErr);
  } else if (slot.addr) {
    Serial.printf("[INA226] %s 0x%02X 없음(그 자리를 5초마다 다시 찾음)\n", whereText(w, sizeof(w)), static_cast<unsigned>(slot.addr));
  } else {
    Serial.println("[INA226] 없음 (0x40~0x4F 에 제조사·칩 ID 가 맞는 장치 없음, 5초마다 다시 찾음)");
  }
#endif
}

}  // namespace ina226
