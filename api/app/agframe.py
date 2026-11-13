# -*- coding: utf-8 -*-
"""
아라온실 VALUE v1 프레임 부호화·복호화.

젯슨 중계기(config/bridge/bridge.py)와 시험 발행기(fake_device.py)에 검증돼 돌고 있는
로직을 그대로 옮긴 것이다. 표준 라이브러리만 쓴다.

    FF A3 01 01 | options_length(4, big-endian) | options | data_length(4) | JSON

API가 이 코드를 갖는 이유: 중계기는 숫자 필드만 평문 토픽으로 넘기므로, 펌웨어의
session 문자열과 응답의 result 문자열은 원본 프레임에서만 얻을 수 있다. 제어 명령도
펌웨어가 이 프레임만 받는다.
"""
import json
import struct

MAGIC = b"\xff\xa3\x01\x01"
MAX_OPTIONS = 1023
MAX_JSON = 1536


class FrameError(ValueError):
    pass


def encode(obj, options="data-type=application/json"):
    """파이썬 객체를 프레임 bytes 로. 펌웨어와 같은 규격."""
    body = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    opt = options.encode("ascii")
    if len(opt) > MAX_OPTIONS:
        raise FrameError("options too long")
    if len(body) > MAX_JSON:
        raise FrameError("json too long")
    out = bytearray(MAGIC)
    out += struct.pack(">I", len(opt))
    out += opt
    out += struct.pack(">I", len(body))
    out += body
    return bytes(out)


def decode(payload):
    """프레임 bytes 를 (JSON 객체, options 문자열)로. 실패하면 FrameError."""
    if len(payload) < 12:
        raise FrameError("too short")
    if payload[:4] != MAGIC:
        raise FrameError("bad magic")
    opt_len = struct.unpack(">I", payload[4:8])[0]
    if opt_len > MAX_OPTIONS or 12 + opt_len > len(payload):
        raise FrameError("bad options length")
    options = payload[8:8 + opt_len].decode("ascii", "replace")
    data_len = struct.unpack(">I", payload[8 + opt_len:12 + opt_len])[0]
    start = 12 + opt_len
    body = payload[start:start + data_len]
    if data_len > MAX_JSON or len(body) != data_len:
        raise FrameError("bad data length")
    body = body.rstrip(b"\x00")   # Message::setValue 는 꼬리 NUL 을 붙인다
    try:
        obj = json.loads(body.decode("utf-8"))
    except Exception as e:
        raise FrameError("json: %s" % e)
    if not isinstance(obj, dict):
        raise FrameError("not an object")
    return obj, options
