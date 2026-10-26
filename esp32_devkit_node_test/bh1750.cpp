#include "bh1750.h"
#include "config.h"
#include "i2c_mux.h"

#include <Wire.h>
#include <math.h>

/*
 * 2026-10-23 (cube-devkit-1.6.2, 사용자 결정): 멀티플렉서 채널 자동 탐색.
 *   자리 i 가 고정 채널표(BH1750_MUX_CHANNELS[i])에 묶여 있던 것을, 부팅 때 8채널(0~7)을 훑어 0x23 또는 0x5C 가 응답하는
 *   (채널, 주소) 짝을 채널 낮은 순으로 자리 1·2 에 배정하는 방식으로 바꿨다. 종류 판별은 "응답 주소"(0x23/0x5C = 조도)
 *   + 전원 켜기·연속 측정 명령을 받아들이는지 확인. 같은 채널에 ADDR 가 다른 둘(0x23·0x5C)이 있으면 둘 다 자리가 된다.
 *   · 배정된 자리는 (채널, 주소)를 기억한다. 가동 중 센서가 빠지면 자리는 비우되(값 null) 번호를 당기지 않고, 5초마다 그 짝을
 *     먼저 다시 찾아 같은 자리로 되돌린다.
 *   · 새 짝이 나타나면 빈 자리 중 첫째에 넣는다(기억이 없는 자리 → 없으면 잃은 자리).
 *   · BH1750_MAX(2)개를 넘는 센서는 로그 한 번 남기고 무시한다. 자리가 비면 다음 탐색에서 다시 후보가 된다.
 *   · 멀티플렉서가 없으면 버스 직결(채널 = I2C_MUX_CH_NONE)로 같은 논리 — 주소가 달라야 2개.
 */

namespace light {
namespace {

struct Slot {
  uint8_t  addr = 0;                    // 배정된(또는 잃은 뒤 기억하는) 주소. 0 = 기억 없음
  uint8_t  channel = I2C_MUX_CH_NONE;   // 배정된(또는 기억하는) 멀티플렉서 채널. 직결 모드 = I2C_MUX_CH_NONE
  bool     found = false;
  uint8_t  fails = 0;
  float    lastLux = NAN;
  uint32_t startedMs = 0;     // 연속 측정 시작 시각 (첫 읽기는 BH1750_FIRST_WAIT_MS 뒤)
  uint32_t lastReadMs = 0;
};

Slot     slots[BH1750_MAX];
bool     useMux = false;
uint32_t lastScanMs = 0;
uint16_t ignoredMask = 0;   // 자리 초과로 무시한 (채널 번호 × 2 + 주소 번호) 비트. 로그를 한 번만 내기 위한 표시. 자리가 비면 0 으로
char     csvBuf[BH1750_MAX * 4 + 1];

// 자리 표시용 ("ch4" / "ch?"(미배정) / "직결")
const char* whereText(uint8_t slot, char* buf, size_t cap) {
  if (!useMux)                                     snprintf(buf, cap, "직결");
  else if (slots[slot].channel == I2C_MUX_CH_NONE) snprintf(buf, cap, "ch?");
  else                                             snprintf(buf, cap, "ch%u", static_cast<unsigned>(slots[slot].channel));
  return buf;
}

// 채널 ch 를 켭니다. 직결 모드면 할 일 없음.
bool routeCh(uint8_t ch) {
  if (!useMux) return true;
  if (ch == I2C_MUX_CH_NONE) return false;
  return mux::select(ch);
}

bool route(uint8_t slot) { return routeCh(slots[slot].channel); }

bool writeCommand(uint8_t a, uint8_t cmd) {
  Wire.beginTransmission(a);
  Wire.write(cmd);
  return Wire.endTransmission() == 0;
}

bool probe(uint8_t a) {
  Wire.beginTransmission(a);
  return Wire.endTransmission() == 0;
}

// 지금 켜진 채널의 주소 a 에 Power On → 연속 H-Resolution 을 겁니다(응답 주소 + 명령 수락 = 조도 센서로 확정).
bool startMeasure(uint8_t a) {
  if (!probe(a)) return false;
  return writeCommand(a, BH1750_CMD_POWER_ON) && writeCommand(a, BH1750_CMD_CONT_HRES);
}

void assign(uint8_t slot, uint8_t ch, uint8_t a) {
  Slot& s = slots[slot];
  s.addr = a;
  s.channel = ch;
  s.found = true;
  s.fails = 0;
  s.lastLux = NAN;
  s.startedMs = millis();
  s.lastReadMs = s.startedMs;
}

// 응답 중인 자리가 이 (채널, 주소) 짝을 쓰는지
bool pairInUse(uint8_t ch, uint8_t a) {
  for (uint8_t i = 0; i < BH1750_MAX; i++) if (slots[i].found && slots[i].channel == ch && slots[i].addr == a) return true;
  return false;
}

// 잃은 자리가 이 짝을 기억하는지 (1단계에서 이미 물어본 짝)
bool pairRemembered(uint8_t ch, uint8_t a) {
  for (uint8_t i = 0; i < BH1750_MAX; i++) if (!slots[i].found && slots[i].addr == a && slots[i].channel == ch) return true;
  return false;
}

// 새 센서를 넣을 자리: 기억이 없는 빈 자리 → 없으면 잃은 자리 중 첫째. -1 = 자리 없음
int8_t pickSlot() {
  for (uint8_t i = 0; i < BH1750_MAX; i++) if (!slots[i].found && slots[i].addr == 0) return static_cast<int8_t>(i);
  for (uint8_t i = 0; i < BH1750_MAX; i++) if (!slots[i].found) return static_cast<int8_t>(i);
  return -1;
}

bool hasEmptySlot() {
  for (uint8_t i = 0; i < BH1750_MAX; i++) if (!slots[i].found) return true;
  return false;
}

// 탐색. 반환 = 이번에 새로 찾은 수.
//   1단계: 잃은 자리가 기억하는 (채널, 주소)를 먼저 물어 같은 자리로 복귀.
//   2단계: 채널 낮은 번호부터(직결 모드는 버스 한 번) 0x23 → 0x5C 순으로 훑어 응답하면 빈 자리에 배정. 자리가 없으면 무시(짝당 로그 한 번).
uint8_t scan() {
  uint8_t n = 0;
  char w[8];
  for (uint8_t i = 0; i < BH1750_MAX; i++) {
    Slot& s = slots[i];
    if (s.found || s.addr == 0) continue;
    if (routeCh(s.channel) && startMeasure(s.addr)) {
      assign(i, s.channel, s.addr);
      Serial.printf("[BH1750] #%u (%s) 0x%02X 응답 — 같은 자리로 복귀, 측정 재개\n", static_cast<unsigned>(i + 1), whereText(i, w, sizeof(w)), static_cast<unsigned>(s.addr));
      n++;
    }
  }
  const uint8_t chCount = useMux ? I2C_MUX_CH_COUNT : 1;
  for (uint8_t ci = 0; ci < chCount; ci++) {
    const uint8_t ch = useMux ? ci : I2C_MUX_CH_NONE;
    if (!routeCh(ch)) continue;
    for (uint8_t ai = 0; ai < BH1750_ADDR_COUNT; ai++) {
      const uint8_t a = BH1750_ADDRS[ai];
      if (pairInUse(ch, a) || pairRemembered(ch, a)) continue;
      if (!probe(a)) continue;
      const int8_t slot = pickSlot();
      if (slot < 0) {
        const uint16_t bit = static_cast<uint16_t>(1u << (ci * 2 + ai));
        if (!(ignoredMask & bit)) {
          ignoredMask |= bit;
          if (useMux) Serial.printf("[BH1750] 채널 %u 에 0x%02X 응답 — 자리 %u개를 넘어 무시(자리가 비면 다시 후보)\n",
                                    static_cast<unsigned>(ch), static_cast<unsigned>(a), static_cast<unsigned>(BH1750_MAX));
          else        Serial.printf("[BH1750] 버스에 0x%02X 응답 — 자리 %u개를 넘어 무시\n", static_cast<unsigned>(a), static_cast<unsigned>(BH1750_MAX));
        }
        continue;
      }
      if (!startMeasure(a)) continue;   // 응답은 하나 명령을 안 받음 → 조도 센서 아님
      assign(static_cast<uint8_t>(slot), ch, a);
      Serial.printf("[BH1750] #%u (%s) 0x%02X 응답 — 측정 시작\n", static_cast<unsigned>(slot + 1), whereText(static_cast<uint8_t>(slot), w, sizeof(w)), static_cast<unsigned>(a));
      n++;
    }
  }
  if (useMux) mux::deselectAll();
  return n;
}

// 자리를 비웁니다. (채널, 주소)는 기억해 두어 재탐색 때 같은 자리로 되돌릴 수 있게 합니다.
void markLost(uint8_t slot, const char* why) {
  Slot& s = slots[slot];
  char w[8];
  if (s.found) {
    Serial.printf("[BH1750] #%u (%s) %s — 없음으로 처리, 5초마다 다시 찾음(같은 채널·주소면 같은 자리로)\n",
                  static_cast<unsigned>(slot + 1), whereText(slot, w, sizeof(w)), why);
  }
  s.found = false;
  s.fails = 0;
  s.lastLux = NAN;
  ignoredMask = 0;   // 자리가 비었으니 무시했던 짝도 다시 후보
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
  Serial.printf("[BH1750] 조도 최대 %u개, %s | 연속 H-Resolution(1 lx), %lu ms 마다 읽음\n", static_cast<unsigned>(BH1750_MAX),
                useMux ? "PCA9548A 채널 자동 탐색(0~7, 0x23·0x5C, 자리 = 채널 낮은 순)" : "버스 직결(멀티플렉서 없음 — 주소가 달라야 2개)",
                static_cast<unsigned long>(BH1750_PERIOD_MS));
  lastScanMs = millis();
  const uint8_t n = scan();
  if (n == 0) Serial.println("[BH1750] 0x23·0x5C 응답 없음 — 5초마다 다시 찾음");
  else        Serial.printf("[BH1750] 응답 %u개 (자리 배정 %s)\n", static_cast<unsigned>(n), channelListCsv());
#endif
}

void tick() {
#if ENABLE_BH1750
  const uint32_t now = millis();

  // 멀티플렉서가 나중에 나타나면(재탐색 성공) 경유 모드로 전환하고 자리를 다시 찾습니다
  if (!useMux && mux::present()) {
    useMux = true;
    Serial.println("[BH1750] 멀티플렉서 발견 — 채널 경유 모드로 전환, 채널 자동 탐색");
    for (uint8_t i = 0; i < BH1750_MAX; i++) {
      markLost(i, "모드 전환");
      slots[i].addr = 0;
      slots[i].channel = I2C_MUX_CH_NONE;
    }
    lastScanMs = now - BH1750_RETRY_MS;   // 바로 탐색
  }

  if (hasEmptySlot() && now - lastScanMs >= BH1750_RETRY_MS) {
    lastScanMs = now;
    scan();
  }

  for (uint8_t i = 0; i < BH1750_MAX; i++) {
    Slot& s = slots[i];
    if (!s.found) continue;
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
uint8_t channelOf(uint8_t slot) { return (slot < BH1750_MAX && slots[slot].found) ? slots[slot].channel : I2C_MUX_CH_NONE; }

// 자리 순서로 채널을 쉼표로 이어 붙인 문자열. 응답 중인 자리 = 채널 번호(직결이면 "bus"), 빈 자리 = "-". 예: "4,-"
const char* channelListCsv() {
  size_t pos = 0;
  for (uint8_t i = 0; i < BH1750_MAX && pos < sizeof(csvBuf) - 1; i++) {
    if (i) csvBuf[pos++] = ',';
    const Slot& s = slots[i];
    int w;
    if (!s.found)     w = snprintf(csvBuf + pos, sizeof(csvBuf) - pos, "-");
    else if (!useMux) w = snprintf(csvBuf + pos, sizeof(csvBuf) - pos, "bus");
    else              w = snprintf(csvBuf + pos, sizeof(csvBuf) - pos, "%u", static_cast<unsigned>(s.channel));
    if (w < 0) break;
    pos += static_cast<size_t>(w);
    if (pos >= sizeof(csvBuf) - 1) { pos = sizeof(csvBuf) - 1; break; }
  }
  csvBuf[pos] = '\0';
  return csvBuf;
}

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
  Serial.printf("[BH1750] %s | 값 %u개 | 자리 배정 %s", useMux ? "채널 자동 탐색" : "직결", static_cast<unsigned>(count()), channelListCsv());
  for (uint8_t i = 0; i < BH1750_MAX; i++) {
    const Slot& s = slots[i];
    if (ok(i))        Serial.printf(" | #%u %s 0x%02X %.1f lx", static_cast<unsigned>(i + 1), whereText(i, w, sizeof(w)), static_cast<unsigned>(s.addr), s.lastLux);
    else if (s.found) Serial.printf(" | #%u %s 0x%02X 첫 측정 대기(실패 %u회)", static_cast<unsigned>(i + 1), whereText(i, w, sizeof(w)), static_cast<unsigned>(s.addr), static_cast<unsigned>(s.fails));
    else if (s.addr)  Serial.printf(" | #%u %s 0x%02X 없음(그 자리를 5초마다 다시 찾음)", static_cast<unsigned>(i + 1), whereText(i, w, sizeof(w)), static_cast<unsigned>(s.addr));
    else              Serial.printf(" | #%u 빈 자리", static_cast<unsigned>(i + 1));
  }
  if (ok()) Serial.printf(" | 평균 %.1f lx", lux());
  Serial.println();
#endif
}

}  // namespace light
