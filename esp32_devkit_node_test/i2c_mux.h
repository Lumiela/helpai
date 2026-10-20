/*
 * I2C 멀티플렉서 PCA9548A — 채널 선택 (2026-09-29)
 * ------------------------------------------------------------------
 * SHT45 는 I2C 주소가 0x44 하나로 고정이라 여러 개를 한 버스에 못 답니다. 팀 하드웨어 구성도
 * (09-22)와 사용자 결정(09-22 J40, 09-29)대로 PCA9548A 로 채널을 나눠 붙입니다.
 *
 *   v2 (2026-10-16, 사용자 결정): I2C 센서는 전부 채널 뒤. 버스에 직결된 것은 멀티플렉서뿐.
 *   ESP32 GPIO21/22 ──── PCA9548A 0x70 ─┬─ ch0: SHT45 0x44
 *                                        ├─ ch1: SHT45 0x44
 *                                        ├─ ch2: SHT45 0x44
 *                                        ├─ ch3: ADS1115 0x48 (토양)
 *                                        ├─ ch4: BH1750 0x23/0x5C (조도 1)
 *                                        └─ ch5: BH1750 0x23/0x5C (조도 2)
 *
 * 제어 레지스터 한 바이트가 채널 마스크입니다(bit n = 채널 n). 한 번에 한 채널만 켜고,
 * 다 쓰면 0 을 써서 전부 끕니다 — 두 채널이 같이 열리면 같은 주소의 센서 둘이 동시에 답해 값이 깨집니다.
 * select() 는 이미 켜진 채널이면 쓰기를 생략하므로 모듈마다 매 트랜잭션 앞에 불러도 부담이 없습니다.
 * 멀티플렉서가 없으면(응답 없음) present() 가 false 이고, 센서 모듈들은 버스 직결로 동작합니다(벤치용).
 */

#ifndef I2C_MUX_H
#define I2C_MUX_H

#include <Arduino.h>

namespace mux {

// 버스가 이미 열린 뒤(.ino 의 Wire.begin) 부릅니다. 멀티플렉서를 찾고 전 채널을 끕니다.
void begin();

// 없을 때 주기적으로 다시 찾습니다.
void tick();

bool    present();
bool    select(uint8_t channel);   // 0~7 한 채널만 켬. 없거나 실패면 false
bool    deselectAll();             // 전 채널 끔
uint8_t current();                 // 켜진 채널. 0xFF = 없음
void    reportSerial();

}  // namespace mux

#endif  // I2C_MUX_H
