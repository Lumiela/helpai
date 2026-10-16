/*
 * 통신 모듈 — Wi-Fi + NTP + TLS MQTT + 아라온실 VALUE v1 프레임
 * ------------------------------------------------------------------
 * 2026-09-22 병합. 젯슨 시험 스케치(smartcube_esp32.ino)의 통신 부분을 팀 통합본의
 * 모듈 구조(fan/motor/led/soil)에 맞춰 떼어낸 것입니다. 통합본이 쓰던
 * AGEdgeConnectorESP32 는 평문 MQTT 전용이라 젯슨(TLS 8443, 계정 필수)에 붙지 못해
 * PubSubClient + WiFiClientSecure 로 바꿨습니다. 프레임 규격은 같습니다.
 *
 *   ESP32 --Wi-Fi--> 인터넷 --TLS 8443(Tailscale Funnel)--> 젯슨 mosquitto
 *
 * ── 토픽 (device/<DEVICE_NAME>/<DEVICE_SN>) ───────────────────────
 *   발행  /public          텔레메트리 (TELEMETRY_INTERVAL_MS 마다, 요청 시 즉시)
 *         /status          접속 직후·그룹 요청 시
 *         /ack             제어 명령 회신
 *   구독  device/<DEVICE_NAME>            그룹(디스커버리) → status 발행
 *         …/notify/#                       control · config · public/request
 *
 * ── 제어 프레임 (…/notify/control) ────────────────────────────────
 *   {"session":"…","seq":N,"pump":bool,"led1":0~100,"led2":0~100,"fan1":0~100,"fan2":0~100}
 *   · session 은 이 모듈이 접속마다 새로 발급해 status·telemetry 에 실어 보낸 값과 같아야 합니다.
 *   · seq 는 세션 안에서 증가해야 합니다(거절된 명령의 seq 도 소비됩니다).
 *   · 검증(프레임·필드·세션·순번)은 여기서 끝내고, 출력 적용은 .ino 의 콜백이 합니다.
 *   회신: {"session":"…","seq":N,"result":"ok|denied|outputs_disabled|pump_locked|
 *          stale_session|duplicate_or_old_seq|invalid_fields|invalid_frame"}
 *
 * ── 설정 프레임 (…/notify/config) ─────────────────────────────────
 *   팀 통합본의 {"air":…,"water":…} / {"led_mode":…} 를 그대로 받습니다.
 *   본문(JSON)을 .ino 의 onConfig 콜백에 넘깁니다. jsonInt() 로 꺼내 쓰면 됩니다.
 */

#ifndef COMM_H
#define COMM_H

#include <Arduino.h>

namespace comm {

// 검증을 끝낸 제어 명령. .ino 가 출력에 적용하고 결과 문자열을 돌려줍니다.
struct ControlCmd {
  uint32_t seq;
  uint8_t  led1, led2, fan1, fan2;
  bool     pump;
};

struct Callbacks {
  const char* (*onControl)(const ControlCmd& cmd);     // 반환값이 그대로 ack 의 result
  void (*onConfig)(const char* json, uint16_t len);     // notify/config 본문
  void (*onRequestTelemetry)();                         // notify/public · notify/request
  void (*onRequestStatus)();                            // 그룹 토픽 · 접속 직후
};

// secrets.h 의 Wi-Fi·브로커 값으로 시작합니다. 자리표시자가 남아 있으면 네트워크를
// 켜지 않고 false 를 돌려줍니다(센서·시리얼은 그대로 됩니다).
bool begin(const Callbacks& callbacks);

// 재접속·keepalive·수신 처리. 루프마다 부릅니다.
void tick();

bool wifiConnected();
bool connected();          // MQTT 접속 여부
bool timeReady();          // NTP 동기화 여부 (이전에는 TLS 접속을 미룹니다)
bool networkEnabled();     // begin() 이 네트워크를 켰는지
const char* session();     // 현재 세션(32자 hex)
uint32_t lastReceivedSeq();
uint32_t publishedCount();
void reconnect();          // 시리얼 r

// JSON 을 VALUE v1 프레임으로 싸서 device/<앱>/<SN><suffix> 에 발행합니다.
// suffix 는 "/public" · "/status" 처럼 앞에 / 를 붙입니다. printf 형식입니다.
// ★ 값은 정수·true/false·null·문자열만. 젯슨 중계기는 숫자만 InfluxDB 로 넘깁니다.
bool publishf(const char* suffix, const char* fmt, ...) __attribute__((format(printf, 2, 3)));
bool publishAck(uint32_t seq, const char* result);

// 평문 1단계 JSON 에서 정수 값 하나. 팀 라이브러리 Message::getJSON(key, int*) 대체.
// strstr 기반이라 키가 서로의 접두어면 안 됩니다("air"/"water"/"led_mode" 는 안전).
bool jsonInt(const char* json, uint16_t len, const char* key, int* out);

// 시리얼 i: 주소·신호·시계·브로커·세션·힙
void printNetInfo();

}  // namespace comm

#endif  // COMM_H
