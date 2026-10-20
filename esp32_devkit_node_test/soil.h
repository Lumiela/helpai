/*
 * 토양 수분 센서 모듈 — ADS1115(I2C) 로 읽고, 우노 방식 10회 평균 + 보정, NVS 저장, 원격 보정
 * ------------------------------------------------------------------
 * ESP32-CAM 판은 내부 ADC 를 쓸 수 없어 I2C 외부 ADC 인 ADS1115 로 읽었고, DevKitC 판도 코드·눈금·
 * 원격 보정을 그대로 쓰기 위해 ADS1115 를 유지합니다(사용자 결정 2026-09-29, config.h 머리말).
 * I2C 핀만 14/15 → 21/22 로 바뀌었습니다(config.h PIN_I2C_SDA/SCL).
 * 라이브러리 없이 레지스터 두 개(설정 0x01 · 변환 0x00)만 직접 다룹니다.
 * v2(2026-10-16, 사용자 결정): PCA9548A 채널 ADS1115_MUX_CHANNEL(3) 뒤에 둡니다. 매 트랜잭션 앞에 채널을 고르고,
 * 멀티플렉서가 없으면 버스 직결로 찾습니다(나타나면 경유로 전환).
 *
 * ── 샘플링 (우노 updateSoilSampler 와 동일한 개수·간격, 논블로킹) ──
 *   10ms 마다 단발 변환을 시작하고, 끝나면 읽어 모읍니다. 10개가 모이면 반올림 평균이
 *   raw() 가 됩니다. 한 묶음 약 0.1초.
 *
 * ── 눈금 ──
 *   raw 는 ADS1115 원시값(PGA ±4.096V, 1 LSB = 0.125mV, 0~32767). 음수는 0 으로 자릅니다.
 *   millivolts() = raw × 0.125. 팀 중계기 시험 발행기(fake_device.py)와 같은 눈금입니다.
 *   moisture() 는 air(0%)·water(100%) 사이의 상대 지표이지 체적 함수율이 아닙니다.
 *
 * ── 보정 (우노 ca / cw / ca<raw> / cw<raw> / c / cr) ──
 *   startCalibration(): 새 묶음 10개를 모아 그 평균을 air 또는 water 로 저장합니다.
 *   setCalibration()/setAir()/setWater(): 값을 직접 지정합니다.
 *   resetCalibration(): config.h 초기값으로 되돌립니다.
 *   바뀐 값은 NVS(Preferences, 이름공간 "soil")에 저장되어 재부팅 후에도 유지됩니다
 *   (사용자 결정 2026-09-28). 우노는 RAM 에만 두었습니다.
 *
 * ── 원격 보정 ──
 *   서버가 notify/config 로 {"cal_air":1} / {"cal_water":1} / {"cal_reset":1} /
 *   {"air":N,"water":N} 을 보내면 .ino 의 onConfig 가 위 함수들을 부릅니다.
 *   측정 보정이 끝나면 pollCalibrationDone() 이 한 번 true 가 되고, .ino 가 그때
 *   status(air/water) 를 다시 발행합니다.
 *
 * ── 연결 끊김 ──
 *   ADS1115 가 응답하지 않으면 ok() 가 false, raw()/moisture() 가 -1 이 되고 5초마다
 *   다시 찾습니다. 텔레메트리는 ads1115_ok=false, 값 null 로 나갑니다.
 */

#ifndef SOIL_H
#define SOIL_H

#include <Arduino.h>

namespace soil {

// I2C 를 열고 ADS1115 를 찾고 NVS 보정값을 읽습니다.
void begin();

// 루프마다 호출. 샘플을 하나씩 모으고, 새 평균이 완성된 순간에만 true.
bool tick();

bool ok();            // ADS1115 가 응답하는지
int  raw();           // 10회 평균 원시값(0~32767). -1 = 아직 없음 / ADS1115 없음
int  millivolts();    // raw × 0.125 (정수 mV). -1 = 없음
int  moisture();      // 0~100 상대 지표. -1 = 없음 / 미보정(air == water)
bool calibrated();    // air != water

// 보정값 직접 지정. 범위 밖이거나 air == water 가 되면 거부(false).
bool setCalibration(int airValue, int waterValue);
bool setAir(int value);
bool setWater(int value);

// 측정 보정: 다음 10회 묶음의 평균을 air(true) / water(false) 로 저장. ADS1115 없으면 false.
bool startCalibration(bool air);
bool calibrating();                 // 측정 보정 대기 중
const char* calibratingWhat();      // "air" / "water" / ""
bool pollCalibrationDone();         // 측정 보정이 끝난 뒤 한 번만 true (읽으면 지워짐)

// config.h 초기값으로 되돌리고 NVS 를 지웁니다.
void resetCalibration();

int  airValue();
int  waterValue();

void printCal();       // 보정값·경고 출력 (우노 printSoilCal)
void reportSerial();

}  // namespace soil

#endif  // SOIL_H
