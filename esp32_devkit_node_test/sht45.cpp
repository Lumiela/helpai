#include "sht45.h"
#include "config.h"
#include "i2c_mux.h"

#include <Wire.h>
#include <math.h>

/*
 * 2026-09-29 저녁 보완: 첫 실기에서 0x44 가 주소 스캔에는 응답했지만 측정값이 끝내 안 나왔다(sht45_count 0).
 * SHT30/31 도 주소가 0x44 라 모델이 다를 가능성이 있어, 자리마다 모델을 판별해 맞는 명령을 쓰고
 * 실패할 때마다 어느 단계에서 왜 실패했는지 시리얼에 남긴다.
 *
 *   SHT4x : 일련번호 0x89 → 6바이트(CRC 2개). 측정 0xFD → 10ms → 6바이트. RH = -6 + 125·raw/65535
 *   SHT3x : 상태 0xF3 0x2D → 3바이트(CRC 1개). 측정 0x24 0x00 → 20ms → 6바이트. RH = 100·raw/65535
 *   온도는 둘 다 -45 + 175·raw/65535. CRC-8(0x31, 초기 0xFF)도 같다.
 */

namespace sht45 {
namespace {

enum Phase : uint8_t { PH_IDLE, PH_NEXT, PH_MEASURING };
enum Model : uint8_t { M_NONE, M_UNKNOWN, M_SHT4X, M_SHT3X };

struct Slot {
  bool     present = false;
  Model    model = M_NONE;
  uint8_t  fails = 0;
  uint32_t lastProbeMs = 0;
  float    temp = NAN;
  float    hum = NAN;
  uint32_t lastOkMs = 0;
  uint32_t serial = 0;      // SHT4x 일련번호 (판별 성공 시)
};

Slot     slots[SHT45_MAX];
bool     useMux = false;
Phase    phase = PH_IDLE;
uint8_t  idx = 0;
uint32_t roundStartMs = 0;
uint32_t cmdMs = 0;
char     lastErr[96] = "";

uint8_t crc8(const uint8_t* d, uint8_t n) {
  uint8_t crc = 0xFF;
  for (uint8_t i = 0; i < n; i++) {
    crc ^= d[i];
    for (uint8_t b = 0; b < 8; b++) crc = (crc & 0x80) ? static_cast<uint8_t>((crc << 1) ^ 0x31) : static_cast<uint8_t>(crc << 1);
  }
  return crc;
}

const char* modelName(Model m) {
  switch (m) {
    case M_SHT4X: return "SHT4x";
    case M_SHT3X: return "SHT3x";
    case M_UNKNOWN: return "?";
    default: return "";
  }
}

// 그 자리의 센서가 보이게 채널을 켭니다. 직결 모드면 할 일 없음.
bool route(uint8_t slot) {
  if (!useMux) return slot == 0;
  return mux::select(SHT45_MUX_CHANNELS[slot]);
}

bool probeAt(uint8_t slot) {
  if (!route(slot)) return false;
  Wire.beginTransmission(SHT45_ADDR);
  return Wire.endTransmission() == 0;
}

bool writeBytes(const uint8_t* b, uint8_t n) {
  Wire.beginTransmission(SHT45_ADDR);
  Wire.write(b, n);
  return Wire.endTransmission() == 0;
}

// n 바이트를 읽어 buf 에. 실패 사유를 lastErr 에.
bool readBytes(uint8_t* buf, uint8_t n) {
  const uint8_t got = static_cast<uint8_t>(Wire.requestFrom(static_cast<uint8_t>(SHT45_ADDR), n));
  if (got != n) {
    snprintf(lastErr, sizeof(lastErr), "읽기 %u/%u바이트(주소 NACK 또는 측정 중)", static_cast<unsigned>(got), static_cast<unsigned>(n));
    while (Wire.available()) Wire.read();
    return false;
  }
  for (uint8_t i = 0; i < n; i++) buf[i] = Wire.read();
  return true;
}

// 모델 판별. 응답은 하는데 둘 다 아니면 M_UNKNOWN(SHT4x 명령으로 시도).
Model identify(uint8_t slot) {
  uint8_t d[6];
  // 감지 직후 소프트 리셋(SHT4x 0x94, 1ms). 전원 투입 중 어정쩡한 상태를 정리한다.
  {
    const uint8_t rst = 0x94;
    writeBytes(&rst, 1);
    delay(2);
  }
  // SHT4x 일련번호
  if (writeBytes(&SHT4X_CMD_SERIAL, 1)) {
    delay(2);
    if (readBytes(d, 6) && crc8(d, 2) == d[2] && crc8(d + 3, 2) == d[5]) {
      slots[slot].serial = (static_cast<uint32_t>(d[0]) << 24) | (static_cast<uint32_t>(d[1]) << 16) |
                           (static_cast<uint32_t>(d[3]) << 8) | d[4];
      return M_SHT4X;
    }
  }
  // SHT3x 상태 레지스터
  if (writeBytes(SHT3X_CMD_STATUS, 2)) {
    delay(2);
    if (readBytes(d, 3) && crc8(d, 2) == d[2]) return M_SHT3X;
  }
  return M_UNKNOWN;
}

bool sendMeasure(Model m) {
  if (m == M_SHT3X) return writeBytes(SHT3X_CMD_MEASURE, 2);
  return writeBytes(&SHT45_CMD_MEASURE, 1);
}

uint32_t measureWait(Model m) { return (m == M_SHT3X) ? SHT3X_MEAS_WAIT_MS : SHT45_MEAS_WAIT_MS; }

bool readResult(Model m, float* t, float* rh) {
  uint8_t d[6];
  if (!readBytes(d, 6)) return false;
  if (crc8(d, 2) != d[2] || crc8(d + 3, 2) != d[5]) {
    snprintf(lastErr, sizeof(lastErr), "CRC 불일치 (%02X %02X %02X | %02X %02X %02X)", d[0], d[1], d[2], d[3], d[4], d[5]);
    return false;
  }
  const uint16_t rawT  = static_cast<uint16_t>((d[0] << 8) | d[1]);
  const uint16_t rawRH = static_cast<uint16_t>((d[3] << 8) | d[4]);
  *t = -45.0f + 175.0f * rawT / 65535.0f;
  float h = (m == M_SHT3X) ? (100.0f * rawRH / 65535.0f) : (-6.0f + 125.0f * rawRH / 65535.0f);
  if (h < 0.0f) h = 0.0f;
  if (h > 100.0f) h = 100.0f;
  *rh = h;
  return true;
}

void markLost(uint8_t slot, const char* why) {
  Slot& s = slots[slot];
  if (s.present) {
    Serial.printf("[SHT45] #%u (ch%u) %s — 없음으로 처리, 5초마다 다시 찾음\n",
                  static_cast<unsigned>(slot + 1), static_cast<unsigned>(channelOf(slot)), why);
  }
  s.present = false;
  s.model = M_NONE;
  s.fails = 0;
  s.temp = NAN;
  s.hum = NAN;
  s.lastProbeMs = millis();
}

void fail(uint8_t slot, const char* step) {
  Slot& s = slots[slot];
  s.fails++;
  Serial.printf("[SHT45] #%u (ch%u, %s) %s 실패 %u/%u: %s\n",
                static_cast<unsigned>(slot + 1), static_cast<unsigned>(channelOf(slot)), modelName(s.model),
                step, static_cast<unsigned>(s.fails), static_cast<unsigned>(SHT45_MAX_FAILS), lastErr);
  if (s.fails >= SHT45_MAX_FAILS) markLost(slot, "연속 실패");
}

void found(uint8_t slot) {
  Slot& s = slots[slot];
  s.present = true;
  s.fails = 0;
  s.model = identify(slot);
  if (s.model == M_SHT4X) {
    Serial.printf("[SHT45] #%u (ch%u) 응답 — SHT4x 확인, 일련번호 %08lX — 측정 시작\n",
                  static_cast<unsigned>(slot + 1), static_cast<unsigned>(channelOf(slot)), static_cast<unsigned long>(s.serial));
  } else if (s.model == M_SHT3X) {
    Serial.printf("[SHT45] #%u (ch%u) 응답 — ★ SHT3x(SHT30/31) 로 판별, SHT3x 명령으로 측정\n",
                  static_cast<unsigned>(slot + 1), static_cast<unsigned>(channelOf(slot)));
  } else {
    Serial.printf("[SHT45] #%u (ch%u) 0x44 응답은 하나 SHT4x·SHT3x 판별 실패(%s) — SHT4x 명령으로 시도\n",
                  static_cast<unsigned>(slot + 1), static_cast<unsigned>(channelOf(slot)), lastErr);
  }
}

// 한 바퀴 안에서 다음 자리로. 없는 자리는 재탐색 시각이 됐으면 찾아보고 넘어갑니다.
void advance() {
  const uint32_t now = millis();
  while (idx < SHT45_MAX) {
    Slot& s = slots[idx];
    if (s.present) {
      if (route(idx) && sendMeasure(s.model)) {
        cmdMs = now;
        phase = PH_MEASURING;
        return;
      }
      snprintf(lastErr, sizeof(lastErr), "명령 NACK(채널 선택 또는 쓰기 실패)");
      fail(idx, "측정 명령");
      idx++;
      continue;
    }
    if (now - s.lastProbeMs >= SHT45_RETRY_MS) {
      s.lastProbeMs = now;
      if (probeAt(idx)) {
        found(idx);
        continue;   // 같은 자리에서 바로 측정
      }
    }
    if (!useMux) break;   // 직결 모드는 자리 하나뿐
    idx++;
  }
  // 바퀴 끝
  if (useMux) mux::deselectAll();
  phase = PH_IDLE;
}

}  // namespace

// ──────────────────────────────────────────────────── 공개 API

void begin() {
#if !ENABLE_SHT45
  Serial.println("[SHT45] ENABLE_SHT45=0 — 온습도 기능이 꺼져 있습니다");
  return;
#else
  useMux = mux::present();
  uint8_t n = 0;
  const uint8_t limit = useMux ? SHT45_MAX : 1;
  Serial.printf("[SHT45] 0x%02X %s | 고정밀, %lu ms 주기\n", static_cast<unsigned>(SHT45_ADDR),
                useMux ? "PCA9548A 채널 경유" : "버스 직결(멀티플렉서 없음)", static_cast<unsigned long>(SHT45_PERIOD_MS));
  for (uint8_t i = 0; i < limit; i++) {
    slots[i].lastProbeMs = millis();
    if (probeAt(i)) { found(i); n++; }
    else Serial.printf("[SHT45] #%u (ch%u) 응답 없음 — 5초마다 다시 찾음\n", static_cast<unsigned>(i + 1), static_cast<unsigned>(channelOf(i)));
  }
  if (useMux) mux::deselectAll();
  Serial.printf("[SHT45] 응답 %u개\n", static_cast<unsigned>(n));
  roundStartMs = millis() - SHT45_PERIOD_MS;   // 첫 바퀴는 바로
#endif
}

void tick() {
#if ENABLE_SHT45
  const uint32_t now = millis();

  // 멀티플렉서가 나중에 나타나면(재탐색 성공) 경유 모드로 전환
  if (!useMux && mux::present()) {
    useMux = true;
    Serial.println("[SHT45] 멀티플렉서 발견 — 채널 경유 모드로 전환, 자리 재탐색");
    for (uint8_t i = 0; i < SHT45_MAX; i++) markLost(i, "모드 전환");
    for (uint8_t i = 0; i < SHT45_MAX; i++) slots[i].lastProbeMs = now - SHT45_RETRY_MS;
    phase = PH_IDLE;
    roundStartMs = now - SHT45_PERIOD_MS;
  }

  switch (phase) {
    case PH_IDLE:
      if (now - roundStartMs < SHT45_PERIOD_MS) return;
      roundStartMs = now;
      idx = 0;
      phase = PH_NEXT;
      advance();
      return;

    case PH_NEXT:
      advance();
      return;

    case PH_MEASURING: {
      Slot& s = slots[idx];
      if (now - cmdMs < measureWait(s.model)) return;
      float t = NAN, rh = NAN;
      if (!route(idx)) {
        snprintf(lastErr, sizeof(lastErr), "채널 선택 실패");
        fail(idx, "읽기");
      } else if (readResult(s.model, &t, &rh)) {
        if (s.fails) Serial.printf("[SHT45] #%u 회복: %.1f°C %.1f%%\n", static_cast<unsigned>(idx + 1), t, rh);
        s.temp = t;
        s.hum = rh;
        s.fails = 0;
        s.lastOkMs = now;
      } else {
        fail(idx, "읽기");
      }
      idx++;
      phase = PH_NEXT;
      return;
    }
  }
#endif
}

bool    viaMux()                 { return useMux; }
bool    ok(uint8_t slot)         { return slot < SHT45_MAX && slots[slot].present && !isnan(slots[slot].temp); }
float   temperature(uint8_t slot){ return ok(slot) ? slots[slot].temp : NAN; }
float   humidity(uint8_t slot)   { return ok(slot) ? slots[slot].hum : NAN; }
uint8_t channelOf(uint8_t slot)  { return slot < SHT45_MAX ? SHT45_MUX_CHANNELS[slot] : 0xFF; }
const char* model(uint8_t slot)  { return slot < SHT45_MAX ? modelName(slots[slot].model) : ""; }

const char* modelSummary() {
  for (uint8_t i = 0; i < SHT45_MAX; i++) if (slots[i].present) return modelName(slots[i].model);
  return "none";
}

uint8_t count() {
  uint8_t n = 0;
  for (uint8_t i = 0; i < SHT45_MAX; i++) if (ok(i)) n++;
  return n;
}

bool anyOk() { return count() > 0; }

float avgTemperature() {
  float sum = 0; uint8_t n = 0;
  for (uint8_t i = 0; i < SHT45_MAX; i++) if (ok(i)) { sum += slots[i].temp; n++; }
  return n ? sum / n : NAN;
}

float avgHumidity() {
  float sum = 0; uint8_t n = 0;
  for (uint8_t i = 0; i < SHT45_MAX; i++) if (ok(i)) { sum += slots[i].hum; n++; }
  return n ? sum / n : NAN;
}

void reportSerial() {
#if ENABLE_SHT45
  Serial.printf("[SHT45] %s | 응답 %u개", useMux ? "채널 경유" : "직결", static_cast<unsigned>(count()));
  for (uint8_t i = 0; i < (useMux ? SHT45_MAX : 1); i++) {
    if (ok(i))                  Serial.printf(" | #%u ch%u %s %.1f°C %.1f%%", static_cast<unsigned>(i + 1), static_cast<unsigned>(channelOf(i)), modelName(slots[i].model), slots[i].temp, slots[i].hum);
    else if (slots[i].present)  Serial.printf(" | #%u ch%u %s 응답은 하나 값 없음(실패 %u회)", static_cast<unsigned>(i + 1), static_cast<unsigned>(channelOf(i)), modelName(slots[i].model), static_cast<unsigned>(slots[i].fails));
    else                        Serial.printf(" | #%u ch%u --", static_cast<unsigned>(i + 1), static_cast<unsigned>(channelOf(i)));
  }
  if (anyOk()) Serial.printf(" | 평균 %.1f°C %.1f%%", avgTemperature(), avgHumidity());
  if (lastErr[0]) Serial.printf(" | 마지막 오류: %s", lastErr);
  Serial.println();
#endif
}

}  // namespace sht45
