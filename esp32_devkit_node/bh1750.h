/*
 * BH1750(GY-302) 조도 센서 모듈 — I2C 0x23 또는 0x5C (2026-09-29)
 * ------------------------------------------------------------------
 * 근거: `확정된 주제/2026-09-29_BH1750_GY-302_한국어설명서_Codex.html` (팀 공유 설명서).
 *   · ADDR 핀 LOW → 0x23, HIGH → 0x5C. 어느 쪽인지 모르므로 둘 다 찾아본다.
 *   · 전원 인가 직후 Power Down. 0x01(Power On) → 0x10(연속 H-Resolution, 1 lx, 대표 120ms·최대 180ms).
 *   · 2바이트(High → Low) 읽어 raw ÷ 1.2 = lux (MTreg 기본 69).
 *   · 한 트랜잭션에 명령 하나, STOP 으로 끝. 정의되지 않은 opcode 금지.
 *   · 모듈 전원은 3.3V (풀업이 VCC 에 붙은 보드를 5V 로 주면 SDA/SCL 이 5V 가 될 수 있음).
 *
 * 연속 측정 모드라 명령은 부팅 때 한 번, 이후 BH1750_PERIOD_MS 마다 2바이트만 읽는다.
 * 읽기가 3번 연속 실패하면 없음(NAN)으로 보고 5초마다 다시 찾아 재설정한다.
 * 멀티플렉서를 거치지 않고 버스에 직결한다(주소가 두 개라 겹칠 일이 없음).
 */

#ifndef BH1750_H
#define BH1750_H

#include <Arduino.h>

namespace light {

// 버스가 열린 뒤 부릅니다. 0x23 → 0x5C 순으로 찾고 연속 측정을 시작합니다.
void begin();

// 루프마다 호출.
void tick();

bool    ok();
float   lux();        // 마지막 측정값. 없으면 NAN
uint8_t address();    // 0 = 없음
void    reportSerial();

}  // namespace light

#endif  // BH1750_H
