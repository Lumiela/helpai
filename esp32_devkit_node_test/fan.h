/*
 * 팬 제어 모듈 — 릴레이 전원 차단 + PWM 속도 제어 + TACH 회전수 측정
 * ------------------------------------------------------------------
 * fan_control.ino 의 검증된 로직을 그대로 옮겼습니다. 특히 아래 두 가지는
 * 실기에서 겪은 문제의 해결책이므로 건드리지 마세요.
 *
 *   1) 0% 는 PWM 만 0이 아니라 릴레이로 12V 를 끊습니다.
 *      저듀티에서 팬이 덜덜거리거나 기동하지 못하는 문제를 피합니다.
 *      (1.7.0: 릴레이가 없는 모듈은 PWM 0 만. 전원은 끊기지 않습니다.)
 *
 *   2) TACH 는 원시 에지가 아니라 500us 이상 LOW 펄스만 셉니다.
 *      PWM 스위칭 노이즈가 TACH 선에 실려 원시 에지가 부풀기 때문입니다.
 *      원시 계수를 주파수의 절반만큼 빼는 식의 보정은 쓰지 않습니다.
 *
 * 원본 대비 바뀐 점은 fan.cpp 상단 주석에 정리했습니다.
 *
 * ── 두 대 지원 (2026-10-13, 사용자 결정: 순환팬도 4핀 PWM 팬) ──
 *   id 0 = 배기팬 fan1, id 1 = 순환팬 fan2. 모든 함수의 id 기본값이 0 이라 기존 호출(배기팬)은 그대로 동작합니다.
 *   두 모듈의 동작 코드는 같은 경로이고 핀·상태만 모듈별로 따로 둡니다. ENABLE_FAN / ENABLE_FAN2 가 0 인 모듈은 begin 에서 건너뛰고 모든 호출이 false 입니다.
 *   ★ PWM 주파수 진단(setFrequency)은 두 모듈이 LEDC 타이머를 공유하므로 둘에 함께 적용됩니다.
 *
 * ── 모듈마다 팬 2대 = TACH 2개 (1.7.0, 2026-10-23 사용자 지시·결정) ──
 *   팬 모듈 2(배기 fan1): 릴레이 33 · PWM 32 · TACH 34(팬 1)·35(팬 2).  팬 모듈 1(순환 fan2): 릴레이 없음 · PWM 13 · TACH 14(팬 1)·27(팬 2).
 *   PWM 은 두 팬 공통, 회전수는 팬마다 따로(rpm(id, tach)). 스톨은 어느 한 팬이라도 duty>0 에서 TACH 가 없으면(stalled(id)).
 *   릴레이 없는 모듈은 12V 가 상시라 TACH 를 늘 세고(부팅 뒤 안정화 시간 지나면), duty 0 이면 스톨 판정만 하지 않습니다.
 */

#ifndef FAN_H
#define FAN_H

#include <Arduino.h>

namespace fan {

static const uint8_t FAN_EXHAUST    = 0;   // 배기팬 fan1 (팬 모듈 2)
static const uint8_t FAN_CIRC       = 1;   // 순환팬 fan2 (팬 모듈 1)
static const uint8_t FAN_COUNT      = 2;
static const uint8_t FAN_TACH_COUNT = 2;   // 모듈당 TACH 수 (1.7.0)

// 켜진 모듈을 모두 초기화합니다. 하나라도 PWM 초기화에 실패하면 false (그 모듈만 fault).
bool begin();

// 루프마다 호출. 모듈마다 TACH 안정화 처리와 1초 측정 창을 돌립니다.
void tick();

// 컴파일 플래그로 켜진 모듈인지 (ENABLE_FAN / ENABLE_FAN2).
bool enabled(uint8_t id);

// 0 = 릴레이로 전원 차단(릴레이 없는 모듈은 PWM 0), 1~100 = 전원 ON + PWM 듀티.
// false 면 PWM 설정 실패(fault 상태로 전환됨) 또는 꺼진 모듈.
bool setPercent(uint8_t percent, uint8_t id = FAN_EXHAUST);

// 진단용. 듀티를 유지하고 주파수만 바꿉니다. 타이머 공유 때문에 켜진 모듈 모두에 적용됩니다.
bool setFrequency(uint32_t hz);

uint8_t  percent(uint8_t id = FAN_EXHAUST);
bool     hasRelay(uint8_t id = FAN_EXHAUST);   // 릴레이 핀이 있는 모듈인지
bool     relayOn(uint8_t id = FAN_EXHAUST);    // 전원 ON 상태 (릴레이 없는 모듈은 항상 true)
uint32_t frequencyHz(uint8_t id = FAN_EXHAUST);

// 분당 회전수. tach 0 = 팬 1, 1 = 팬 2. -1 = 측정 불가(전원 OFF, 안정화 대기, TACH 미검출, 꺼진 모듈).
int rpm(uint8_t id = FAN_EXHAUST, uint8_t tach = 0);

// 전원 ON + duty>0 인데 일정 시간 유효 TACH 가 없음 = 팬 미연결/스톨. 모듈 단위 = 어느 한 팬이라도.
bool stalled(uint8_t id = FAN_EXHAUST);
bool stalledTach(uint8_t id, uint8_t tach);

// PWM 설정에 실패해 모듈을 비활성화한 상태. 원격 진단을 위해 유지됩니다.
bool faulted(uint8_t id = FAN_EXHAUST);
const char* faultReason(uint8_t id = FAN_EXHAUST);

// 시리얼 상태 출력. 켜진 모듈을 모두 한 줄씩 (원본 reportStatus 대응).
void reportSerial();

}  // namespace fan

#endif  // FAN_H
