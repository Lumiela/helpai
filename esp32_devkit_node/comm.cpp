/*
 * comm.cpp — smartcube_esp32.ino 의 네트워크·프레임 부분을 옮긴 것. 바뀐 점:
 *
 *  1) 전역이던 상태를 namespace comm 안으로. 출력 적용은 콜백으로 분리했습니다.
 *     제어 프레임의 검증(프레임·필드·세션·순번)은 여기서 끝내고, 어떤 출력을 어떻게
 *     바꿀지는 .ino 가 정합니다(팀 통합본의 "명령 디스패치는 .ino" 구조 유지).
 *  2) 발행이 printf 형식(publishf)입니다. 팀 통합본이 connector.publishJSON(topic, fmt, …)
 *     로 쓰던 호출 모양을 그대로 살리기 위해서입니다.
 *  3) 토픽 뿌리가 secrets.h 의 DEVICE_NAME/DEVICE_SN 에서 나옵니다(팀 규약).
 *  4) notify/public(젯슨 api) 과 notify/request(팀 서버) 를 둘 다 "지금 텔레메트리" 로 받습니다.
 *
 * 건드리지 않은 것: 프레임 규격, 세션·순번 규칙, 시계가 잡히기 전 TLS 접속 보류,
 * 파서(우노판에서 g++ 로 14가지 입력 통과).
 */

#include "comm.h"
#include "config.h"
#include "secrets.h"
#include "certs.h"

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <esp_random.h>
#include <time.h>
#include <stdarg.h>

namespace comm {
namespace {

// ── 프레임 규격 (AgFrame.h / bridge.py 와 동일) ────────────────────
const uint8_t  MAGIC[4] = { 0xFF, 0xA3, 0x01, 0x01 };
const uint16_t AG_MAX_OPTIONS = 1023;
const uint16_t AG_MAX_JSON    = 1536;
const uint16_t TX_CAP = COMM_FRAME_BUF;
uint8_t txFrame[TX_CAP];

// ── 토픽 ──────────────────────────────────────────────────────────
const char TOPIC_ROOT[]  = "device/" DEVICE_NAME "/" DEVICE_SN;   // 클라이언트 ID 로도 씁니다
const char TOPIC_GROUP[] = "device/" DEVICE_NAME;

// ── 상태 ──────────────────────────────────────────────────────────
WiFiClientSecure secureClient;
PubSubClient     mqtt(secureClient);
Callbacks        cb = { nullptr, nullptr, nullptr, nullptr };

char     sessionId[33] = "";
uint32_t lastSeq = 0;
uint32_t published = 0;
uint32_t wifiAttempt = 0;
uint32_t mqttAttempt = 0;
bool     netEnabled = false;

// ── 작은 도구 ─────────────────────────────────────────────────────
void write32(uint8_t* p, uint32_t n) {
  p[0] = n >> 24; p[1] = n >> 16; p[2] = n >> 8; p[3] = n;
}

uint32_t read32(const uint8_t* p) {
  return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}

bool placeholder(const char* v) {
  return v == nullptr || v[0] == '\0' || strstr(v, "여기에") != nullptr || strstr(v, "YOUR_") != nullptr;
}

void makeSession() {
  snprintf(sessionId, sizeof(sessionId), "%08lx%08lx%08lx%08lx",
           (unsigned long)esp_random(), (unsigned long)esp_random(),
           (unsigned long)esp_random(), (unsigned long)esp_random());
}

// 프레임 옵션. 시각이 잡히면 last-modified 를 붙입니다(젯슨 프로젝트 펌웨어와 같은 모양).
void buildOptions(char* buf, size_t cap) {
  time_t now = time(nullptr);
  if (now > TIME_OK_EPOCH) {
    struct tm utc;
    gmtime_r(&now, &utc);
    char stamp[32];
    strftime(stamp, sizeof(stamp), "%Y-%m-%d %H:%M:%S", &utc);
    snprintf(buf, cap, "data-type=application/json\r\nlast-modified=%s", stamp);
  } else {
    snprintf(buf, cap, "data-type=application/json");
  }
}

// 프레임 머리(매직·옵션)를 쓰고 JSON 이 시작될 위치를 돌려줍니다. 0 = 실패.
uint16_t frameBegin(const char* options) {
  uint16_t opt = strlen(options);
  if (opt > AG_MAX_OPTIONS || (uint16_t)(12 + opt) >= TX_CAP) return 0;
  memcpy(txFrame, MAGIC, 4);
  write32(txFrame + 4, opt);
  memcpy(txFrame + 8, options, opt);
  return 12 + opt;
}

uint16_t frameEnd(uint16_t jsonStart, uint16_t jsonLen) {
  if (jsonLen == 0 || jsonLen > AG_MAX_JSON) return 0;
  write32(txFrame + jsonStart - 4, jsonLen);
  return jsonStart + jsonLen;
}

bool sendFrame(const char* suffix, uint16_t total) {
  if (total == 0 || !mqtt.connected()) return false;
  char topic[160];
  snprintf(topic, sizeof(topic), "%s%s", TOPIC_ROOT, suffix);
  bool ok = mqtt.publish(topic, txFrame, total, false);
  if (ok) published++;
  return ok;
}

// 받은 프레임을 풀어 JSON 본문 위치·길이를 돌려줍니다(AgFrame.h ag::decode 와 같은 검사).
bool decodeFrame(const uint8_t* payload, unsigned int size, const uint8_t** body, uint16_t* len) {
  if (size < 12 || memcmp(payload, MAGIC, 4) != 0) return false;
  uint32_t opt = read32(payload + 4);
  if (opt > AG_MAX_OPTIONS || 12UL + opt > size) return false;
  uint32_t dl = read32(payload + 8 + opt);
  if (dl > AG_MAX_JSON || 12UL + opt + dl != size) return false;
  const uint8_t* b = payload + 12 + opt;
  while (dl && b[dl - 1] == 0) dl--;   // Message::setValue 가 붙이는 꼬리 NUL
  *body = b;
  *len = (uint16_t)dl;
  return true;
}

// ── JSON 파서 (평문 1단계, strstr 기반) ───────────────────────────
const char* findValue(const char* json, uint16_t len, const char* key) {
  char needle[24];
  snprintf(needle, sizeof(needle), "\"%s\"", key);
  uint8_t nlen = strlen(needle);
  for (uint16_t i = 0; i + nlen < len; i++) {
    if (memcmp(json + i, needle, nlen) != 0) continue;
    uint16_t p = i + nlen;
    while (p < len && (json[p] == ' ' || json[p] == ':')) p++;
    return (p < len) ? (json + p) : nullptr;
  }
  return nullptr;
}

int parsePercent(const char* json, uint16_t len, const char* key) {
  const char* v = findValue(json, len, key);
  if (!v || *v < '0' || *v > '9') return -1;
  long n = strtol(v, nullptr, 10);
  return (n >= 0 && n <= 100) ? (int)n : -1;
}

bool parseBool(const char* json, uint16_t len, const char* key, bool* dst) {
  const char* v = findValue(json, len, key);
  if (!v) return false;
  if (strncmp(v, "true", 4) == 0)  { *dst = true;  return true; }
  if (strncmp(v, "false", 5) == 0) { *dst = false; return true; }
  return false;
}

// ── 제어 프레임 ───────────────────────────────────────────────────
void handleControl(const uint8_t* body, uint16_t len) {
  const char* json = (const char*)body;

  const char* sv = findValue(json, len, "session");
  const char* qv = findValue(json, len, "seq");
  if (!sv || !qv || *sv != '"') { publishAck(0, "invalid_fields"); return; }

  ControlCmd cmd;
  cmd.seq = strtoul(qv, nullptr, 10);
  int led1 = parsePercent(json, len, "led1");
  int led2 = parsePercent(json, len, "led2");
  int fan1 = parsePercent(json, len, "fan1");
  int fan2 = parsePercent(json, len, "fan2");
  bool pump = false;
  bool pumpOk = parseBool(json, len, "pump", &pump);
  if (cmd.seq == 0 || led1 < 0 || led2 < 0 || fan1 < 0 || fan2 < 0 || !pumpOk) {
    publishAck(cmd.seq, "invalid_fields");
    return;
  }
  if (strncmp(sv + 1, sessionId, 32) != 0) { publishAck(cmd.seq, "stale_session"); return; }
  if (cmd.seq <= lastSeq) { publishAck(cmd.seq, "duplicate_or_old_seq"); return; }
  lastSeq = cmd.seq;   // 거절되는 명령도 순번을 소비합니다(재전송 구분)

  cmd.led1 = (uint8_t)led1; cmd.led2 = (uint8_t)led2;
  cmd.fan1 = (uint8_t)fan1; cmd.fan2 = (uint8_t)fan2;
  cmd.pump = pump;

  Serial.printf("[RX] control seq=%lu fan1=%u fan2=%u led1=%u led2=%u pump=%s\n",
                (unsigned long)cmd.seq, cmd.fan1, cmd.fan2, cmd.led1, cmd.led2, pump ? "true" : "false");

  const char* result = cb.onControl ? cb.onControl(cmd) : "denied";
  publishAck(cmd.seq, result);
  Serial.printf("  [ACK] seq=%lu result=%s\n", (unsigned long)cmd.seq, result);
}

void onMqttMessage(char* topic, uint8_t* payload, unsigned int size) {
  if (strcmp(topic, TOPIC_GROUP) == 0) {
    if (cb.onRequestStatus) cb.onRequestStatus();
    return;
  }

  size_t rootLen = strlen(TOPIC_ROOT);
  if (strncmp(topic, TOPIC_ROOT, rootLen) != 0) return;
  const char* rest = topic + rootLen;   // "/notify/…"

  if (strcmp(rest, "/notify/public") == 0 || strcmp(rest, "/notify/request") == 0) {
    if (cb.onRequestTelemetry) cb.onRequestTelemetry();
    return;
  }

  const uint8_t* body = nullptr;
  uint16_t len = 0;

  if (strcmp(rest, "/notify/config") == 0) {
    if (!decodeFrame(payload, size, &body, &len)) {
      Serial.println("[RX] config — VALUE 프레임이 아닙니다(평문 JSON?). 무시");
      return;
    }
    if (cb.onConfig) cb.onConfig((const char*)body, len);
    return;
  }

  if (strcmp(rest, "/notify/control") == 0) {
    if (!decodeFrame(payload, size, &body, &len)) { publishAck(0, "invalid_frame"); return; }
    handleControl(body, len);
    return;
  }
}

void mqttConnectAttempt() {
  bool ok;
#ifdef MQTT_USER
  ok = mqtt.connect(TOPIC_ROOT, MQTT_USER, MQTT_PASS);
#else
  ok = mqtt.connect(TOPIC_ROOT);
#endif
  if (!ok) {
    Serial.printf("[MQTT] 접속 실패: state=%d (-2=서버/인증서, 4=계정 오류, 5=권한 없음)\n", mqtt.state());
    return;
  }
  makeSession();      // 접속마다 새 제어 세션. 순번은 0부터.
  lastSeq = 0;
  char filter[160];
  snprintf(filter, sizeof(filter), "%s/notify/#", TOPIC_ROOT);
  if (!(mqtt.subscribe(TOPIC_GROUP, 0) && mqtt.subscribe(filter, 0))) {
    Serial.println("[MQTT] 구독 실패 — 연결을 끊고 다시 시도합니다");
    mqtt.disconnect();
    return;
  }
  Serial.printf("[MQTT] 연결됨. 구독 완료:\n       %s\n       %s\n       session=%s\n",
                TOPIC_GROUP, filter, sessionId);
  if (cb.onRequestStatus) cb.onRequestStatus();
  if (cb.onRequestTelemetry) cb.onRequestTelemetry();
}

}  // namespace

// ──────────────────────────────────────────────────── 공개 API

bool begin(const Callbacks& callbacks) {
  cb = callbacks;
  makeSession();

  if (placeholder(WIFI_SSID) || placeholder(MQTT_HOST)) {
    Serial.println("★ secrets.h 를 먼저 채우세요. 네트워크를 시작하지 않습니다. ★");
    Serial.println("  센서·팬·모터·LED 는 시리얼로 계속 다룰 수 있습니다.");
    netEnabled = false;
    return false;
  }

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  configTime(0, 0, "pool.ntp.org", "time.google.com");

  secureClient.setCACert(MQTT_CA_PEM);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  mqtt.setKeepAlive(MQTT_KEEPALIVE_S);
  mqtt.setSocketTimeout(MQTT_SOCKET_S);
  if (!mqtt.setBufferSize(COMM_FRAME_BUF)) Serial.println("[MQTT][경고] 버퍼 할당 실패");

  wifiAttempt = millis();
  mqttAttempt = millis() - NETWORK_RETRY_MS;
  netEnabled = true;
  Serial.printf("[NET] Wi-Fi %s → %s:%d (TLS) | 클라이언트 %s\n",
                WIFI_SSID, MQTT_HOST, MQTT_PORT, TOPIC_ROOT);
  return true;
}

void tick() {
  if (!netEnabled) return;
  uint32_t now = millis();

  if (WiFi.status() != WL_CONNECTED) {
    if (mqtt.connected()) mqtt.disconnect();
    if ((uint32_t)(now - wifiAttempt) >= NETWORK_RETRY_MS) {
      wifiAttempt = now;
      WiFi.reconnect();
    }
    return;
  }

  if (!mqtt.connected()) {
    // 시계가 잡히기 전에는 붙지 않습니다. 인증서 유효기간 검사가 실패합니다.
    if ((uint32_t)(now - mqttAttempt) < NETWORK_RETRY_MS || !timeReady()) return;
    mqttAttempt = now;
    mqttConnectAttempt();
    return;
  }

  mqtt.loop();
}

bool wifiConnected()       { return WiFi.status() == WL_CONNECTED; }
bool connected()           { return netEnabled && mqtt.connected(); }
bool timeReady()           { return time(nullptr) > TIME_OK_EPOCH; }
bool networkEnabled()      { return netEnabled; }
const char* session()      { return sessionId; }
uint32_t lastReceivedSeq() { return lastSeq; }
uint32_t publishedCount()  { return published; }

void reconnect() {
  if (!netEnabled) { Serial.println("[NET] secrets.h 가 비어 있어 네트워크가 꺼져 있습니다"); return; }
  Serial.println("[NET] 다시 접속합니다");
  mqtt.disconnect();
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  wifiAttempt = millis();
  mqttAttempt = millis() - NETWORK_RETRY_MS;
}

bool publishf(const char* suffix, const char* fmt, ...) {
  if (!mqtt.connected()) return false;
  char opt[80];
  buildOptions(opt, sizeof(opt));
  uint16_t start = frameBegin(opt);
  if (!start) return false;

  char* j = (char*)(txFrame + start);
  uint16_t cap = TX_CAP - start;
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(j, cap, fmt, ap);
  va_end(ap);
  if (n <= 0 || (uint16_t)n >= cap) {
    Serial.printf("[MQTT][경고] %s 프레임이 버퍼(%u)를 넘어 버립니다\n", suffix, (unsigned)cap);
    return false;
  }
  return sendFrame(suffix, frameEnd(start, (uint16_t)n));
}

bool publishAck(uint32_t seq, const char* result) {
  return publishf("/ack", "{\"session\":\"%s\",\"seq\":%lu,\"result\":\"%s\"}",
                  sessionId, (unsigned long)seq, result);
}

bool jsonInt(const char* json, uint16_t len, const char* key, int* out) {
  const char* v = findValue(json, len, key);
  if (!v) return false;
  if (*v != '-' && (*v < '0' || *v > '9')) return false;
  *out = (int)strtol(v, nullptr, 10);
  return true;
}

void printNetInfo() {
  Serial.println("── 네트워크 ───────────────────────────────────");
  if (!netEnabled) {
    Serial.println("  secrets.h 가 비어 있어 네트워크를 켜지 않았습니다");
  } else {
    Serial.printf("  Wi-Fi     : %s (SSID %s)\n",
                  wifiConnected() ? "연결됨" : "끊김", WiFi.SSID().c_str());
    if (wifiConnected()) {
      Serial.printf("  주소      : %s / 게이트웨이 %s\n",
                    WiFi.localIP().toString().c_str(), WiFi.gatewayIP().toString().c_str());
      Serial.printf("  신호      : %d dBm\n", (int)WiFi.RSSI());
    }
    time_t now = time(nullptr);
    if (now > TIME_OK_EPOCH) {
      struct tm utc; gmtime_r(&now, &utc);
      char stamp[32]; strftime(stamp, sizeof(stamp), "%Y-%m-%d %H:%M:%S", &utc);
      Serial.printf("  시계      : %s UTC (동기화됨)\n", stamp);
    } else {
      Serial.println("  시계      : 아직 동기화 안 됨 — 그때까지 MQTT 접속을 미룹니다");
    }
    Serial.printf("  브로커    : %s:%d (%s)\n", MQTT_HOST, MQTT_PORT, mqtt.connected() ? "연결됨" : "끊김");
  }
  Serial.printf("  클라이언트: %s\n", TOPIC_ROOT);
  Serial.printf("  세션      : %s | 마지막 순번 %lu\n", sessionId, (unsigned long)lastSeq);
  Serial.printf("  발행 누적 : %lu\n", (unsigned long)published);
  Serial.printf("  힙 여유   : %u B\n", (unsigned)ESP.getFreeHeap());
  Serial.println("───────────────────────────────────────────────");
}

}  // namespace comm
