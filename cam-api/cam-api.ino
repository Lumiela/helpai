/*
 * SmartCube 카메라 보드 (ESP32-CAM) — 젯슨 api 0.6.0 연동
 *   명령   MQTT 8443(TLS)  device/helpai.cube/<SN>/notify/config   {snap} · {stream}
 *   업로드 HTTPS 443        /api/dev/cameras/hello · /api/dev/snapshots · /api/dev/frames  (Basic 인증)
 *   순서   Wi-Fi → NTP → hello → MQTT → status
 * 사진은 PSRAM 프레임 버퍼에만 있고 전송이 끝나면 바로 반환된다(저장 없음).
 */
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoOTA.h>               // 2026-10-02 추가: 같은 Wi-Fi 에서 IDE 네트워크 포트 업로드 (코어 내장)
#include "esp_camera.h"
#include "sensor.h"
#include "config.h"
#include "ESP32_OV5640_AF.h"           // 2026-10-14 자동 초점 (ESP32_OV5640_AF 0.1.1, 이 폴더에 복사)
#include "secrets.h"
#include "certs.h"
#include "comm.h"

// ===== AI Thinker ESP32-CAM 핀 =====
#define PWDN_GPIO_NUM  32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM   0
#define SIOD_GPIO_NUM  26
#define SIOC_GPIO_NUM  27
#define Y9_GPIO_NUM    35
#define Y8_GPIO_NUM    34
#define Y7_GPIO_NUM    39
#define Y6_GPIO_NUM    36
#define Y5_GPIO_NUM    21
#define Y4_GPIO_NUM    19
#define Y3_GPIO_NUM    18
#define Y2_GPIO_NUM     5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM  23
#define PCLK_GPIO_NUM  22
#define FLASH_GPIO      4
#define FLASH_LEDC_CH   7                  // 카메라 XCLK(LEDC 채널 0)와 겹치지 않게

// ---------- 상태 ----------
static camera_config_t camCfg;             // 켤 때마다 이 설정(해상도만 바꿔)으로 카메라를 켠다
static framesize_t snapSize = SNAP_FRAME_SIZE;
static const char *sensorName = "unknown";
static bool camOn = false;
static framesize_t camOnSize;
static uint32_t camOnXclk = 0;            // 켜져 있을 때의 XCLK(Hz)

static bool netOk = false;
static bool helloDone = false;
static uint32_t lastHelloAttempt = 0;
static uint32_t mqttDownSince = 0;

// 스냅샷 요청 (한 건만 대기)
static bool snapPending = false;
static char snapTrigger[16] = "";
static char snapReq[24] = "";
static uint32_t snapRequestedAt = 0;
static uint32_t snapNextAt = 0;
static uint8_t snapTries = 0;

// 스트리밍
static bool streaming = false;
static uint32_t streamStartedAt = 0;
static uint32_t streamMaxMs = 0;
static uint32_t lastFrameAt = 0;
static uint8_t streamFails = 0;
static uint32_t streamFrames = 0;
static uint32_t streamLogAt = 0;
static WiFiClientSecure frameClient;       // 프레임은 연결을 재사용해 TLS 악수를 줄인다
static HTTPClient frameHttp;

// LAN 원격 업로드 (2026-10-02, 큐브 제어기와 같은 방식)
static bool otaStarted = false;            // ArduinoOTA.begin() 을 불렀는지 (Wi-Fi 가 붙은 뒤 한 번)
static bool otaActive = false;             // 전송 진행 중
static char otaState[24] = "off";          // status 의 ota: off / ready / uploading / error:<n>
static uint32_t otaLastTickMs = 0;

// ---------- 플래시 ----------
static void setFlash(uint8_t duty) {
#if FLASH_ENABLE
  ledcWrite(FLASH_GPIO, duty);
#endif
}

// ---------- 자동 초점 (2026-10-14, cube-cam-0.7.1) ----------
// 켤 때마다 afLoad() 로 초점 펌웨어를 내려받고, 스냅샷은 afFocusOnce()(단발, 완료 대기), 스트리밍은 afContinuous()(연속).
// 어느 단계가 실패해도 촬영·스트리밍은 그대로 진행한다. 결과는 status 의 af 로 보고: off(끔/OV5640 아님) · ready(펌웨어 올라감)
// · ok(초점 완료 또는 연속 모드 켜짐) · fail(펌웨어 실패 또는 초점 시간 초과)
#if ENABLE_AUTOFOCUS
static OV5640 af;
#endif
static bool afLoaded = false;              // 지금 켜져 있는 카메라에 초점 펌웨어가 올라갔는지
static char afState[8] = "off";

static void afLoad(sensor_t *s) {
  afLoaded = false;
#if ENABLE_AUTOFOCUS
  if (!s || !af.start(s)) { strlcpy(afState, "off", sizeof(afState)); return; }
  const uint32_t t0 = millis();
  const uint8_t rc = af.focusInit();       // 펌웨어 4,332B 쓰기 → 보조 제어기 대기 상태(0x70) 확인
  if (rc == 0) {
    afLoaded = true;
    strlcpy(afState, "ready", sizeof(afState));
    Serial.printf("[af] 펌웨어 올림 %lums\n", (unsigned long)(millis() - t0));
  } else {
    strlcpy(afState, "fail", sizeof(afState));
    Serial.printf("[af] 펌웨어 실패(%u) → 초점 없이 진행\n", (unsigned)rc);
  }
#endif
}

// 단발 초점: 명령 0x03 → 접수 확인 → 완료(0x10) 대기. 반환값 = 초점 완료 여부
static bool afFocusOnce() {
#if ENABLE_AUTOFOCUS
  if (!afLoaded) return false;
  sensor_t *s = esp_camera_sensor_get();
  if (!s) return false;
  const uint32_t t0 = millis();
  s->set_reg(s, OV5640_CMD_ACK, 0xff, 0x01);
  s->set_reg(s, OV5640_CMD_MAIN, 0xff, AF_TRIG_SINGLE_AUTO_FOCUS);
  while (s->get_reg(s, OV5640_CMD_ACK, 0xff) != 0) {   // 명령 접수 대기
    if (millis() - t0 > AF_FOCUS_WAIT_MS) { strlcpy(afState, "fail", sizeof(afState)); Serial.println("[af] 명령 접수 안 됨 → 그대로 촬영"); return false; }
    delay(5);
  }
  for (;;) {
    const int st = s->get_reg(s, OV5640_CMD_FW_STATUS, 0xff);
    if (st == FW_STATUS_S_FOCUSED) {
      strlcpy(afState, "ok", sizeof(afState));
      Serial.printf("[af] 초점 완료 %lums\n", (unsigned long)(millis() - t0));
      return true;
    }
    if (millis() - t0 > AF_FOCUS_WAIT_MS) {
      strlcpy(afState, "fail", sizeof(afState));
      Serial.printf("[af] 초점 시간 초과(상태 0x%02x) → 그대로 촬영\n", st & 0xff);
      return false;
    }
    delay(10);
  }
#else
  return false;
#endif
}

// 연속 초점(스트리밍): 켜 두면 센서가 스스로 계속 맞춘다
static void afContinuous() {
#if ENABLE_AUTOFOCUS
  if (!afLoaded) return;
  const uint8_t rc = af.autoFocusMode();
  strlcpy(afState, rc == 0 ? "ok" : "fail", sizeof(afState));
  Serial.printf("[af] 연속 초점 %s\n", rc == 0 ? "켬" : "실패");
#endif
}

// ---------- 카메라 ----------
// 발열을 줄이기 위해 평소에는 카메라를 꺼 두고, 찍을 때(스트리밍 중에는 계속) 켠다
static void cameraOff() {
  if (camOn) esp_camera_deinit();          // XCLK·DMA 정지, 프레임 버퍼 해제
  camOn = false;
  pinMode(PWDN_GPIO_NUM, OUTPUT);
  digitalWrite(PWDN_GPIO_NUM, HIGH);       // 센서 절전 모드
}

static bool cameraOn(framesize_t size, int quality, uint32_t xclkHz) {
  if (camOn && camOnSize == size && camOnXclk == xclkHz) return true;
  cameraOff();
  // 2592×1944 (2026-10-07, cube-cam-0.6-5mp): esp_camera_init 이 OV5640 을 2560×1920 으로 제한하므로
  // 2560×1920 으로 켠 뒤 센서에 2592×1944 를 다시 지정한다. 사진 버퍼(기본 가로×세로÷5)도 2592×1944 기준으로 잡는다
  const bool fiveMp = (size == FRAMESIZE_5MP);
  camCfg.frame_size = fiveMp ? FRAMESIZE_QSXGA : size;
  camCfg.jpeg_buffer_size = fiveMp ? (size_t)resolution[FRAMESIZE_5MP].width * resolution[FRAMESIZE_5MP].height / 5 : 0;
  camCfg.jpeg_quality = quality;
  camCfg.xclk_freq_hz = xclkHz;            // 스냅샷 20MHz, 스트리밍은 STREAM_XCLK_HZ (발열 대책)
  esp_err_t err = esp_camera_init(&camCfg);
  if (err != ESP_OK) {
    Serial.printf("[cam] init failed: 0x%x\n", err);
    cameraOff();
    return false;
  }
  camOn = true;
  camOnSize = size;
  camOnXclk = xclkHz;
  sensor_t *s = esp_camera_sensor_get();
  if (fiveMp && (!s || s->set_framesize(s, FRAMESIZE_5MP) != 0)) {
    Serial.println("[cam] 2592x1944 지정 실패");
    cameraOff();
    return false;
  }
  if (s && s->id.PID == OV5640_PID) s->set_vflip(s, OV5640_VFLIP);
  afLoad(s);                               // 켤 때마다 초점 펌웨어 다시 내려받기 (ENABLE_AUTOFOCUS)
  for (int i = 0; i < WARMUP_FRAMES; i++) {
    camera_fb_t *f = esp_camera_fb_get();
    if (f) esp_camera_fb_return(f);
  }
  if (size == STREAM_FRAME_SIZE) afContinuous();   // 스트리밍용으로 켠 것이면 연속 초점
  return true;
}

// 설정을 채우고 한 번 켜서 센서 종류를 읽은 뒤 끈다
static bool initCamera() {
  if (!psramFound()) {
    Serial.println("PSRAM 없음 — 보드 설정에서 PSRAM Enabled 로 굽습니다");
    return false;
  }
  camera_config_t &c = camCfg;
  c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM;  c.pin_d1 = Y3_GPIO_NUM;
  c.pin_d2 = Y4_GPIO_NUM;  c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM;  c.pin_d5 = Y7_GPIO_NUM;
  c.pin_d6 = Y8_GPIO_NUM;  c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM;
  c.pin_pclk = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM;
  c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM;
  c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM;
  c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = SNAP_XCLK_HZ;           // 켤 때마다 cameraOn() 이 용도별 값으로 덮어쓴다
  c.pixel_format = PIXFORMAT_JPEG;
  c.fb_count = 2;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;

  if (!cameraOn(FRAMESIZE_UXGA, SNAP_JPEG_QUALITY, SNAP_XCLK_HZ)) return false;
  sensor_t *s = esp_camera_sensor_get();
  switch (s->id.PID) {
    case OV5640_PID: sensorName = "OV5640"; break;   // 2026-10-07 센서 변경
    case OV3660_PID: sensorName = "OV3660"; break;
    case OV2640_PID: sensorName = "OV2640"; break;
    default:         sensorName = "unknown"; break;
  }
  Serial.printf("[cam] sensor %s (PID 0x%x) | snapshot %ux%u\n", sensorName, s->id.PID,
                resolution[snapSize].width, resolution[snapSize].height);
  cameraOff();
  return true;
}

static camera_fb_t *grabSnapshot() {
  if (!cameraOn(snapSize, SNAP_JPEG_QUALITY, SNAP_XCLK_HZ)) return NULL;
#if FLASH_ENABLE
  setFlash(FLASH_DUTY);
  delay(FLASH_WARMUP_MS);
  for (int i = 0; i < 2; i++) {
    camera_fb_t *f = esp_camera_fb_get();
    if (f) esp_camera_fb_return(f);
  }
#endif
  if (afFocusOnce()) {                     // 플래시 켜진 밝기에서 초점을 맞추고, 렌즈가 움직인 뒤 한 장 버린다
    camera_fb_t *f = esp_camera_fb_get();
    if (f) esp_camera_fb_return(f);
  }
  camera_fb_t *fb = esp_camera_fb_get();
  setFlash(0);
  return fb;
}

// ---------- HTTPS ----------
static String apiUrl(const char *path) {
  return String("https://") + MQTT_HOST + path;
}

// 반환: HTTP 코드 (음수 = 접속 실패)
static int httpsPost(HTTPClient &http, WiFiClientSecure &client, const String &url,
                     const char *type, const uint8_t *buf, size_t len, String *resp) {
  if (!http.begin(client, url)) return -1;
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setAuthorization(MQTT_USER, MQTT_PASS);
  http.addHeader("Content-Type", type);
  int code = http.POST((uint8_t *)buf, len);
  if (resp && code > 0) *resp = http.getString();
  http.end();
  return code;
}

static bool jsonTrue(const String &json, const char *key) {
  String k = String("\"") + key + "\"";
  int p = json.indexOf(k);
  if (p < 0) return false;
  p += k.length();
  while (p < (int)json.length() && (json[p] == ' ' || json[p] == ':')) p++;
  return json.startsWith("true", p);
}

// 평문 1단계 JSON 에서 문자열 값 하나 (comm::jsonInt 는 정수만 꺼낸다)
static bool jsonStr(const char *json, uint16_t len, const char *key, char *out, size_t cap) {
  char needle[24];
  snprintf(needle, sizeof(needle), "\"%s\"", key);
  size_t n = strlen(needle);
  for (uint16_t i = 0; i + n < len; i++) {
    if (memcmp(json + i, needle, n) != 0) continue;
    uint16_t p = i + n;
    while (p < len && (json[p] == ' ' || json[p] == ':')) p++;
    if (p >= len || json[p] != '"') return false;
    p++;
    size_t o = 0;
    while (p < len && json[p] != '"' && o + 1 < cap) {
      if (isalnum((unsigned char)json[p])) out[o++] = json[p];   // URL 에 그대로 붙이므로 영숫자만
      p++;
    }
    out[o] = '\0';
    return o > 0;
  }
  return false;
}

// ---------- 칩 온도 ----------
// ESP32 내부 온도(°C). 보정 없는 ROM 센서라 절대값보다 "평소보다 얼마나 올랐나" 를 보는 용도.
// 센서가 없는 개체는 53.3 고정(원시값 128). 비정상값은 n/a.
static float chipTemperatureC() {
  const float t = temperatureRead();
  if (isnan(t) || t < -40.0f || t > 150.0f) return NAN;
  return t;
}

static const char *chipTempStr() {
  static char buf[12];
  const float t = chipTemperatureC();
  if (isnan(t)) return "n/a";
  snprintf(buf, sizeof(buf), "%.1f°C", t);
  return buf;
}

// ---------- status ----------
static uint32_t lastStatusAt = 0;

// 접속 직후·스트리밍 시작/정지·그룹 요청 때, 그리고 STATUS_INTERVAL_MS 마다 보낸다.
// 장치 연결 페이지가 ip·mac·rssi_dbm·chip_temp 를 그대로 읽는다(큐브 제어기와 같은 필드 이름)
static void publishStatus() {
  lastStatusAt = millis();
  char temp[12];
  const float t = chipTemperatureC();
  if (isnan(t)) strlcpy(temp, "null", sizeof(temp));
  else snprintf(temp, sizeof(temp), "%.1f", t);
  comm::publishf("/status",
                 "{\"sn\":\"%s\",\"fw\":\"%s\",\"model\":\"%s\",\"sensor\":\"%s\",\"stream\":%d,\"heap\":%u,"
                 "\"ip\":\"%s\",\"mac\":\"%s\",\"rssi_dbm\":%d,\"chip_temp\":%s,\"ota\":\"%s\",\"af\":\"%s\"}",
                 DEVICE_SN, FW_VERSION, DEVICE_MODEL, sensorName, streaming ? 1 : 0,
                 (unsigned)ESP.getFreeHeap(),
                 WiFi.localIP().toString().c_str(), WiFi.macAddress().c_str(), (int)WiFi.RSSI(), temp, otaState, afState);
}

// ---------- hello ----------
static void startStream(int maxS);

static bool sendHello() {
  char body[192];
  snprintf(body, sizeof(body), "{\"sn\":\"%s\",\"model\":\"%s\",\"sensor\":\"%s\",\"fw\":\"%s\"}",
           DEVICE_SN, DEVICE_MODEL, sensorName, FW_VERSION);
  WiFiClientSecure client;
  client.setCACert(MQTT_CA_PEM);
  HTTPClient http;
  String resp;
  int code = httpsPost(http, client, apiUrl("/api/dev/cameras/hello"), "application/json",
                       (const uint8_t *)body, strlen(body), &resp);
  Serial.printf("[hello] HTTP %d %s\n", code, resp.c_str());
  if (code == 401) Serial.println("  → secrets.h 의 MQTT_USER / MQTT_PASS 확인");
  if (code == 404) Serial.println("  → DEVICE_SN 의 큐브가 이 계정 소유가 아님");
  if (code == 400) Serial.println("  → DEVICE_SN 형식 오류 (CUBE001-C1 모양)");
  if (code != 200) return false;

  if (jsonTrue(resp, "stream_on")) {
    int maxS = STREAM_DEFAULT_MAX_S;
    comm::jsonInt(resp.c_str(), resp.length(), "stream_max_s", &maxS);
    startStream(maxS);
  }
  return true;
}

// ---------- 스냅샷 ----------
static void queueSnap(const char *trigger, const char *req) {
  if (snapPending && snapReq[0] && !req[0]) {
    Serial.println("[snap] 즉시 촬영 대기 중 — 새 요청은 합침");
    return;
  }
  strlcpy(snapTrigger, trigger, sizeof(snapTrigger));
  strlcpy(snapReq, req, sizeof(snapReq));
  snapPending = true;
  snapRequestedAt = millis();
  snapNextAt = millis();
  snapTries = 0;
}

static void doSnapshot() {
  // 즉시 촬영은 SNAP_REQ_WAIT_MS 가 지나면 서버가 실패로 답하므로, 그 뒤에는 device 로 저장만 한다
  if (snapReq[0] && millis() - snapRequestedAt > SNAP_REQ_WAIT_MS) {
    Serial.printf("[snap] %lu초 경과 → trigger=device 로 전환\n", (unsigned long)(SNAP_REQ_WAIT_MS / 1000));
    strlcpy(snapTrigger, "device", sizeof(snapTrigger));
    snapReq[0] = '\0';
  }
  if (streaming) frameClient.stop();       // TLS 연결 3개가 동시에 열리지 않게

  uint32_t t0 = millis();
  camera_fb_t *fb = grabSnapshot();
  uint32_t t1 = millis();
  if (comm::connected()) publishStatus();  // 이 사진의 초점 결과(af)가 사이드카 json 에 남도록 업로드 전에 상태를 보낸다
  int code = -1;
  if (fb) {
    char path[128];
    if (snapReq[0])
      snprintf(path, sizeof(path), "/api/dev/snapshots?cam=%s&trigger=%s&req=%s", DEVICE_SN, snapTrigger, snapReq);
    else
      snprintf(path, sizeof(path), "/api/dev/snapshots?cam=%s&trigger=%s", DEVICE_SN, snapTrigger);
    WiFiClientSecure client;
    client.setCACert(MQTT_CA_PEM);
    HTTPClient http;
    String resp;
    code = httpsPost(http, client, apiUrl(path), "image/jpeg", fb->buf, fb->len, &resp);
    uint32_t t2 = millis();
    Serial.printf("[snap] %s %u bytes | capture %lums | upload %lums | RSSI %d | heap %u | HTTP %d\n",
                  snapTrigger, fb->len, t1 - t0, t2 - t1, WiFi.RSSI(), (unsigned)ESP.getFreeHeap(), code);
    if (code != 200) Serial.printf("  %s\n", resp.c_str());
    esp_camera_fb_return(fb);
  } else {
    Serial.println("[snap] capture failed");
  }
  if (!streaming) cameraOff();             // 스트리밍 중이면 다음 프레임에서 HD 로 다시 켜진다

  if (code == 200) { snapPending = false; return; }
  if (code == 400 || code == 401 || code == 404 || code == 413) {   // 다시 보내도 같은 결과
    snapPending = false;
    return;
  }
  if (++snapTries >= SNAP_RETRY_MAX) {
    Serial.println("[snap] 재시도 횟수 초과 — 포기");
    snapPending = false;
    return;
  }
  snapNextAt = millis() + SNAP_RETRY_MS;
}

// ---------- 스트리밍 ----------
static void startStream(int maxS) {
  if (maxS <= 0) maxS = STREAM_DEFAULT_MAX_S;
  streamMaxMs = (uint32_t)maxS * 1000UL;
  streamStartedAt = millis();
  streamLogAt = millis();
  streamFails = 0;
  streamFrames = 0;
  if (!streaming) {
    streaming = true;
#if !STREAM_KEEP_WIFI_SLEEP
    WiFi.setSleep(false);                  // 처리량 확보. 멈추면 다시 절전
#endif
    Serial.printf("[stream] start (max %ds) | chip %s\n", maxS, chipTempStr());
    publishStatus();
  }
}

static void stopStream(const char *reason) {
  if (!streaming) return;
  streaming = false;
  frameHttp.end();
  frameClient.stop();
  cameraOff();
  WiFi.setSleep(true);
  Serial.printf("[stream] stop (%s) | chip %s\n", reason, chipTempStr());
  publishStatus();
}

static void streamStep() {
  if (millis() - streamStartedAt >= streamMaxMs) { stopStream("max_s"); return; }
  if (millis() - lastFrameAt < STREAM_FRAME_INTERVAL_MS) return;
  lastFrameAt = millis();

  int code = -1;
  String resp;
  if (cameraOn(STREAM_FRAME_SIZE, STREAM_JPEG_QUALITY, STREAM_XCLK_HZ)) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (fb) {
      static const String url = apiUrl("/api/dev/frames?cam=" DEVICE_SN);
      code = httpsPost(frameHttp, frameClient, url, "image/jpeg", fb->buf, fb->len, &resp);
      esp_camera_fb_return(fb);
    }
  }

  if (code == 200) {
    streamFails = 0;
    streamFrames++;
    if (jsonTrue(resp, "stop")) { stopStream("server stop:true"); return; }
  } else if (++streamFails >= STREAM_FAIL_MAX) {
    Serial.printf("[stream] 마지막 HTTP %d %s\n", code, resp.c_str());
    stopStream("upload failures");
    return;
  }

  if (millis() - streamLogAt >= 5000) {
    Serial.printf("[stream] %.1f fps | RSSI %d | heap %u | chip %s\n",
                  streamFrames * 1000.0f / (millis() - streamLogAt), WiFi.RSSI(), (unsigned)ESP.getFreeHeap(),
                  chipTempStr());
    streamFrames = 0;
    streamLogAt = millis();
  }
}

// ---------- MQTT 콜백 ----------
static const char *onControl(const comm::ControlCmd &) {
  return "denied";                         // 카메라에는 제어 명령이 없다
}

static void onConfig(const char *json, uint16_t len) {
  Serial.printf("[RX] config %.*s\n", len, json);
  if (otaActive) { Serial.println("[RX] 원격 업로드 중 → 무시"); return; }
  int v;
  if (comm::jsonInt(json, len, "snap", &v) && v == 1) {
    char req[24] = "";
    char trigger[16] = "";
    jsonStr(json, len, "req", req, sizeof(req));
    if (!jsonStr(json, len, "trigger", trigger, sizeof(trigger)))
      strlcpy(trigger, req[0] ? "request" : "periodic", sizeof(trigger));
    queueSnap(trigger, req);               // 촬영·업로드는 loop 에서 (MQTT 수신 중에 오래 막지 않게)
  }
  if (comm::jsonInt(json, len, "stream", &v)) {
    if (v == 1) {
      int maxS = STREAM_DEFAULT_MAX_S;
      comm::jsonInt(json, len, "max_s", &maxS);
      startStream(maxS);
    } else {
      stopStream("server stream:0");
    }
  }
}

// ---------- 시리얼 (시험용) ----------
static void handleSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'i') { comm::printNetInfo(); Serial.printf("  원격 업로드: %s | 자동 초점: %s\n", otaState, afState); }
    else if (c == 's') queueSnap("device", "");
    else if (c == 'r') comm::reconnect();
  }
}

// ---------- LAN 원격 업로드 (2026-10-02, 사용자 지시: 큐브 제어기와 같은 방식·같은 비밀번호) ----------
// 같은 Wi-Fi 의 아두이노 IDE 가 네트워크 포트("cube-<SN> at <IP>")로 새 판을 올린다. 코어 내장 ArduinoOTA.
// Wi-Fi 가 붙은 뒤 한 번 begin(), 루프마다 handle(). 전송은 handle() 안에서 끝까지 진행되므로 그동안 루프는 멈추고,
// MQTT keepalive 만 진행 콜백에서 1초마다 대신 돌린다. 전송이 시작되면 스트리밍·카메라를 끄고 서버 config 는 무시한다.
static void otaSetup() {
#if ENABLE_LAN_OTA
  if (strstr(OTA_PASSWORD, "여기에") != nullptr || OTA_PASSWORD[0] == '\0') {
    Serial.println("[OTA] secrets.h 의 OTA_PASSWORD 가 비어 있어 LAN 업로드를 켜지 않습니다");
    return;
  }
  static char host[40];
  snprintf(host, sizeof(host), "%s%s", OTA_HOSTNAME_PREFIX, DEVICE_SN);
  ArduinoOTA.setHostname(host);
  ArduinoOTA.setPort(OTA_PORT);
  ArduinoOTA.setPassword(OTA_PASSWORD);
  ArduinoOTA.setRebootOnSuccess(true);

  ArduinoOTA.onStart([]() {
    otaActive = true;
    strlcpy(otaState, "uploading", sizeof(otaState));
    if (streaming) stopStream("ota");      // 프레임 업로드 연결·카메라 정지
    else cameraOff();
    WiFi.setSleep(false);                  // 전송 속도 확보. 실패하면 다시 절전
    Serial.printf("\n[OTA] 업로드 시작 (%s). 스트리밍·카메라 정지, 서버 config 무시\n",
                  ArduinoOTA.getCommand() == U_FLASH ? "펌웨어" : "파일시스템");
    publishStatus();
    otaLastTickMs = millis();
  });
  ArduinoOTA.onProgress([](unsigned int done, unsigned int total) {
    static uint8_t lastPct = 255;
    const uint8_t pct = total ? (uint8_t)(done * 100UL / total) : 0;
    if (pct != lastPct && pct % 10 == 0) { Serial.printf("[OTA] %u%% (%u/%u B)\n", pct, done, total); lastPct = pct; }
    // 전송 중에도 MQTT 접속을 잃지 않도록 keepalive·수신을 1초마다 돌린다
    if (millis() - otaLastTickMs >= 1000) { otaLastTickMs = millis(); comm::tick(); }
  });
  ArduinoOTA.onEnd([]() {
    Serial.println("[OTA] 전송 완료 — 검증 뒤 재부팅합니다");
  });
  ArduinoOTA.onError([](ota_error_t e) {
    otaActive = false;
    snprintf(otaState, sizeof(otaState), "error:%u", (unsigned)e);
    if (!streaming) WiFi.setSleep(true);
    const char *why = e == OTA_AUTH_ERROR ? "인증 실패(비밀번호)" : e == OTA_BEGIN_ERROR ? "시작 실패(파티션·공간)" :
                      e == OTA_CONNECT_ERROR ? "연결 실패" : e == OTA_RECEIVE_ERROR ? "수신 실패(Wi-Fi 끊김?)" :
                      e == OTA_END_ERROR ? "마무리 실패(이미지 검증)" : "알 수 없음";
    Serial.printf("[OTA] 실패: %s — 현재 판으로 계속 동작\n", why);
    publishStatus();
  });
  strlcpy(otaState, "ready", sizeof(otaState));
  Serial.printf("[OTA] LAN 업로드 준비: 호스트 %s, 포트 %u (Wi-Fi 접속 뒤 활성)\n", host, (unsigned)OTA_PORT);
#endif
}

static void otaTick() {
#if ENABLE_LAN_OTA
  if (strcmp(otaState, "off") == 0) return;
  if (!otaStarted) {
    if (!comm::wifiConnected()) return;
    ArduinoOTA.begin();                    // mDNS 등록 포함. IDE 포트 목록에 뜨기 시작
    otaStarted = true;
    Serial.printf("[OTA] 활성: %s.local / %s:%u\n", ArduinoOTA.getHostname().c_str(),
                  WiFi.localIP().toString().c_str(), (unsigned)OTA_PORT);
    return;
  }
  ArduinoOTA.handle();
#endif
}

void setup() {
  Serial.begin(115200);
  Serial.println();
  Serial.printf("SmartCube camera %s | SN %s\n", FW_VERSION, DEVICE_SN);

  if (!initCamera()) return;

#if FLASH_ENABLE
  ledcAttachChannel(FLASH_GPIO, 5000, 8, FLASH_LEDC_CH);
  ledcWrite(FLASH_GPIO, 0);
#endif

  if (strstr(MQTT_USER, "여기에") || strstr(MQTT_PASS, "여기에")) {
    Serial.println("★ secrets.h 의 MQTT_USER / MQTT_PASS 를 채우세요 (대시보드 → 장치 연결 → 브로커 계정). 네트워크를 시작하지 않습니다 ★");
    return;
  }

  frameClient.setCACert(MQTT_CA_PEM);
  frameHttp.setReuse(true);

  comm::Callbacks cbs = { onControl, onConfig, nullptr, publishStatus };
  netOk = comm::begin(cbs);
  WiFi.setSleep(true);                     // 모뎀 절전 (발열 감소). 스트리밍 중에만 끈다
#if WIFI_LOW_TX_POWER
  WiFi.setTxPower(WIFI_POWER_11dBm);
#endif
  otaSetup();                              // LAN 원격 업로드 (ENABLE_LAN_OTA, 2026-10-02)
  Serial.println("시리얼: i 네트워크 정보 | s 즉시 촬영 | r 재접속");
}

void loop() {
  if (!netOk) { delay(1000); return; }
  handleSerial();
  otaTick();                               // LAN 원격 업로드. hello·MQTT 보다 먼저: 서버 접속이 안 돼도 올릴 수 있게

  // 처음 보는 카메라는 hello 로 등록돼야 브로커 토픽 권한이 생기므로 MQTT 보다 먼저 보낸다
  if (!helloDone) {
    if (comm::wifiConnected() && comm::timeReady() && millis() - lastHelloAttempt >= NETWORK_RETRY_MS) {
      lastHelloAttempt = millis();
      helloDone = sendHello();
      mqttDownSince = millis();
    }
    delay(50);
    return;
  }

  comm::tick();

  // MQTT 가 계속 거절되면(권한 없음 등) hello 부터 다시
  if (comm::connected()) {
    mqttDownSince = millis();
  } else if (comm::wifiConnected() && millis() - mqttDownSince > MQTT_REHELLO_MS) {
    Serial.println("[MQTT] 오래 접속 안 됨 → hello 다시");
    helloDone = false;
    return;
  }

  // 상태(칩 온도 등)를 주기적으로 보낸다. 대기 중에도 화면 값과 온라인 표시가 갱신된다
  if (comm::connected() && millis() - lastStatusAt >= STATUS_INTERVAL_MS) publishStatus();

  if (snapPending && (int32_t)(millis() - snapNextAt) >= 0) doSnapshot();
  if (streaming) streamStep();
  else delay(20);
}
