#include "co2_hx2000u.h"
#include "config.h"

#include <string.h>

namespace co2 {
namespace {

enum class State : uint8_t { Idle, Waiting };

// 시트 "Host Send (Read CO2 Conc.)": 시작 2 + 명령 1 + 인자 2 + 체크섬 2 = 7바이트. 체크섬 0x0172 = 0x42+0x4D+0xE3.
constexpr uint8_t REQUEST[7] = { 0x42, 0x4D, 0xE3, 0x00, 0x00, 0x01, 0x72 };
// 시트 "Sensor Feed back": 시작 2 + 명령 2(0x00 0x08) + CO2 2 + Cal_A 2 + Cal_B 2 + 체크섬 2 = 12바이트
constexpr uint8_t RESPONSE_LEN = 12;
constexpr uint8_t HEADER[4]    = { 0x42, 0x4D, 0x00, 0x08 };

State    state = State::Idle;
bool     started = false;
bool     haveData = false;
bool     readNowFlag = true;                   // 부팅 직후 바로 한 번
uint8_t  fails = 0;                            // 연속 실패
uint32_t lastRequestMs = 0;
uint32_t sentMs = 0;
uint32_t lastOkMs = 0;
uint32_t warmStartMs = 0;                      // 첫(또는 다시 받은) 유효 응답 시각 = 예열 시작
uint32_t okCount = 0;
uint32_t failCount = 0;

int      lastPpm = -1;
uint16_t calA = 0;                             // 진단 표시용. 계산에는 쓰지 않음
uint8_t  calBHigh = 0, calBLow = 0;            // 시트 예: 65.0 → 0x41 0x00, 70.01 → 0x46 0x01

uint8_t  rxBuf[64];
uint8_t  rxLen = 0;
uint8_t  lastFrame[RESPONSE_LEN];              // 진단용: 마지막으로 받은(또는 유효한) 프레임
uint8_t  lastFrameLen = 0;
char     lastError[48] = "";

void saveFrame(const uint8_t* d, uint8_t n) {
  lastFrameLen = n > sizeof(lastFrame) ? sizeof(lastFrame) : n;
  memcpy(lastFrame, d, lastFrameLen);
}

void sendRequest() {
  while (Serial1.available()) Serial1.read();     // 묵은 바이트(센서 자체 송신·잡음) 비움
  Serial1.write(REQUEST, sizeof(REQUEST));
  rxLen = 0;
  sentMs = millis();
  lastRequestMs = sentMs;
  state = State::Waiting;
}

// 수신 버퍼에서 유효한 응답을 찾습니다. 1 = 정상(lastPpm 갱신), 0 = 아직 없음.
// 머리 [42][4D][00][08] 을 찍어 가며 찾으므로 앞에 붙은 잡음이나 센서가 먼저 보낸 조각은 건너뜁니다.
int tryParse() {
  for (uint8_t i = 0; i + sizeof(HEADER) <= rxLen; i++) {
    if (memcmp(&rxBuf[i], HEADER, sizeof(HEADER)) != 0) continue;
    if (i + RESPONSE_LEN > rxLen) return 0;      // 머리는 맞는데 아직 덜 들어옴
    uint16_t sum = 0;
    for (uint8_t k = 0; k < RESPONSE_LEN - 2; k++) sum += rxBuf[i + k];
    const uint8_t ckHigh = static_cast<uint8_t>(sum >> 8), ckLow = static_cast<uint8_t>(sum & 0xFF);
    if (rxBuf[i + 10] != ckHigh || rxBuf[i + 11] != ckLow) {
      snprintf(lastError, sizeof(lastError), "체크섬 불일치");
      saveFrame(&rxBuf[i], RESPONSE_LEN);
      continue;
    }
    lastPpm  = static_cast<int>(rxBuf[i + 4]) * 256 + rxBuf[i + 5];
    calA     = static_cast<uint16_t>((rxBuf[i + 6] << 8) | rxBuf[i + 7]);
    calBHigh = rxBuf[i + 8];
    calBLow  = rxBuf[i + 9];
    saveFrame(&rxBuf[i], RESPONSE_LEN);
    return 1;
  }
  return 0;
}

void onSuccess(uint32_t now) {
  const bool first = !haveData;
  fails = 0;
  haveData = true;
  lastOkMs = now;
  okCount++;
  lastError[0] = '\0';
  if (first) {
    warmStartMs = now;   // 첫 응답(또는 잃었다가 다시 받음)부터 예열 CO2_WARMUP_MS (3분, 2026-10-16)
    Serial.printf("[CO2] 응답 시작 — %d ppm (Cal_A %u · Cal_B %u.%02u). 예열 %lu분 동안은 텔레메트리 null\n",
                  lastPpm, static_cast<unsigned>(calA), static_cast<unsigned>(calBHigh), static_cast<unsigned>(calBLow),
                  static_cast<unsigned long>(CO2_WARMUP_MS / 60000UL));
  }
  state = State::Idle;
}

void onFail(const char* why) {
  failCount++;
  fails++;
  if (why) snprintf(lastError, sizeof(lastError), "%s", why);
  if (rxLen > 0) saveFrame(rxBuf, rxLen);
  state = State::Idle;
  if (fails < CO2_MAX_FAILS) return;
  fails = 0;
  if (haveData) {
    Serial.printf("[CO2] 응답 끊김(%s) — 없음으로 처리, 계속 재시도. 센서 5V·GND·Tx/Rx 교차(센서 Tx → GPIO%u) 확인\n",
                  lastError, static_cast<unsigned>(PIN_CO2_RX));
  }
  haveData = false;
  lastPpm = -1;
}

}  // namespace

// ──────────────────────────────────────────────────── 공개 API

void begin() {
#if !ENABLE_CO2
  Serial.println("[CO2] ENABLE_CO2=0 — CO2 센서 기능이 꺼져 있습니다");
#else
  if (started) Serial1.end();                   // g19/g18 핀 시험 뒤 다시 초기화하는 경우. 예열 시계·통계는 유지
  Serial1.begin(CO2_BAUD, SERIAL_8N1, PIN_CO2_RX, PIN_CO2_TX);
  started = true;
  readNowFlag = true;
  rxLen = 0;
  state = State::Idle;
  Serial.printf("[CO2] HX-2000U UART1 TX=GPIO%u RX=GPIO%u %lu bps 8N1 | %lu ms 마다 읽기, 예열 %lu분, 응답 대기 %lu ms\n",
                static_cast<unsigned>(PIN_CO2_TX), static_cast<unsigned>(PIN_CO2_RX), static_cast<unsigned long>(CO2_BAUD),
                static_cast<unsigned long>(CO2_PERIOD_MS), static_cast<unsigned long>(CO2_WARMUP_MS / 60000UL),
                static_cast<unsigned long>(CO2_TIMEOUT_MS));
#endif
}

void tick() {
#if ENABLE_CO2
  if (!started) return;
  const uint32_t now = millis();

  if (state == State::Idle) {
    if (readNowFlag || now - lastRequestMs >= CO2_PERIOD_MS) {
      readNowFlag = false;
      sendRequest();
    }
    return;
  }

  // 응답 대기: 들어온 바이트를 모으고 프레임을 찾습니다
  while (Serial1.available() && rxLen < sizeof(rxBuf)) rxBuf[rxLen++] = static_cast<uint8_t>(Serial1.read());
  if (tryParse() == 1) { onSuccess(now); return; }
  if (now - sentMs >= CO2_TIMEOUT_MS) {
    onFail(rxLen == 0 ? "응답 없음" : "유효 프레임 없음");
  }
#endif
}

bool ok()        { return started && haveData; }
bool warmingUp() { return ok() && (millis() - warmStartMs) < CO2_WARMUP_MS; }
int  rawPpm()    { return ok() ? lastPpm : -1; }
int  ppm()       { return (ok() && !warmingUp()) ? lastPpm : -1; }

uint32_t warmupRemainingMs() {
  if (!warmingUp()) return 0;
  return CO2_WARMUP_MS - (millis() - warmStartMs);
}

void readNow() {
  readNowFlag = true;
  if (state == State::Waiting) state = State::Idle;   // 대기 중이던 요청은 버리고 새로 보냄
}

void reportSerial() {
#if ENABLE_CO2
  if (!started) { Serial.println("[CO2] 시작 안 됨"); return; }
  Serial.printf("[CO2] HX-2000U | UART1 TX%u/RX%u %lu bps | 성공 %lu · 실패 %lu · 연속 실패 %u\n",
                static_cast<unsigned>(PIN_CO2_TX), static_cast<unsigned>(PIN_CO2_RX), static_cast<unsigned long>(CO2_BAUD),
                static_cast<unsigned long>(okCount), static_cast<unsigned long>(failCount), static_cast<unsigned>(fails));
  if (ok()) {
    Serial.printf("        %d ppm", lastPpm);
    if (warmingUp()) Serial.printf(" (예열 중, %lu분 남음 — 텔레메트리 null)", static_cast<unsigned long>((warmupRemainingMs() + 59999UL) / 60000UL));
    Serial.printf("  Cal_A %u · Cal_B %u.%02u  (%lu초 전)\n", static_cast<unsigned>(calA),
                  static_cast<unsigned>(calBHigh), static_cast<unsigned>(calBLow),
                  static_cast<unsigned long>((millis() - lastOkMs) / 1000));
  } else {
    Serial.printf("        값 없음%s%s\n", lastError[0] ? " — 마지막 오류: " : "", lastError);
  }
  if (lastFrameLen > 0) {
    Serial.print("        마지막 프레임:");
    for (uint8_t i = 0; i < lastFrameLen; i++) Serial.printf(" %02X", static_cast<unsigned>(lastFrame[i]));
    Serial.println();
  }
#else
  Serial.println("[CO2] ENABLE_CO2=0");
#endif
}

}  // namespace co2
