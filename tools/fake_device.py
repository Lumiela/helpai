# -*- coding: utf-8 -*-
"""
ESP32 대역 시험 발행기. 펌웨어와 동일한 아라온실 VALUE v1 프레임을 만들어
암호화 MQTT(8443)로 원격 서버에 보낸다. 실기 없이 전 구간을 확인하는 용도.
"""
import json
import os
import ssl
import struct
import sys
import time

import paho.mqtt.client as mqtt

HOST = os.environ["MQTT_HOST"]
PORT = int(os.environ.get("MQTT_PORT", "8443"))
USER = os.environ["MQTT_USERNAME"]
PW = os.environ["MQTT_PASSWORD"]
CA = os.environ.get("MQTT_CA", "/isrg.pem")
SN = os.environ.get("DEVICE_SN", "SC-01")
APP = os.environ.get("AG_APP", "ag.smartcube.esp32")
CLIENT = "device/%s/%s" % (APP, SN)


def encode(json_text, options):
    body = json_text.encode("utf-8")
    opt = options.encode("ascii")
    out = bytearray(b"\xff\xa3\x01\x01")
    out += struct.pack(">I", len(opt))
    out += opt
    out += struct.pack(">I", len(body))
    out += body
    return bytes(out)


def telemetry():
    """펌웨어 publishTelemetry 와 같은 필드 구성."""
    return {
        "session": "0123456789abcdef0123456789abcdef",
        "uptime_ms": int(time.monotonic() * 1000),
        "applied_seq": 0,
        "ads1115_ok": True,
        "moisture_calibrated": False,
        "moisture_raw": 18450,
        "moisture_v": 18450 * 0.000125,
        "moisture_pct": None,
        "fan1_rpm": 1820,
        "fan2_rpm": 1760,
        "led1": 70,
        "led2": 0,
        "pump": False,
        "fan1": 60,
        "fan2": 60,
        "lease_active": True,
        "pump_locked": False,
        "rssi_dbm": -58,
    }


def main():
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    options = "data-type=application/json\r\nlast-modified=%s" % stamp

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=CLIENT)
    client.username_pw_set(USER, PW)
    client.tls_set(ca_certs=CA, cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)
    print("접속: %s:%d (암호화, 인증서 검증)" % (HOST, PORT), flush=True)
    client.connect(HOST, PORT, keepalive=60)
    client.loop_start()
    time.sleep(2)
    if not client.is_connected():
        print("접속 실패", flush=True)
        return 1
    print("접속 성공", flush=True)

    frame = encode(json.dumps(telemetry()), options)
    topic = CLIENT + "/public"
    info = client.publish(topic, frame, qos=0, retain=False)
    info.wait_for_publish(5)
    print("발행: %s (%d바이트 프레임)" % (topic, len(frame)), flush=True)

    time.sleep(2)
    client.loop_stop()
    client.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
