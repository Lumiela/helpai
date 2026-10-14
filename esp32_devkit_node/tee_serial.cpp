// ★ 이 파일은 config.h 를 포함하지 않습니다(그 안의 `#define Serial TeeOut` 을 피하기 위해). 실제 UART 는 Serial0.
#include "tee_serial.h"

#include <HardwareSerial.h>
#include <lwip/sockets.h>
#include <errno.h>

TeeSerial TeeOut;

void TeeSerial::begin(unsigned long baud) { Serial0.begin(baud); }

int  TeeSerial::available() { return Serial0.available(); }
int  TeeSerial::read()      { return Serial0.read(); }
int  TeeSerial::peek()      { return Serial0.peek(); }
void TeeSerial::flush()     { Serial0.flush(); }

size_t TeeSerial::write(uint8_t c) { return write(&c, 1); }

size_t TeeSerial::write(const uint8_t* buf, size_t n) {
  const size_t w = Serial0.write(buf, n);
  if (sock >= 0) {
    // MSG_DONTWAIT: 상대가 안 읽어 송신 버퍼가 차면 즉시 -1(EAGAIN). 절대 기다리지 않는다.
    const int r = ::send(sock, buf, n, MSG_DONTWAIT);
    if (r < 0 && errno != EAGAIN && errno != EWOULDBLOCK) sock = -1;   // 끊긴 소켓. consoleTick 이 정리
  }
  return w;
}

void TeeSerial::attach(int fd) { sock = fd; }

void TeeSerial::rawSend(int fd, const char* s, size_t n) {
  if (fd < 0) return;
  ::send(fd, s, n, MSG_DONTWAIT);
}
