# -*- coding: utf-8 -*-
"""
아라온실 VALUE v1 프레임 중계기.
ESP32가 device/<앱>/<시리얼>/{public,status,ack} 로 보내는 이진 프레임을 풀어
평문 JSON으로 smartcube/<시리얼>/ctrl/{data,status,ack} 에 다시 발행한다.
기존 수집기(telegraf)가 그 토픽을 이미 구독하므로 값이 그대로 DB에 쌓인다.

프레임 형식 (esp32-smartcube/include/AgFrame.h 와 동일):
  FF A3 01 01 | options_length(4, big-endian) | options | data_length(4) | JSON
"""
import json
import os
import struct
import sys
import time

import paho.mqtt.client as mqtt

HOST = os.environ.get("BROKER_HOST", "mosquitto")
PORT = int(os.environ.get("BROKER_PORT", "1883"))
USER = os.environ["MQTT_USER"]
PW = os.environ["MQTT_PASSWORD"]
APP = os.environ.get("AG_APP", "ag.smartcube.esp32")
OUT_ROOT = os.environ.get("OUT_ROOT", "smartcube")

MAGIC = b"\xff\xa3\x01\x01"
MAX_OPTIONS = 1023
MAX_JSON = 1536
# 장치 토픽 끝자락 → 수집기 토픽 끝자락
KIND_MAP = {"public": "data", "status": "status", "ack": "ack"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def decode_frame(payload):
    """프레임을 풀어 (JSON 객체, options 문자열)을 돌려준다. 실패하면 (None, 사유)."""
    if len(payload) < 12:
        return None, "too short"
    if payload[:4] != MAGIC:
        return None, "bad magic"
    opt_len = struct.unpack(">I", payload[4:8])[0]
    if opt_len > MAX_OPTIONS or 12 + opt_len > len(payload):
        return None, "bad options length"
    options = payload[8:8 + opt_len].decode("ascii", "replace")
    data_len = struct.unpack(">I", payload[8 + opt_len:12 + opt_len])[0]
    start = 12 + opt_len
    body = payload[start:start + data_len]
    if data_len > MAX_JSON or len(body) != data_len:
        return None, "bad data length"
    body = body.rstrip(b"\x00")
    try:
        obj = json.loads(body.decode("utf-8"))
    except Exception as e:
        return None, "json: %s" % e
    if not isinstance(obj, dict):
        return None, "not an object"
    return obj, options


def numeric_only(obj):
    """수집기가 저장할 수 있는 숫자 필드만 남긴다. 참/거짓은 1/0, null은 버린다."""
    out = {}
    for k, v in obj.items():
        if isinstance(v, bool):
            out[k] = 1 if v else 0
        elif isinstance(v, (int, float)):
            out[k] = v
    return out


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code != 0:
        log("브로커 접속 실패:", reason_code)
        return
    for kind in KIND_MAP:
        topic = "device/%s/+/%s" % (APP, kind)
        client.subscribe(topic, qos=0)
        log("구독:", topic)


def on_message(client, userdata, msg):
    parts = msg.topic.split("/")
    if len(parts) != 4:
        return
    _, app, sn, kind = parts
    out_kind = KIND_MAP.get(kind)
    if out_kind is None:
        return

    obj, note = decode_frame(msg.payload)
    if obj is None:
        log("해석 실패 [%s] %s (%d바이트)" % (msg.topic, note, len(msg.payload)))
        return

    fields = numeric_only(obj)
    if not fields:
        log("숫자 필드 없음 [%s] 원본 키: %s" % (msg.topic, ",".join(obj.keys())))
        return

    out_topic = "%s/%s/ctrl/%s" % (OUT_ROOT, sn, out_kind)
    client.publish(out_topic, json.dumps(fields), qos=0, retain=False)
    log("중계 %s → %s (필드 %d개)" % (msg.topic, out_topic, len(fields)))


def on_disconnect(client, userdata, flags, reason_code, properties=None):
    log("브로커 연결 끊김:", reason_code)


def main():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ag-bridge")
    client.username_pw_set(USER, PW)
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    log("브로커 접속 시도: %s:%d" % (HOST, PORT))
    while True:
        try:
            client.connect(HOST, PORT, keepalive=60)
            break
        except Exception as e:
            log("접속 실패, 5초 후 재시도:", e)
            time.sleep(5)
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    sys.exit(main())
