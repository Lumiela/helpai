/*
 * TeeSerial — USB 시리얼(Serial0)과 무선 콘솔(TCP 소켓)에 같은 출력을 내보내는 스트림 (2026-09-30)
 * ------------------------------------------------------------------
 * 모든 모듈이 `Serial.printf(...)` 로 찍는 로그를 무선 콘솔에서도 보려면 출력을 한 곳에서 갈라야 합니다.
 * config.h 끝에서 `Serial` 을 이 객체(TeeOut)로 바꿔 두므로 모듈 코드는 한 줄도 안 바꿉니다.
 * (코어 3.x 는 원래 `#define Serial Serial0` 이라, 실제 UART 는 Serial0 으로 직접 씁니다.)
 *
 *   출력: Serial0 에 항상 + 콘솔 소켓이 붙어 있으면 그쪽에도 (★ 비차단: 보낼 공간이 없으면 그 조각은 버림)
 *   입력: USB 시리얼(Serial0)만. 콘솔 입력은 .ino 의 consoleTick() 이 따로 읽어 handleCommand 로 넘깁니다.
 */

#ifndef TEE_SERIAL_H
#define TEE_SERIAL_H

#include <Arduino.h>

class TeeSerial : public Stream {
public:
  void begin(unsigned long baud);

  // 입력은 USB 시리얼만
  int  available() override;
  int  read() override;
  int  peek() override;
  void flush() override;

  // 출력은 USB + 콘솔 소켓
  size_t write(uint8_t c) override;
  size_t write(const uint8_t* buf, size_t n) override;

  // 콘솔 소켓 연결/해제. -1 = 없음. 소켓은 .ino 가 소유하고 여기서는 fd 로 보내기만 합니다.
  void attach(int fd);
  int  attached() const { return sock; }

  // 소켓에 비차단으로 보냅니다(프롬프트 등). 공간이 없으면 버립니다.
  static void rawSend(int fd, const char* s, size_t n);

private:
  int sock = -1;
};

extern TeeSerial TeeOut;

#endif  // TEE_SERIAL_H
