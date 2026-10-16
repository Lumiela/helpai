#include "rs485_soil.h"
#include "config.h"

#include <math.h>
#include <string.h>

namespace rs485soil {
namespace {

enum class State : uint8_t { Idle, Waiting };

// 응답 길이: 주소 1 + 기능 1 + 바이트 수 1 + 데이터 16 + CRC 2 = 21
constexpr uint8_t RESPONSE_LEN = 3 + RS485_SOIL_REG_COUNT * 2 + 2;

State    state = State::Idle;
bool     started = false;
bool     manual = false;                       // b<addr> 로 지정 → 탐색 안 함
bool     quietScan = false;                    // 탐색 한 바퀴를 다 돌아 로그를 줄이는 중
uint8_t  scanIdx = 0;
uint8_t  curAddr = RS485_SOIL_ADDR_DEFAULT;    // 지금 요청을 보내는 주소
int      lockedAddr = -1;                      // 응답한 주소. -1 = 아직 없음
uint8_t  fails = 0;                            // 연속 실패
bool     haveData = false;
bool     readNowFlag = true;                   // 부팅 직후 바로 한 번
uint32_t lastRequestMs = 0;
uint32_t sentMs = 0;
uint32_t lastOkMs = 0;
uint32_t okCount = 0;
uint32_t failCount = 0;

uint8_t  rxBuf[64];
uint8_t  rxLen = 0;
uint8_t  lastFrame[32];                        // 진단용: 마지막으로 받은(또는 유효한) 프레임
uint8_t  lastFrameLen = 0;
char     lastError[48] = "";

int16_t  regs[RS485_SOIL_REG_COUNT];

// Modbus CRC16 (다항식 0xA001, 초기값 0xFFFF). 전송 순서는 하위 바이트 먼저.
uint16_t crc16(const uint8_t* d, uint8_t n) {
  uint16_t crc = 0xFFFF;
  for (uint8_t i = 0; i < n; i++) {
    crc ^= d[i];
    for (uint8_t b = 0; b < 8; b++) {
      if (crc & 0x0001) crc = static_cast<uint16_t>((crc >> 1) ^ 0xA001);
      else              crc = static_cast<uint16_t>(crc >> 1);
    }
  }
  return crc;
}

void saveFrame(const uint8_t* d, uint8_t n) {
  lastFrameLen = n > sizeof(lastFrame) ? sizeof(lastFrame) : n;
  memcpy(lastFrame, d, lastFrameLen);
}

void sendRequest() {
  while (Serial2.available()) Serial2.read();     // 묵은 바이트(에코·잡음) 비움
  uint8_t req[8] = {
    curAddr, 0x03,
    static_cast<uint8_t>(RS485_SOIL_REG_START >> 8), static_cast<uint8_t>(RS485_SOIL_REG_START & 0xFF),
    0x00, RS485_SOIL_REG_COUNT, 0x00, 0x00
  };
  const uint16_t crc = crc16(req, 6);
  req[6] = static_cast<uint8_t>(crc & 0xFF);
  req[7] = static_cast<uint8_t>(crc >> 8);
  Serial2.write(req, sizeof(req));
  rxLen = 0;
  sentMs = millis();
  lastRequestMs = sentMs;
  state = State::Waiting;
}

// 수신 버퍼에서 유효한 응답을 찾습니다. 1 = 정상 응답(regs 갱신), -1 = Modbus 예외 응답, 0 = 아직 없음.
// 머리 [주소][0x03][0x10] 을 찍어 가며 찾으므로 앞에 붙은 에코(요청 8바이트)나 잡음은 건너뜁니다.
int tryParse() {
  for (uint8_t i = 0; i + 5 <= rxLen; i++) {
    const bool addrOk = (curAddr == 0) || (rxBuf[i] == curAddr);
    if (!addrOk) continue;

    // 예외 응답: [주소][0x83][코드][CRC lo][CRC hi]
    if (rxBuf[i + 1] == 0x83) {
      const uint16_t crc = crc16(&rxBuf[i], 3);
      if (rxBuf[i + 3] == (crc & 0xFF) && rxBuf[i + 4] == (crc >> 8)) {
        snprintf(lastError, sizeof(lastError), "Modbus 예외 코드 %u", static_cast<unsigned>(rxBuf[i + 2]));
        saveFrame(&rxBuf[i], 5);
        return -1;
      }
      continue;
    }

    if (rxBuf[i + 1] != 0x03 || rxBuf[i + 2] != RS485_SOIL_REG_COUNT * 2) continue;
    if (i + RESPONSE_LEN > rxLen) return 0;      // 머리는 맞는데 아직 덜 들어옴
    const uint16_t crc = crc16(&rxBuf[i], RESPONSE_LEN - 2);
    if (rxBuf[i + RESPONSE_LEN - 2] != (crc & 0xFF) || rxBuf[i + RESPONSE_LEN - 1] != (crc >> 8)) {
      snprintf(lastError, sizeof(lastError), "CRC 불일치");
      continue;
    }
    for (uint8_t r = 0; r < RS485_SOIL_REG_COUNT; r++) {
      regs[r] = static_cast<int16_t>((rxBuf[i + 3 + r * 2] << 8) | rxBuf[i + 4 + r * 2]);
    }
    lockedAddr = rxBuf[i];
    saveFrame(&rxBuf[i], RESPONSE_LEN);
    return 1;
  }
  return 0;
}

void onSuccess(uint32_t now) {
  const bool first = !haveData;
  fails = 0;
  haveData = true;
  quietScan = false;
  lastOkMs = now;
  okCount++;
  lastError[0] = '\0';
  // 브로드캐스트(0)로 찾았으면 센서가 밝힌 자기 주소로 이후 요청을 보냅니다(0 으로 답하는 개체는 0 유지).
  if (curAddr != lockedAddr) curAddr = static_cast<uint8_t>(lockedAddr);
  if (first) {
    Serial.printf("[RS485] 주소 %d 응답 — 고정. T %.1f°C VWC %.1f%% EC %d pH %.1f\n",
                  lockedAddr, static_cast<double>(regs[0] / 10.0f), static_cast<double>(static_cast<uint16_t>(regs[1]) / 10.0f),
                  static_cast<int>(static_cast<uint16_t>(regs[2])), static_cast<double>(static_cast<uint16_t>(regs[7]) / 10.0f));
  }
  state = State::Idle;
}

void onFail(const char* why) {
  failCount++;
  fails++;
  if (why) snprintf(lastError, sizeof(lastError), "%s", why);
  if (rxLen > 0) saveFrame(rxBuf, rxLen);
  state = State::Idle;

  const uint8_t limit = (lockedAddr >= 0 || manual) ? RS485_SOIL_MAX_FAILS : RS485_SOIL_SCAN_FAILS;
  if (fails < limit) return;
  fails = 0;

  if (lockedAddr >= 0) {
    Serial.printf("[RS485] 주소 %d 응답 끊김(%s) — 없음으로 처리%s\n", lockedAddr, lastError,
                  manual ? "" : ", 다른 주소부터 다시 탐색");
  }
  lockedAddr = -1;
  haveData = false;
  if (manual) return;

  scanIdx = static_cast<uint8_t>((scanIdx + 1) % RS485_SOIL_SCAN_COUNT);
  curAddr = RS485_SOIL_SCAN_ADDRS[scanIdx];
  if (scanIdx == 0 && !quietScan) {
    quietScan = true;    // 한 바퀴 다 돌았음 → 응답이 올 때까지 조용히 계속 탐색
    Serial.printf("[RS485] 주소 %u·%u·%u 모두 응답 없음 — 센서·변환기 전원과 A/B 선, 분압 저항을 확인. 계속 탐색\n",
                  static_cast<unsigned>(RS485_SOIL_SCAN_ADDRS[0]), static_cast<unsigned>(RS485_SOIL_SCAN_ADDRS[1]),
                  static_cast<unsigned>(RS485_SOIL_SCAN_ADDRS[2]));
  }
}

void resetSearch(bool keepManual) {
  if (!keepManual) { manual = false; scanIdx = 0; curAddr = RS485_SOIL_SCAN_ADDRS[0]; }
  lockedAddr = -1;
  haveData = false;
  fails = 0;
  quietScan = false;
  readNowFlag = true;
  rxLen = 0;
  state = State::Idle;
}

}  // namespace

// ──────────────────────────────────────────────────── 공개 API

void begin() {
#if !ENABLE_RS485_SOIL
  Serial.println("[RS485] ENABLE_RS485_SOIL=0 — 토양 7종 센서 기능이 꺼져 있습니다");
#else
  if (started) Serial2.end();                   // g17 핀 시험 뒤 다시 초기화하는 경우
  Serial2.begin(RS485_SOIL_BAUD, SERIAL_8N1, PIN_RS485_RX, PIN_RS485_TX);
  started = true;
  resetSearch(manual);
  Serial.printf("[RS485] UART2 TX=GPIO%u RX=GPIO%u %lu bps 8N1 | 주소 %u 부터 탐색(%u → %u → %u), 고정 뒤 %lu ms 마다 8레지스터 읽기\n",
                static_cast<unsigned>(PIN_RS485_TX), static_cast<unsigned>(PIN_RS485_RX), static_cast<unsigned long>(RS485_SOIL_BAUD),
                static_cast<unsigned>(curAddr), static_cast<unsigned>(RS485_SOIL_SCAN_ADDRS[0]), static_cast<unsigned>(RS485_SOIL_SCAN_ADDRS[1]),
                static_cast<unsigned>(RS485_SOIL_SCAN_ADDRS[2]), static_cast<unsigned long>(RS485_SOIL_PERIOD_MS));
#endif
}

void tick() {
#if ENABLE_RS485_SOIL
  if (!started) return;
  const uint32_t now = millis();

  if (state == State::Idle) {
    const uint32_t period = (lockedAddr < 0 && !manual) ? RS485_SOIL_SCAN_PERIOD_MS : RS485_SOIL_PERIOD_MS;
    if (readNowFlag || now - lastRequestMs >= period) {
      readNowFlag = false;
      sendRequest();
    }
    return;
  }

  // 응답 대기: 들어온 바이트를 모으고 프레임을 찾습니다
  while (Serial2.available() && rxLen < sizeof(rxBuf)) rxBuf[rxLen++] = static_cast<uint8_t>(Serial2.read());
  const int r = tryParse();
  if (r == 1)  { onSuccess(now); return; }
  if (r == -1) { onFail(nullptr); return; }
  if (now - sentMs >= RS485_SOIL_TIMEOUT_MS) {
    onFail(rxLen == 0 ? "응답 없음" : "유효 프레임 없음");
  }
#endif
}

bool    ok()             { return started && haveData; }
bool    scanning()       { return started && !manual && lockedAddr < 0; }
int     address()        { return lockedAddr; }
uint8_t currentAddress() { return curAddr; }

float temperature() { return ok() ? regs[0] / 10.0f : NAN; }
float vwc()         { return ok() ? static_cast<uint16_t>(regs[1]) / 10.0f : NAN; }
int   ec()          { return ok() ? static_cast<int>(static_cast<uint16_t>(regs[2])) : -1; }
int   salinity()    { return ok() ? static_cast<int>(static_cast<uint16_t>(regs[3])) : -1; }
int   nitrogen()    { return ok() ? static_cast<int>(static_cast<uint16_t>(regs[4])) : -1; }
int   phosphorus()  { return ok() ? static_cast<int>(static_cast<uint16_t>(regs[5])) : -1; }
int   potassium()   { return ok() ? static_cast<int>(static_cast<uint16_t>(regs[6])) : -1; }
float ph()          { return ok() ? static_cast<uint16_t>(regs[7]) / 10.0f : NAN; }

bool setAddress(int addr) {
  if (addr < 0 || addr > 247) return false;
  manual = true;
  curAddr = static_cast<uint8_t>(addr);
  resetSearch(true);
  Serial.printf("[RS485] 주소 %u 수동 지정 — 탐색 중단, 바로 읽기\n", static_cast<unsigned>(curAddr));
  return true;
}

void rescan() {
  resetSearch(false);
  Serial.printf("[RS485] 주소 다시 탐색: %u → %u → %u\n",
                static_cast<unsigned>(RS485_SOIL_SCAN_ADDRS[0]), static_cast<unsigned>(RS485_SOIL_SCAN_ADDRS[1]),
                static_cast<unsigned>(RS485_SOIL_SCAN_ADDRS[2]));
}

void readNow() {
  readNowFlag = true;
  if (state == State::Waiting) state = State::Idle;   // 대기 중이던 요청은 버리고 새로 보냄
}

void reportSerial() {
#if ENABLE_RS485_SOIL
  if (!started) { Serial.println("[RS485] 시작 안 됨"); return; }
  Serial.printf("[RS485] 토양 7종 | UART2 TX%u/RX%u %lu bps | 주소 ",
                static_cast<unsigned>(PIN_RS485_TX), static_cast<unsigned>(PIN_RS485_RX), static_cast<unsigned long>(RS485_SOIL_BAUD));
  if (manual)               Serial.printf("%u 수동", static_cast<unsigned>(curAddr));
  else if (lockedAddr >= 0) Serial.printf("%d 고정", lockedAddr);
  else                      Serial.printf("탐색 중(지금 %u)", static_cast<unsigned>(curAddr));
  Serial.printf(" | 성공 %lu · 실패 %lu · 연속 실패 %u\n",
                static_cast<unsigned long>(okCount), static_cast<unsigned long>(failCount), static_cast<unsigned>(fails));
  if (ok()) {
    Serial.printf("        T %.1f°C  VWC %.1f%%  EC %d µS/cm  염분 %d  N %d  P %d  K %d mg/kg  pH %.1f  (%lu초 전)\n",
                  static_cast<double>(temperature()), static_cast<double>(vwc()), ec(), salinity(),
                  nitrogen(), phosphorus(), potassium(), static_cast<double>(ph()),
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
  Serial.println("[RS485] ENABLE_RS485_SOIL=0");
#endif
}

}  // namespace rs485soil
