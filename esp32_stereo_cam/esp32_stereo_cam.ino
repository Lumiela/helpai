// ESP32-CAM 스테레오 비전용 펌웨어 (AI-Thinker ESP32-CAM)
// 두 보드에 같은 코드를 올리되, CAM_SIDE 값만 바꿔서 업로드하세요.
//   왼쪽 카메라:  #define CAM_SIDE 0  -> 192.168.0.101
//   오른쪽 카메라: #define CAM_SIDE 1  -> 192.168.0.102
// 엔드포인트: http://<IP>/capture  -> JPEG 한 장 반환

#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>

#define CAM_SIDE 0   // 0 = 왼쪽, 1 = 오른쪽

const char* ssid = "YOUR_SSID";
const char* pass = "YOUR_PASS";

// 공유기 대역에 맞게 수정 (예: 192.168.1.x 라면 아래 세 줄 모두 변경)
IPAddress localIP(192, 168, 0, CAM_SIDE == 0 ? 101 : 102);
IPAddress gateway(192, 168, 0, 1);
IPAddress subnet(255, 255, 255, 0);

// 노출/게인 고정값 - 좌우 밝기를 같게 맞추기 위함 (두 보드 동일하게 설정)
// 화면이 너무 어두우면 AEC_VALUE를 올리세요 (0~1200)
#define AEC_VALUE 300
#define AGC_GAIN  2

// AI-Thinker ESP32-CAM 핀맵
#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22

WebServer server(80);

void handleCapture() {
  // 버퍼에 남아 있던 이전 프레임은 버리고 새로 촬영
  camera_fb_t* fb = esp_camera_fb_get();
  if (fb) esp_camera_fb_return(fb);
  fb = esp_camera_fb_get();
  if (!fb) {
    server.send(500, "text/plain", "capture failed");
    return;
  }
  server.setContentLength(fb->len);
  server.send(200, "image/jpeg", "");
  server.sendContent((const char*)fb->buf, fb->len);
  esp_camera_fb_return(fb);
}

void setup() {
  Serial.begin(115200);

  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM;
  c.pin_d1 = Y3_GPIO_NUM;
  c.pin_d2 = Y4_GPIO_NUM;
  c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM;
  c.pin_d5 = Y7_GPIO_NUM;
  c.pin_d6 = Y8_GPIO_NUM;
  c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM;
  c.pin_pclk = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM;
  c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM;
  c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM;
  c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAMESIZE_VGA;  // 640x480 (캘리브레이션과 측정 시 동일해야 함)
  c.jpeg_quality = 10;
  c.fb_count = 1;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;

  if (esp_camera_init(&c) != ESP_OK) {
    Serial.println("Camera init failed");
    return;
  }

  sensor_t* s = esp_camera_sensor_get();
  s->set_exposure_ctrl(s, 0);
  s->set_aec_value(s, AEC_VALUE);
  s->set_gain_ctrl(s, 0);
  s->set_agc_gain(s, AGC_GAIN);
  s->set_whitebal(s, 0);

  WiFi.config(localIP, gateway, subnet);
  WiFi.begin(ssid, pass);
  WiFi.setSleep(false);  // 응답 지연 줄이기
  Serial.print("WiFi connecting");
  while (WiFi.status() != WL_CONNECTED) {
    delay(300);
    Serial.print(".");
  }
  Serial.println();
  Serial.print(CAM_SIDE == 0 ? "LEFT" : "RIGHT");
  Serial.print(" camera ready: http://");
  Serial.print(WiFi.localIP());
  Serial.println("/capture");

  server.on("/capture", handleCapture);
  server.begin();
}

void loop() {
  server.handleClient();
}
