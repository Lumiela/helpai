/*
 * LED 모듈 — MOSFET(또는 직결) PWM, 우노 통합 스케치의 일반 모드 이식
 * ------------------------------------------------------------------
 * 우노 통합 스케치의 LED 부분(D10, Timer1 OC1B)을 ESP32 LEDC 로 옮긴 것입니다.
 * 원본 esp32_cube_node 의 led 모듈은 "진단 모듈"(보정·램프 없음, 주파수 6단계)이었지만,
 * 이 판은 우노가 실기에서 정상 동작을 확인한 일반 모드 규칙을 그대로 따릅니다.
 *
 *   · 25kHz 고정 (우노 일반 모드와 동일). 주파수 진단은 motor 모듈의 진단 모드가 맡습니다.
 *   · 감마 보정 (LED_GAMMA_CORRECTION): 카운트 = FULL × 입력% × 입력% / 10000.
 *     사람 눈이 밝기를 로그로 느끼므로 입력 50% 가 실제 duty 25% 가 됩니다.
 *     우노와 같이 입력 1~3% 는 카운트 0 이 되어 꺼집니다.
 *   · 램프 (LED_RAMP_STEP_MS): 목표까지 1%씩 이동. 0→100% 약 0.5초.
 *
 * .ino 가 쓰는 함수 이름은 연결판(esp32_cam_uno_link)의 led namespace 와 같습니다.
 * 주파수 모드 함수(setModeIndex 등)는 호환용으로만 남겨 항상 거부합니다.
 *
 * 배선: GPIO33(config.h PIN_LED_PWM, v2) → 100Ω → 로직레벨 MOSFET 게이트, 게이트↔GND 10kΩ 풀다운.
 *       소형 LED 라면 GPIO33 → 220Ω → LED 애노드, 캐소드 → GND 로 직결해도 됩니다.
 *       (ESP32-CAM 판은 GPIO2, DevKitC 1.x 판은 GPIO16 이었습니다. v2 에서 설비 열(J2)의 GPIO33 으로 옮김, 2026-10-16)
 */

#ifndef LED_H
#define LED_H

#include <Arduino.h>

namespace led {

// PWM 채널 초기화. false 면 이후 모든 LED 동작이 거부됩니다.
bool begin();

// 루프마다 호출. 램프를 한 스텝씩 진행합니다.
void tick();

// 목표 밝기(%). 0 = 소등. 실제 출력은 램프를 거쳐 도달합니다.
bool setPercent(uint8_t percent);

// 램프를 건너뛰고 즉시 소등. 모드 전환·비상 정지 경로에서 씁니다.
void stopNow();

uint8_t  percent();        // 목표 밝기 % (우노 ledSet)
uint8_t  output();         // 지금 실제 출력 % (우노 ledOut. 램프 중이면 percent 와 다름)
bool     ramping();
uint32_t rawDuty();        // 현재 LEDC duty 카운트 (우노 OCR1B 대응)
uint32_t rawMax();         // (1<<bits)-1
uint32_t frequencyHz();    // LED_PWM_HZ 고정

// 호환용. 이 판은 25kHz 고정이라 항상 거부합니다.
bool     setModeIndex(uint8_t index);
bool     nextMode();
uint8_t  modeIndex();      // 항상 0
const char* modeListCsv(); // "25kHz"
void     printModes();

bool faulted();
const char* faultReason();

// 1초 주기 시리얼 상태 출력.
void reportSerial();

}  // namespace led

#endif  // LED_H
