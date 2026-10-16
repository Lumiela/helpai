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
 *
 * ── 두 대 지원 (2026-10-13, 사용자 결정: 순환팬도 4핀 PWM 팬) ──
 *   id 0 = 배기팬 fan1 (릴레이 25 · PWM 26 · TACH 34), id 1 = 순환팬 fan2 (릴레이 19 · PWM 23 · TACH 35).
 *   모든 함수의 id 기본값이 0 이라 기존 호출(배기팬)은 그대로 동작합니다. 두 팬의 동작 코드는 같은 경로이고
 *   핀·상태만 팬별로 따로 둡니다. ENABLE_FAN / ENABLE_FAN2 가 0 인 팬은 begin 에서 건너뛰고 모든 호출이 false 입니다.
 *   ★ PWM 주파수 진단(setFrequency)은 두 팬이 LEDC 타이머를 공유하므로 둘에 함께 적용됩니다.
 */

#ifndef FAN_H
#define FAN_H

#include <Arduino.h>

namespace fan {

static const uint8_t FAN_EXHAUST = 0;   // 배기팬 fan1
static const uint8_t FAN_CIRC    = 1;   // 순환팬 fan2
static const uint8_t FAN_COUNT   = 2;

// 켜진 팬을 모두 초기화합니다. 하나라도 PWM 초기화에 실패하면 false (그 팬만 fault).
bool begin();

// 루프마다 호출. 팬마다 TACH 안정화 처리와 1초 측정 창을 돌립니다.
void tick();

// 컴파일 플래그로 켜진 팬인지 (ENABLE_FAN / ENABLE_FAN2).
bool enabled(uint8_t id);

// 0 = 릴레이로 전원 차단, 1~100 = 전원 ON + PWM 듀티.
// false 면 PWM 설정 실패(fault 상태로 전환됨) 또는 꺼진 팬.
bool setPercent(uint8_t percent, uint8_t id = FAN_EXHAUST);

// 진단용. 듀티를 유지하고 주파수만 바꿉니다. 타이머 공유 때문에 켜진 팬 모두에 적용됩니다.
bool setFrequency(uint32_t hz);

uint8_t  percent(uint8_t id = FAN_EXHAUST);
bool     relayOn(uint8_t id = FAN_EXHAUST);
uint32_t frequencyHz(uint8_t id = FAN_EXHAUST);

// 분당 회전수. -1 = 측정 불가(전원 OFF, 안정화 대기, TACH 미검출, 꺼진 팬).
int rpm(uint8_t id = FAN_EXHAUST);

// 전원 ON + duty>0 인데 일정 시간 유효 TACH 가 없음 = 팬 미연결/스톨.
bool stalled(uint8_t id = FAN_EXHAUST);

// PWM 설정에 실패해 팬을 비활성화한 상태. 원격 진단을 위해 유지됩니다.
bool faulted(uint8_t id = FAN_EXHAUST);
const char* faultReason(uint8_t id = FAN_EXHAUST);

// 시리얼 상태 출력. 켜진 팬을 모두 한 줄씩 (원본 reportStatus 대응).
void reportSerial();

}  // namespace fan

#endif  // FAN_H
