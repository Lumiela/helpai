/*
 * SHT45 온습도 센서 모듈 — I2C 0x44, PCA9548A 채널 경유 최대 3개 (2026-09-29)
 * ------------------------------------------------------------------
 * 사용자 결정(2026-09-29): 멀티플렉서를 거쳐 3개를 기준으로 하되 지금은 2개. 1·2·3개 어느
 * 경우든 동작해야 한다. → 부팅 때 응답하는 것만 쓰고, 없는 자리는 5초마다 다시 찾는다. 멀티플렉서 자체가 없으면 버스 직결 1개로 동작한다.
 * 사용자 결정(2026-10-23, 1.6.2): 채널 고정표를 없애고 자동 탐색 — 부팅 때 8채널을 훑어 0x44 가 응답한 채널을 채널 낮은 순으로
 * 자리 1·2·3 에 배정한다. 빠진 자리는 번호를 당기지 않고 그 채널을 먼저 다시 찾는다. 4개째부터는 무시. 자세한 규칙은 sht45.cpp 머리말.
 *
 * ── 측정 (데이터시트 §4.5) ──
 *   0xFD = 고정밀 단발 측정(최대 8.3ms). 명령 뒤 SHT45_MEAS_WAIT_MS 기다렸다가 6바이트를 읽는다:
 *   T_hi T_lo CRC | RH_hi RH_lo CRC.  CRC-8: 다항식 0x31, 초기값 0xFF.
 *   T[°C]  = -45 + 175 × raw / 65535
 *   RH[%]  = -6  + 125 × raw / 65535  → 0~100 으로 자름(데이터시트 권고)
 *
 * ── 진행 방식 (논블로킹) ──
 *   SHT45_PERIOD_MS 마다 한 바퀴: 센서마다 [채널 선택 → 0xFD] → 10ms 뒤 [채널 선택 → 6바이트 읽기].
 *   채널 선택은 매 트랜잭션 앞에 다시 하므로, 그 사이 다른 모듈(ADS1115·BH1750)이 버스를 써도 된다.
 *   한 바퀴가 끝나면 채널을 전부 끈다. 3번 연속 실패한 센서는 없음(NAN)으로 보고 다시 찾는다.
 *
 * ── 보고 (사용자 결정 09-22 J40) ──
 *   평균 temperature/humidity 하나 + 개별 temp_1~3/hum_1~3. 없는 자리는 null.
 *   히터는 쓰지 않는다. 케이지 안 응결이 문제가 되면 그때 검토.
 */

#ifndef SHT45_H
#define SHT45_H

#include <Arduino.h>

namespace sht45 {

// 버스와 멀티플렉서(mux::begin)가 준비된 뒤 부릅니다. 8채널을 훑어 자리를 배정합니다.
void begin();

// 루프마다 호출. 한 센서씩 명령·읽기를 진행하고, 빈 자리가 있으면 5초마다 다시 찾습니다.
void tick();

bool    viaMux();                 // 멀티플렉서 경유인지 (false = 버스 직결 1개)
uint8_t count();                  // 지금 응답하는 센서 수 (0~SHT45_MAX)
bool    ok(uint8_t slot);         // slot 0~SHT45_MAX-1 (자리 = 부팅 때 채널 낮은 순 배정, 이후 유지)
float   temperature(uint8_t slot);  // °C. 없으면 NAN
float   humidity(uint8_t slot);     // %RH. 없으면 NAN
bool    anyOk();
float   avgTemperature();         // 응답하는 센서 평균. 없으면 NAN
float   avgHumidity();
uint8_t channelOf(uint8_t slot);  // 그 자리에 배정된 멀티플렉서 채널 번호. 없음·직결 = I2C_MUX_CH_NONE(0xFF)
const char* channelListCsv();     // status sht45_channels 용: 자리 순 "0,2,-" (빈 자리 '-', 직결 'bus')
// 판별된 모델: "SHT4x" / "SHT3x" / "?"(응답은 하나 판별 실패 → SHT4x 명령으로 시도) / ""(없음)
// 2026-09-29 저녁 추가: 첫 실기에서 0x44 는 응답하는데 측정이 안 돼 SHT3x 가능성을 열어 둠.
const char* model(uint8_t slot);
const char* modelSummary();       // status 용 한 단어 (자리 1 기준, 없으면 "none")

void reportSerial();

}  // namespace sht45

#endif  // SHT45_H
