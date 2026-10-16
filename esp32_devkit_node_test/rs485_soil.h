/*
 * RS485 토양 7종 센서 모듈 — Modbus-RTU, 온도·수분·EC·염분·N·P·K·pH (2026-10-12)
 * ------------------------------------------------------------------
 * 센서: 토양 다기능 센서(설명서 요약본 "Sc90d138968ba4d2db3fff3e1678df98dI.pdf - 자세한 리포트.md" 기준).
 *   전원 DC 3.3~24V(이 회로는 센서 기판 5V), RS485 Modbus-RTU, 기본 주소 2, 9600 bps 8N1.
 *   보유 레지스터 0x0000~0x0007 을 기능 코드 0x03 으로 한 번에 8개 읽습니다:
 *     0 온도 ×0.1°C(부호 있음) · 1 수분 ×0.1% · 2 EC µS/cm · 3 염분 · 4 N · 5 P · 6 K (mg/kg) · 7 pH ×0.1
 *   설명서: 온도·수분·EC 는 5초 안에 안정, 첫 pH 는 삽입 뒤 5분 넘게 걸림. 측정 전류 25mA, 최대 35mA.
 *
 * 변환기: [SMG-A] TTL to RS485 자동 흐름제어 모듈(SZH-CVBE-010, MAX13487E). 방향 전환 핀이 없습니다.
 *   모듈 VCC 5V · GND 공통 · 모듈 RXD ← GPIO17(TX) 직결 · 모듈 TXD → 2.2kΩ 직렬 → GPIO27(RX), GPIO27–GND 3.3kΩ
 *   (5V 출력을 약 3.1V 로 낮춤). 120Ω 종단(R16)은 열어 둡니다(짧은 버스, 자동 방향 전환 방식).
 *   핀은 사용자 결정 2026-10-12 (TX 17 / RX 27). 09-09 설계가 예약한 16 은 09-29 부터 LED 핀이라 못 씁니다.
 *
 * ── 진행 방식 (논블로킹, UART2) ──
 *   RS485_SOIL_PERIOD_MS 마다 요청 8바이트를 보내고 응답(21바이트)을 기다립니다. 응답은 수신 버퍼에서
 *   [주소][0x03][0x10] 머리를 찾아 CRC16 을 검사하므로, 변환기가 요청을 되돌려 보내도(에코) 무시됩니다.
 *   RS485_SOIL_TIMEOUT_MS 안에 유효 응답이 없으면 실패 1회. 연속 실패가 한도에 닿으면 없음(NAN/-1) 처리.
 *
 * ── 주소 (사용자 결정 2026-10-12: 기본 2, 없으면 자동 탐색) ──
 *   RS485_SOIL_SCAN_ADDRS 순서(2 → 1 → 0)로 돌며 응답한 주소에 고정합니다. 0 은 브로드캐스트인데 이 센서는
 *   슬레이브가 하나면 0 에도 응답하고 응답의 주소 바이트는 센서 자신의 주소라, 그 값으로 고정합니다.
 *   고정 뒤 연속 실패로 잃으면 다음 주소부터 다시 탐색합니다. 시리얼 b<addr> 로 수동 지정하면 탐색을 멈춥니다.
 *
 * ── 보고 ──
 *   텔레메트리: soil_rs485_ok · soil_temp · soil_vwc · soil_ec · soil_salinity · soil_n · soil_p · soil_k · soil_ph
 *   (사용자 결정 2026-10-12, soil_ 접두어). 기존 ADS1115 의 moisture_* 와 SHT45 의 temperature 는 그대로입니다.
 *   status: soil_rs485_ok · soil_rs485_addr(-1 = 아직 없음)
 */

#ifndef RS485_SOIL_H
#define RS485_SOIL_H

#include <Arduino.h>

namespace rs485soil {

// UART2 를 열고 첫 요청을 예약합니다. g17 핀 시험 뒤 다시 불러도 됩니다.
void begin();

// 루프마다 호출. 요청 보내기·응답 모으기·시간 초과 판정을 한 단계씩 진행합니다.
void tick();

bool  ok();            // 최근 유효 응답이 있고 아직 잃지 않았는지
bool  scanning();      // 주소 자동 탐색 중(아직 응답한 주소 없음, 수동 지정 아님)
int   address();       // 응답해 고정된 주소. -1 = 아직 없음
uint8_t currentAddress();   // 지금 요청을 보내는 주소

float temperature();   // 토양 온도 °C. 없으면 NAN
float vwc();           // 토양 수분 % (센서 자체 FDR 값). 없으면 NAN
int   ec();            // 전기 전도도 µS/cm. 없으면 -1
int   salinity();      // 염분. 없으면 -1
int   nitrogen();      // N mg/kg. 없으면 -1
int   phosphorus();    // P mg/kg. 없으면 -1
int   potassium();     // K mg/kg. 없으면 -1
float ph();            // pH. 없으면 NAN

// 시리얼 b<addr>: 주소를 수동 지정하고 탐색을 멈춥니다(0~247). 범위 밖이면 false.
bool setAddress(int addr);
// 시리얼 bs: 탐색 목록 처음부터 다시 찾습니다(수동 지정 해제).
void rescan();
// 시리얼 br: 주기를 기다리지 않고 바로 한 번 읽습니다.
void readNow();

void reportSerial();   // 시리얼 s / b: 주소·통계·값·마지막 프레임

}  // namespace rs485soil

#endif  // RS485_SOIL_H
