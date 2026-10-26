/*
 * BH1750(GY-302) 조도 센서 모듈 ×2 — I2C 0x23 또는 0x5C, PCA9548A 채널 경유 (v2 2026-10-16)
 * ------------------------------------------------------------------
 * 근거: `확정된 주제/2026-09-29_BH1750_GY-302_한국어설명서_Codex.html` (팀 공유 설명서).
 *   · ADDR 핀 LOW → 0x23, HIGH → 0x5C. 자리마다 둘 다 찾아본다.
 *   · 전원 인가 직후 Power Down. 0x01(Power On) → 0x10(연속 H-Resolution, 1 lx, 대표 120ms·최대 180ms).
 *   · 2바이트(High → Low) 읽어 raw ÷ 1.2 = lux (MTreg 기본 69).
 *   · 한 트랜잭션에 명령 하나, STOP 으로 끝. 정의되지 않은 opcode 금지.
 *   · 모듈 전원은 3.3V (풀업이 VCC 에 붙은 보드를 5V 로 주면 SDA/SCL 이 5V 가 될 수 있음).
 *
 * v2 (사용자 결정 2026-10-16): 조도 센서 2개. 보고는 lux = 값이 있는 자리의 평균, lux_1·lux_2 = 개별(없으면 null),
 *   bh1750_count = 값이 있는 자리 수. 매 트랜잭션 앞에 자기 채널을 고른다(같은 채널이면 생략).
 *   멀티플렉서가 없으면 버스 직결로 동작하며, 그때는 자리마다 주소가 달라야(0x23·0x5C) 둘 다 잡힌다(한 주소는 한 자리만).
 *   멀티플렉서가 나중에 나타나면 경유 모드로 전환하고 자리를 다시 찾는다.
 * 1.6.2 (사용자 결정 2026-10-23): 채널 고정표(4·5)를 없애고 자동 탐색 — 부팅 때 8채널을 훑어 0x23/0x5C 가 응답한 (채널, 주소)를
 *   채널 낮은 순으로 자리 1·2 에 배정한다. 채널이 다르면 둘 다 0x23 이어도 되고, 한 채널에 0x23·0x5C 둘을 두어도 된다.
 *   빠진 자리는 번호를 당기지 않고 그 짝을 먼저 다시 찾는다. 3개째부터는 무시. 자세한 규칙은 bh1750.cpp 머리말.
 * 연속 측정 모드라 명령은 찾을 때 한 번, 이후 BH1750_PERIOD_MS 마다 2바이트만 읽는다.
 * 읽기가 3번 연속 실패하면 그 자리를 없음(NAN)으로 보고 5초마다 다시 찾아 재설정한다.
 */

#ifndef BH1750_H
#define BH1750_H

#include <Arduino.h>

namespace light {

// 버스와 멀티플렉서(mux::begin)가 준비된 뒤 부릅니다. 8채널 × (0x23 → 0x5C) 를 훑어 자리를 배정하고 연속 측정을 시작합니다.
void begin();

// 루프마다 호출. 빈 자리가 있으면 5초마다 다시 찾고, 자리마다 읽기를 진행합니다.
void tick();

bool    viaMux();                  // 멀티플렉서 경유인지 (false = 버스 직결)
uint8_t count();                   // 값이 있는 자리 수 (0~BH1750_MAX)
bool    ok(uint8_t slot);          // 자리 0~BH1750_MAX-1 (자리 = 부팅 때 채널 낮은 순 배정, 이후 유지)
float   lux(uint8_t slot);         // 자리별 lux. 없으면 NAN
uint8_t address(uint8_t slot);     // 자리별 주소. 0 = 없음
uint8_t channelOf(uint8_t slot);   // 그 자리에 배정된 멀티플렉서 채널 번호. 없음·직결 = I2C_MUX_CH_NONE(0xFF)
const char* channelListCsv();      // status bh1750_channels 용: 자리 순 "4,-" (빈 자리 '-', 직결 'bus')

bool    ok();                      // 한 자리라도 값이 있는지
float   lux();                     // 값이 있는 자리의 평균. 없으면 NAN (텔레메트리 lux)
uint8_t address();                 // 첫 응답 자리의 주소. 0 = 없음 (status bh1750_addr 호환)

void    reportSerial();

}  // namespace light

#endif  // BH1750_H
