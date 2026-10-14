/*
 * 팬 제어 모듈 — 릴레이 전원 차단 + PWM 속도 제어 + TACH 회전수 측정
 * ------------------------------------------------------------------
 * fan_control.ino 의 검증된 로직을 그대로 옮겼습니다. 특히 아래 두 가지는
 * 실기에서 겪은 문제의 해결책이므로 건드리지 마세요.
 *
 *   1) 0% 는 PWM 만 0이 아니라 릴레이로 12V 를 끊습니다.
 *      저듀티에서 팬이 덜덜거리거나 기동하지 못하는 문제를 피합니다.
 *
 *   2) TACH 는 원시 에지가 아니라 500us 이상 LOW 펄스만 셉니다.
 *      PWM 스위칭 노이즈가 TACH 선에 실려 원시 에지가 부풀기 때문입니다.
 *      원시 계수를 주파수의 절반만큼 빼는 식의 보정은 쓰지 않습니다.
 *
 * 원본 대비 바뀐 점은 fan.cpp 상단 주석에 정리했습니다.
 */

#ifndef FAN_H
#define FAN_H

#include <Arduino.h>

namespace fan {

// PWM 채널 초기화. false 면 이후 모든 팬 동작이 거부됩니다.
bool begin();

// 루프마다 호출. TACH 안정화 처리와 1초 측정 창을 돌립니다.
void tick();

// 0 = 릴레이로 전원 차단, 1~100 = 전원 ON + PWM 듀티.
// false 면 PWM 설정 실패(fault 상태로 전환됨).
bool setPercent(uint8_t percent);

// 진단용. 듀티를 유지하고 주파수만 바꿉니다.
bool setFrequency(uint32_t hz);

uint8_t  percent();
bool     relayOn();
uint32_t frequencyHz();

// 분당 회전수. -1 = 측정 불가(전원 OFF, 안정화 대기, TACH 미검출).
int rpm();

// 전원 ON + duty>0 인데 일정 시간 유효 TACH 가 없음 = 팬 미연결/스톨.
bool stalled();

// PWM 설정에 실패해 팬을 비활성화한 상태. 원격 진단을 위해 유지됩니다.
bool faulted();
const char* faultReason();

// 1초 주기 시리얼 상태 출력 (원본 reportStatus 대응).
void reportSerial();

}  // namespace fan

#endif  // FAN_H
