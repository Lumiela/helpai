/*
 * INA226 12V 입력부 전압·전류 모듈 — I2C 0x40~0x4F, PCA9548A 채널 자동 탐색, 1개 (1.7.0, 2026-10-23)
 * ------------------------------------------------------------------
 * 사용자 지시(2026-10-23): "ina226 센서 데이터도 받을 수 있도록 코드 추가". 결정: 전압·전류만(power_v · power_a · ina226_ok).
 * 모듈 자료(사용자 제공, SZH-MIN057 후보): 0~36V · ±20A 양방향 · 전원 2.7~5.5V · I2C 주소 16개(A0·A1) · ALERT 출력(미사용).
 * 데이터시트 SBOS547C(Downloads/ina226.pdf):
 *   레지스터 0x00 설정(기본 0x4127) · 0x01 션트 전압(부호 있음, LSB 2.5µV, ±81.92mV) · 0x02 버스 전압(LSB 1.25mV, 0~40.96V)
 *   · 0x03 전력 · 0x04 전류 · 0x05 교정 · 0xFE 제조사 ID 0x5449 · 0xFF 칩 ID 0x2260/0x2261(상위 12비트 0x226, 하위 4비트 리비전).
 *   §6.5.3 "교정 없이 사용": 교정 레지스터를 쓰지 않고 션트·버스 전압만 읽어도 된다 → 전류 = 션트 전압 ÷ 션트 저항(config.h INA226_SHUNT_OHM).
 *   모든 레지스터는 16비트 2바이트(상위 먼저).
 *
 * ── 종류 판별 (sht45·bh1750 과 같은 자동 탐색 규칙) ──
 *   멀티플렉서 채널 0~7 을 낮은 번호부터 훑어 0x40~0x4F(0x44 SHT45·0x48 ADS1115 제외)에 응답하는 주소를 찾고,
 *   제조사 ID(0xFE) == 0x5449 이고 칩 ID(0xFF) 상위 12비트 == 0x226 이면 INA226 으로 확정한다(응답 주소만으로는 다른 칩과 구분 못 함).
 *   확정되면 설정 레지스터에 INA226_CONFIG(평균 16회, 1.1ms, 연속)를 쓰고 INA226_PERIOD_MS 마다 션트·버스 전압을 읽는다.
 *   빠지면(3회 연속 실패) 값 null, 5초마다 기억하는 (채널, 주소)를 먼저 다시 찾고 그다음 다른 채널을 찾는다. 멀티플렉서가 없으면 버스 직결.
 *
 * ── 보고 ──
 *   텔레메트리 power_v(버스 전압 V, 소수 2자리) · power_a(전류 A, 소수 3자리, 양방향) · ina226_ok. status ina226_ok · ina226_addr · ina226_channel.
 *
 * ── 배선 (config.h 머리말) ──
 *   IN+ ← 12V 전원(+), IN- → 부하 쪽 12V(+), VBUS 는 모듈에서 IN- 와 이어져 있음(모듈 설명). VCC 3.3V, GND 공통, SDA/SCL → 멀티플렉서 빈 채널.
 */

#ifndef INA226_H
#define INA226_H

#include <Arduino.h>

namespace ina226 {

// 버스와 멀티플렉서(mux::begin)가 준비된 뒤 부릅니다. 8채널을 훑어 자리를 배정합니다.
void begin();

// 루프마다 호출. 1초마다 읽고, 없으면 5초마다 다시 찾습니다.
void tick();

bool    viaMux();           // 멀티플렉서 경유인지
bool    ok();               // 값이 있는지 (텔레메트리 ina226_ok)
float   busVoltage();       // V. 없으면 NAN
float   current();          // A (양방향, IN+ → IN- 가 양). 없으면 NAN
float   shuntMillivolts();  // mV. 없으면 NAN
uint8_t address();          // 응답 중인 주소. 0 = 없음
uint8_t channelOf();        // 배정된 멀티플렉서 채널. 없음·직결 = I2C_MUX_CH_NONE(0xFF)
int     channelForStatus(); // status 용: 채널 번호, 직결 = -2, 없음 = -1

void reportSerial();

}  // namespace ina226

#endif  // INA226_H
