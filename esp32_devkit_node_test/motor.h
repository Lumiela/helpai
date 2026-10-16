/*
 * 12V DC 모터(급수 펌프) 모듈 — N-MOSFET 로우사이드 + LEDC PWM, 우노 통합 스케치 이식
 * ------------------------------------------------------------------
 * 우노 통합 스케치의 모터 부분(D9)을 ESP32 LEDC 로 옮긴 것입니다. 원본 esp32_cube_node
 * 의 motor 모듈에 우노의 진단 모드(d 명령)를 더했습니다.
 *
 * ── 일반 모드 (우노 m 명령) ────────────────────────────────────────
 *   1) 최소 duty 보정 (MOTOR_MIN_DUTY_PERCENT = 83)
 *      실측 결과 이 모터는 duty 83% 아래에서 회전을 유지하지 못합니다.
 *      입력 1~100% 를 실제 duty 83~100% 구간에 비례 배분하되, 정수 % 로 먼저 반올림하면
 *      83~100 이 18단계뿐이라 입력 1~5 가 뭉개지므로 PWM 카운트를 직접 계산합니다(우노 motorOcr).
 *   2) 램프 (MOTOR_RAMP_STEP_MS = 5)
 *      목표까지 1%씩 이동. 0→100% 약 0.5초. 돌입 전류·기계 충격을 줄이기 위한 것.
 *
 * ── 진단 모드 (우노 d 명령) ────────────────────────────────────────
 *   같은 핀을 보정·램프 없는 raw duty(0~100%)로 구동하고 주파수를 f0~f5 로 바꿉니다.
 *   판독 방법은 우노 스케치 머리말과 같습니다:
 *     [실험 A] f0(25kHz)에서 100 → 1%씩 내린다.
 *       99~95% 에서 바로 꺼짐      → 게이트가 완전히 열리지 않음(게이트 구동 부족 / 로직레벨 아님)
 *       30~60% 에서 툭 꺼짐        → 부하 내부 정전류 드라이버가 초핑 전원에서 재기동 반복
 *       부드럽게 줄다가 꺼짐       → 정상
 *     [실험 B] 꺼지는 duty 로 맞춘 채 f 로 주파수를 낮춘다.
 *       낮은 주파수에서 켜짐       → MOSFET 이 25kHz 를 못 따라감(품번·게이트 저항 100Ω 의심)
 *       저주파에서만 켜지고 깜빡임 → PWM 이 눈에 보이는 것. 실사용 1kHz 이상
 *       어느 주파수에서도 꺼짐     → 전력 경로 문제(전원 용량·배선·공통 GND)
 *   진단 모드에서는 .ino 가 서버 제어를 outputs_disabled 로 거부합니다.
 *   ★ 이 핀에는 펌프가 달려 있으므로 .ino 의 PUMP_MAX_MS 가 진단 모드에도 적용됩니다.
 *
 * ── 배선 주의 (우노 → ESP32) ───────────────────────────────────────
 *   게이트 구동 전압이 5V → 3.3V 입니다. 로직레벨 MOSFET(IRLZ44N·IRLB8721 등)이어야 합니다.
 *   게이트↔GND 10kΩ 풀다운은 필수입니다(부팅·모드 전환 중 핀이 뜨는 순간을 막음).
 *   플라이백 다이오드·공통 접지는 우노와 같습니다.
 */

#ifndef MOTOR_H
#define MOTOR_H

#include <Arduino.h>

namespace motor {

// PWM 채널 초기화. false 면 이후 모든 모터 동작이 거부됩니다.
bool begin();

// 루프마다 호출. 램프를 한 스텝씩 진행합니다(진단 모드에서는 할 일 없음).
void tick();

// [일반] 목표 속도(%). 0 = 정지. 실제 출력은 램프를 거쳐 도달합니다.
// 진단 모드이거나 fault 면 false.
bool setPercent(uint8_t percent);

// [일반] 램프를 건너뛰고 즉시 정지. 비상 정지·모드 전환 경로에서 씁니다.
void stopNow();

uint8_t  target();      // 목표 속도 %
uint8_t  output();      // 지금 실제로 나가고 있는 속도 % (램프 중이면 target 과 다름)
bool     ramping();     // output != target
uint32_t rawDuty();     // 현재 LEDC duty 카운트 (우노 OCR1A 대응)
uint32_t rawMax();      // 현재 분해능의 최대 카운트 (우노 ICR1 대응)
uint32_t frequencyHz(); // 현재 PWM 주파수 (진단 모드면 선택된 단계)
uint8_t  minDutyPercent();

bool faulted();
const char* faultReason();

// ── 진단 모드 (우노 d / n / 0~100 / f / f0~f5) ──
bool diagEnter();                  // 진단 모드 진입: 출력 0, f0(25kHz)
bool diagExit();                   // 일반 모드 복귀: 출력 0, 25kHz/10bit 로 다시 붙임
bool diagActive();
bool diagSetDuty(uint8_t percent); // raw duty %. 보정·램프 없이 즉시 적용
bool diagSetModeIndex(uint8_t index);
bool diagNextMode();
uint8_t  diagDuty();
uint8_t  diagModeIndex();
const char* diagModeLabel();
void printDiagModes();

// 1초 주기 시리얼 상태 출력.
void reportSerial();

}  // namespace motor

#endif  // MOTOR_H
