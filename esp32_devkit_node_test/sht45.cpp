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
 *
 * 2026-10-23 (cube-devkit-1.6.2, 사용자 결정): 멀티플렉서 채널 자동 탐색.
 *   자리 i 가 고정 채널표(SHT45_MUX_CHANNELS[i])에 묶여 있던 것을, 부팅 때 8채널(0~7)을 훑어 0x44 가 응답하는 채널을
 *   채널 낮은 순으로 자리 1·2·3 에 배정하는 방식으로 바꿨다. 종류 판별은 "응답 주소"(0x44 = 온습도) + 기존 모델 판별(일련번호·상태 레지스터).
 *   · 배정된 자리는 그 채널을 기억한다. 가동 중 센서가 빠지면 자리는 비우되(값 null) 번호를 당기지 않고, 5초마다 그 채널을 먼저 다시 찾아
 *     같은 자리로 되돌린다(그래프 시계열이 섞이지 않게).
 *   · 새 채널에서 센서가 나타나면 빈 자리 중 첫째에 넣는다(채널 기억이 없는 자리 → 없으면 잃은 자리).
 *   · SHT45_MAX(3)개를 넘는 센서는 로그 한 번 남기고 무시한다. 자리가 비면 다음 탐색에서 다시 후보가 된다.
 *   · 토양 ADS1115(0x48)는 이 모듈과 무관하게 채널 1 고정(soil.cpp). 멀티플렉서가 없으면 예전처럼 버스 직결 1개.
 *   · 탐색 한 번 = 채널 선택 8번 + 주소 확인 8번(약 5ms) + 찾은 센서마다 모델 판별(약 6ms). 빈 자리가 있을 때만 5초마다.
 */

namespace sht45 {
namespace {

enum Phase : uint8_t { PH_IDLE, PH_NEXT, PH_MEASURING };
enum Model : uint8_t { M_NONE, M_UNKNOWN, M_SHT4X, M_SHT3X };

struct Slot {
  bool     present = false;
  uint8_t  channel = I2C_MUX_CH_NONE;   // 배정된(또는 잃은 뒤 기억하는) 멀티플렉서 채널. 직결 모드·미배정 = I2C_MUX_CH_NONE
  Model    model = M_NONE;
  uint8_t  fails = 0;
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
uint32_t lastScanMs = 0;
uint8_t  ignoredMask = 0;     // 자리 초과로 무시한 채널(bit n = 채널 n). 로그를 한 번만 내기 위한 표시. 자리가 비면 0 으로
char     lastErr[96] = "";
char     csvBuf[SHT45_MAX * 4 + 1];

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

// 자리 표시용 ("ch4" / "ch?"(미배정) / "직결")
const char* whereText(uint8_t slot, char* buf, size_t cap) {
  if (!useMux)                                  snprintf(buf, cap, "직결");
  else if (slots[slot].channel == I2C_MUX_CH_NONE) snprintf(buf, cap, "ch?");
  else                                          snprintf(buf, cap, "ch%u", static_cast<unsigned>(slots[slot].channel));
  return buf;
}

// 그 자리의 센서가 보이게 채널을 켭니다. 직결 모드면 할 일 없음(자리 0 만).
bool route(uint8_t slot) {
  if (!useMux) return slot == 0;
  const uint8_t ch = slots[slot].channel;
  if (ch == I2C_MUX_CH_NONE) return false;
  return mux::select(ch);
}

// 지금 켜진 채널(또는 직결 버스)에 0x44 가 있는지
bool probeAddr() {
  Wire.beginTransmission(SHT45_ADDR);
  return Wire.endTransmission() == 0;
}

// 채널 ch 를 켜고 0x44 를 확인 (경유 모드 전용)
bool probeChannel(uint8_t ch) {
  if (!mux::select(ch)) return false;
  return probeAddr();
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

// 모델 판별. 응답은 하는데 둘 다 아니면 M_UNKNOWN(SHT4x 명령으로 시도). 부르기 전에 그 자리의 채널이 켜져 있어야 한다.
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

// 자리를 비웁니다. 채널은 기억해 두어(경유 모드) 재탐색 때 같은 자리로 되돌릴 수 있게 합니다.
void markLost(uint8_t slot, const char* why) {
  Slot& s = slots[slot];
  char w[8];
  if (s.present) {
    Serial.printf("[SHT45] #%u (%s) %s — 없음으로 처리, 5초마다 다시 찾음(같은 채널이면 같은 자리로)\n",
                  static_cast<unsigned>(slot + 1), whereText(slot, w, sizeof(w)), why);
  }
  s.present = false;
  s.model = M_NONE;
  s.fails = 0;
  s.temp = NAN;
  s.hum = NAN;
  ignoredMask = 0;   // 자리가 비었으니 무시했던 채널도 다시 후보
}

void fail(uint8_t slot, const char* step) {
  Slot& s = slots[slot];
  char w[8];
  s.fails++;
  Serial.printf("[SHT45] #%u (%s, %s) %s 실패 %u/%u: %s\n",
                static_cast<unsigned>(slot + 1), whereText(slot, w, sizeof(w)), modelName(s.model),
                step, static_cast<unsigned>(s.fails), static_cast<unsigned>(SHT45_MAX_FAILS), lastErr);
  if (s.fails >= SHT45_MAX_FAILS) markLost(slot, "연속 실패");
}

// 자리에 센서가 있다고 확정하고 모델을 판별합니다. 부르기 전에 그 자리의 채널이 켜져 있어야 합니다.
void found(uint8_t slot) {
  Slot& s = slots[slot];
  char w[8];
  s.present = true;
  s.fails = 0;
  s.model = identify(slot);
  if (s.model == M_SHT4X) {
    Serial.printf("[SHT45] #%u (%s) 응답 — SHT4x 확인, 일련번호 %08lX — 측정 시작\n",
                  static_cast<unsigned>(slot + 1), whereText(slot, w, sizeof(w)), static_cast<unsigned long>(s.serial));
  } else if (s.model == M_SHT3X) {
    Serial.printf("[SHT45] #%u (%s) 응답 — ★ SHT3x(SHT30/31) 로 판별, SHT3x 명령으로 측정\n",
                  static_cast<unsigned>(slot + 1), whereText(slot, w, sizeof(w)));
  } else {
    Serial.printf("[SHT45] #%u (%s) 0x44 응답은 하나 SHT4x·SHT3x 판별 실패(%s) — SHT4x 명령으로 시도\n",
                  static_cast<unsigned>(slot + 1), whereText(slot, w, sizeof(w)), lastErr);
  }
}

bool hasEmptySlot() {
  for (uint8_t i = 0; i < SHT45_MAX; i++) if (!slots[i].present) return true;
  return false;
}

// 응답 중인 자리가 이 채널을 쓰는지
bool channelInUse(uint8_t ch) {
  for (uint8_t i = 0; i < SHT45_MAX; i++) if (slots[i].present && slots[i].channel == ch) return true;
  return false;
}

// 잃은 자리가 이 채널을 기억하는지 (1단계에서 이미 물어본 채널)
bool channelRemembered(uint8_t ch) {
  for (uint8_t i = 0; i < SHT45_MAX; i++) if (!slots[i].present && slots[i].channel == ch) return true;
  return false;
}

// 새 센서를 넣을 자리: 채널 기억이 없는 빈 자리 → 없으면 잃은 자리 중 첫째. -1 = 자리 없음
int8_t pickSlot() {
  for (uint8_t i = 0; i < SHT45_MAX; i++) if (!slots[i].present && slots[i].channel == I2C_MUX_CH_NONE) return static_cast<int8_t>(i);
  for (uint8_t i = 0; i < SHT45_MAX; i++) if (!slots[i].present) return static_cast<int8_t>(i);
  return -1;
}

// 멀티플렉서 8채널 탐색 (경유 모드). 반환 = 이번에 새로 찾은 수.
//   1단계: 잃은 자리가 기억하는 채널을 먼저 물어 같은 자리로 복귀.
//   2단계: 나머지 채널을 낮은 번호부터 훑어 0x44 가 응답하면 빈 자리에 배정. 자리가 없으면 무시(채널당 로그 한 번).
uint8_t scanChannels() {
  uint8_t n = 0;
  for (uint8_t i = 0; i < SHT45_MAX; i++) {
    Slot& s = slots[i];
    if (s.present || s.channel == I2C_MUX_CH_NONE) continue;
    if (probeChannel(s.channel)) { found(i); n++; }
  }
  for (uint8_t ch = 0; ch < I2C_MUX_CH_COUNT; ch++) {
    if (channelInUse(ch) || channelRemembered(ch)) continue;
    if (!probeChannel(ch)) continue;
    const int8_t slot = pickSlot();
    if (slot < 0) {
      if (!(ignoredMask & static_cast<uint8_t>(1u << ch))) {
        ignoredMask |= static_cast<uint8_t>(1u << ch);
        Serial.printf("[SHT45] 채널 %u 에 0x44 응답 — 자리 %u개를 넘어 무시(자리가 비면 다시 후보)\n",
                      static_cast<unsigned>(ch), static_cast<unsigned>(SHT45_MAX));
      }
      continue;
    }
    slots[slot].channel = ch;
    found(static_cast<uint8_t>(slot));
    n++;
  }
  mux::deselectAll();
  return n;
}

// 직결 모드 탐색: 자리 0 하나
uint8_t scanDirect() {
  slots[0].channel = I2C_MUX_CH_NONE;
  if (!probeAddr()) return 0;
  found(0);
  return 1;
}

// 빈 자리가 있고 재탐색 시각이 됐으면 탐색
void rescanIfDue(uint32_t now) {
  if (!hasEmptySlot()) return;
  if (now - lastScanMs < SHT45_RETRY_MS) return;
  lastScanMs = now;
  if (useMux) scanChannels();
  else        scanDirect();
}

// 한 바퀴 안에서 다음 자리로. 빈 자리는 건너뜁니다(재탐색은 바퀴 시작 때 rescanIfDue 가 함).
void advance() {
  const uint32_t now = millis();
  while (idx < SHT45_MAX) {
    Slot& s = slots[idx];
    if (!s.present) {
      if (!useMux) break;   // 직결 모드는 자리 하나뿐
      idx++;
      continue;
    }
    if (route(idx) && sendMeasure(s.model)) {
      cmdMs = now;
      phase = PH_MEASURING;
      return;
    }
    snprintf(lastErr, sizeof(lastErr), "명령 NACK(채널 선택 또는 쓰기 실패)");
    fail(idx, "측정 명령");
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
  Serial.printf("[SHT45] 0x%02X %s | 최대 %u개, 고정밀, %lu ms 주기\n", static_cast<unsigned>(SHT45_ADDR),
                useMux ? "PCA9548A 채널 자동 탐색(0~7, 자리 = 채널 낮은 순)" : "버스 직결(멀티플렉서 없음)",
                static_cast<unsigned>(useMux ? SHT45_MAX : 1), static_cast<unsigned long>(SHT45_PERIOD_MS));
  lastScanMs = millis();
  const uint8_t n = useMux ? scanChannels() : scanDirect();
  if (n == 0) Serial.println("[SHT45] 응답 없음 — 5초마다 다시 찾음");
  else        Serial.printf("[SHT45] 응답 %u개 (자리 배정 %s)\n", static_cast<unsigned>(n), channelListCsv());
  roundStartMs = millis() - SHT45_PERIOD_MS;   // 첫 바퀴는 바로
#endif
}

void tick() {
#if ENABLE_SHT45
  const uint32_t now = millis();

  // 멀티플렉서가 나중에 나타나면(재탐색 성공) 경유 모드로 전환하고 전 자리를 다시 찾습니다
  if (!useMux && mux::present()) {
    useMux = true;
    Serial.println("[SHT45] 멀티플렉서 발견 — 채널 경유 모드로 전환, 채널 자동 탐색");
    for (uint8_t i = 0; i < SHT45_MAX; i++) { markLost(i, "모드 전환"); slots[i].channel = I2C_MUX_CH_NONE; }
    lastScanMs = now - SHT45_RETRY_MS;   // 다음 바퀴 시작 때 바로 탐색
    phase = PH_IDLE;
    roundStartMs = now - SHT45_PERIOD_MS;
  }

  switch (phase) {
    case PH_IDLE:
      if (now - roundStartMs < SHT45_PERIOD_MS) return;
      roundStartMs = now;
      rescanIfDue(now);
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
uint8_t channelOf(uint8_t slot)  { return (slot < SHT45_MAX && slots[slot].present) ? slots[slot].channel : I2C_MUX_CH_NONE; }
const char* model(uint8_t slot)  { return slot < SHT45_MAX ? modelName(slots[slot].model) : ""; }

// 자리 순서로 채널을 쉼표로 이어 붙인 문자열. 응답 중인 자리 = 채널 번호(직결이면 "bus"), 빈 자리 = "-". 예: "0,2,-"
const char* channelListCsv() {
  size_t pos = 0;
  for (uint8_t i = 0; i < SHT45_MAX && pos < sizeof(csvBuf) - 1; i++) {
    if (i) csvBuf[pos++] = ',';
    const Slot& s = slots[i];
    int w;
    if (!s.present)   w = snprintf(csvBuf + pos, sizeof(csvBuf) - pos, "-");
    else if (!useMux) w = snprintf(csvBuf + pos, sizeof(csvBuf) - pos, "bus");
    else              w = snprintf(csvBuf + pos, sizeof(csvBuf) - pos, "%u", static_cast<unsigned>(s.channel));
    if (w < 0) break;
    pos += static_cast<size_t>(w);
    if (pos >= sizeof(csvBuf) - 1) { pos = sizeof(csvBuf) - 1; break; }
  }
  csvBuf[pos] = '\0';
  return csvBuf;
}

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
  char w[8];
  Serial.printf("[SHT45] %s | 응답 %u개 | 자리 배정 %s", useMux ? "채널 자동 탐색" : "직결", static_cast<unsigned>(count()), channelListCsv());
  for (uint8_t i = 0; i < (useMux ? SHT45_MAX : 1); i++) {
    const Slot& s = slots[i];
    if (ok(i))          Serial.printf(" | #%u %s %s %.1f°C %.1f%%", static_cast<unsigned>(i + 1), whereText(i, w, sizeof(w)), modelName(s.model), s.temp, s.hum);
    else if (s.present) Serial.printf(" | #%u %s %s 응답은 하나 값 없음(실패 %u회)", static_cast<unsigned>(i + 1), whereText(i, w, sizeof(w)), modelName(s.model), static_cast<unsigned>(s.fails));
    else if (useMux && s.channel != I2C_MUX_CH_NONE) Serial.printf(" | #%u %s 없음(그 채널을 5초마다 다시 찾음)", static_cast<unsigned>(i + 1), whereText(i, w, sizeof(w)));
    else                Serial.printf(" | #%u 빈 자리", static_cast<unsigned>(i + 1));
  }
  if (anyOk()) Serial.printf(" | 평균 %.1f°C %.1f%%", avgTemperature(), avgHumidity());
  if (lastErr[0]) Serial.printf(" | 마지막 오류: %s", lastErr);
  Serial.println();
#endif
}

}  // namespace sht45
