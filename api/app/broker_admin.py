# -*- coding: utf-8 -*-
"""
브로커 계정 관리 — mosquitto 동적 보안(dynamic-security) 플러그인 제어 (2026-09-29 오후, 사용자 결정).

  · 브로커 계정은 **인스턴스당 하나**(사용자명 = 인스턴스 ID). 고객이 큐브를 더 사도 같은 인스턴스 계정으로 붙는다.
  · 인스턴스 역할 inst-<iid> 의 ACL = 그 인스턴스 큐브들의 토픽(device/<앱>/<SN>/#)만 + 그룹 토픽(device/<앱>) 구독.
    → 다른 인스턴스 큐브 토픽은 발행·구독 불가(미결 P16 브로커 ACL 해소).
  · 명령은 MQTT 제어 토픽 $CONTROL/dynamic-security/v1 로 보내고 응답은 …/v1/response 에서 받는다.
    api 의 브로커 계정(MQTT_USER)이 admin 역할이어야 한다(마이그레이션 스크립트가 만든다).
  · 짧게 사는 paho 클라이언트를 명령마다 하나 만들어 동기로 처리한다(호출은 run_in_executor 로).
  · BROKER_ADMIN=0 이면 전부 건너뛴다(시험·플러그인 미적용 브로커).

환경변수
  BROKER_ADMIN            1(기본) | 0
  BROKER_HOST/PORT        mosquitto:1883 (main 과 같음)
  MQTT_USER/MQTT_PASSWORD api 계정(admin 역할)
  AG_APP                  토픽의 앱 이름 (helpai.cube)
  BROKER_PUBLIC_HOST/PORT 소유자에게 보여 줄 장치 접속 주소 (Funnel: helphyai-jetson.tail3126a6.ts.net:8443, TLS)
"""
import json
import os
import secrets
import threading
import time
from typing import List, Optional

import paho.mqtt.client as mqtt

ENABLED = os.environ.get("BROKER_ADMIN", "1").strip() not in ("0", "false", "no", "")
BROKER_HOST = os.environ.get("BROKER_HOST", "mosquitto")
BROKER_PORT = int(os.environ.get("BROKER_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
AG_APP = os.environ.get("AG_APP", "ag.smartcube.esp32")
PUBLIC_HOST = os.environ.get("BROKER_PUBLIC_HOST", "").strip()
PUBLIC_PORT = int(os.environ.get("BROKER_PUBLIC_PORT", "8443") or "8443")
TIMEOUT_S = 6.0

CONTROL = "$CONTROL/dynamic-security/v1"
RESPONSE = CONTROL + "/response"

stats = {"enabled": ENABLED, "ok": 0, "failed": 0, "last_error": None, "ensured": []}
_log = print
_ensured = set()   # 이 프로세스에서 확인이 끝난 인스턴스


def set_logger(fn):
    global _log
    _log = fn


def role_name(iid: str) -> str:
    return "inst-" + iid


def new_password(n: int = 24) -> str:
    """장치 비밀번호. `$`·공백 없는 URL-safe 문자만(펌웨어 secrets.h 와 .env 에 그대로 붙여 넣을 수 있게)."""
    return secrets.token_urlsafe(n).replace("-", "x").replace("_", "y")[:32]


def public_endpoint():
    return {"host": PUBLIC_HOST, "port": PUBLIC_PORT, "tls": True, "app": AG_APP}


class DynsecError(RuntimeError):
    pass


def _run(commands: List[dict]) -> List[dict]:
    """명령 묶음을 보내고 응답 목록을 돌려준다(동기). 연결·응답 실패는 DynsecError."""
    if not ENABLED:
        raise DynsecError("BROKER_ADMIN=0")
    done = threading.Event()
    box = {"resp": None, "err": None}

    def on_connect(c, u, flags, rc, props=None):
        if rc != 0:
            box["err"] = "브로커 접속 실패: %s" % rc
            done.set()
            return
        c.subscribe(RESPONSE, qos=1)
        c.publish(CONTROL, json.dumps({"commands": commands}), qos=1)

    def on_message(c, u, msg):
        try:
            box["resp"] = json.loads(msg.payload.decode("utf-8"))
        except Exception as e:   # noqa: BLE001
            box["err"] = "응답 해석 실패: %s" % e
        done.set()

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="smartcube-api-admin-%d" % (int(time.time() * 1000) % 100000))
    if MQTT_USER:
        c.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    c.on_connect = on_connect
    c.on_message = on_message
    try:
        c.connect(BROKER_HOST, BROKER_PORT, keepalive=30)
        c.loop_start()
        if not done.wait(TIMEOUT_S):
            raise DynsecError("동적 보안 응답 없음(플러그인 미적용?)")
        if box["err"]:
            raise DynsecError(box["err"])
        responses = (box["resp"] or {}).get("responses")
        # 플러그인이 없는 브로커도 응답 토픽에 무언가를 흘려 줄 수 있다 → 명령 수와 응답 수가 맞아야 성공으로 본다
        if not isinstance(responses, list) or len(responses) != len(commands):
            raise DynsecError("동적 보안 응답 형식 아님(플러그인 미적용?): %s" % str(box["resp"])[:80])
        return responses
    finally:
        try:
            c.loop_stop()
            c.disconnect()
        except Exception:   # noqa: BLE001
            pass


_IGNORABLE = ("already exists", "already a member", "already has")


def _check(responses: List[dict], ignore_exists: bool = True):
    for r in responses:
        err = r.get("error")
        if err and not (ignore_exists and any(s in err for s in _IGNORABLE)):
            raise DynsecError("%s: %s" % (r.get("command"), err))


def _acl(rolename: str, acltype: str, topic: str, allow: bool = True, priority: int = 0):
    return {"command": "addRoleACL", "rolename": rolename, "acltype": acltype, "topic": topic,
            "allow": allow, "priority": priority}


def cube_acls(iid: str, sn: str) -> List[dict]:
    r = role_name(iid)
    t = "device/%s/%s/#" % (AG_APP, sn)
    return [_acl(r, "publishClientSend", t), _acl(r, "publishClientReceive", t), _acl(r, "subscribePattern", t),
            _acl(r, "unsubscribePattern", t)]


def group_acls(iid: str) -> List[dict]:
    r = role_name(iid)
    g = "device/%s" % AG_APP
    return [_acl(r, "subscribeLiteral", g), _acl(r, "unsubscribeLiteral", g), _acl(r, "publishClientReceive", g)]


def ensure_instance(iid: str, cubes: List[str], password: Optional[str], cameras: Optional[List[str]] = None) -> None:
    """역할·ACL·클라이언트가 있게 만든다(멱등). password 가 None 이면 클라이언트는 만들지 않는다. cameras 도 같은 역할에."""
    cmds = [{"command": "createRole", "rolename": role_name(iid)}] + group_acls(iid)
    for sn in list(cubes) + list(cameras or []):
        cmds += cube_acls(iid, sn)
    if password:
        # createClient 가 rolename 으로 역할까지 붙인다. 이미 있는 클라이언트에 addClientRole 을 또 보내면
        # 플러그인이 "Internal error" 를 돌려주므로(09-29 15:07 겪음) 보내지 않는다.
        cmds.append({"command": "createClient", "username": iid, "password": password, "rolename": role_name(iid)})
    _check(_run(cmds))


def add_cube(iid: str, sn: str) -> None:
    _check(_run(cube_acls(iid, sn)))


def set_password(iid: str, password: str) -> None:
    resp = _run([{"command": "setClientPassword", "username": iid, "password": password}])
    # 클라이언트가 아직 없으면 만든다
    if any("not found" in (r.get("error") or "") for r in resp):
        _check(_run([{"command": "createClient", "username": iid, "password": password, "rolename": role_name(iid)}]))
    else:
        _check(resp)


def probe() -> bool:
    """플러그인이 살아 있는지. 실패하면 False (예외 없음)."""
    try:
        _check(_run([{"command": "getDefaultACLAccess"}]))
        return True
    except Exception as e:   # noqa: BLE001
        stats["last_error"] = str(e)[:200]
        return False


def ensure_or_log(iid: str, cubes: List[str], password: Optional[str], cameras: Optional[List[str]] = None) -> bool:
    """실패해도 예외를 올리지 않고 로그·통계만. 성공한 인스턴스는 기억해 두 번 하지 않는다."""
    if not ENABLED:
        return False
    if iid in _ensured:
        return True
    try:
        ensure_instance(iid, cubes, password, cameras)
        _ensured.add(iid)
        stats["ok"] += 1
        stats["ensured"] = sorted(_ensured)
        stats["last_error"] = None
        _log("[broker] 인스턴스 %s 계정·역할 확인 (큐브 %s, 카메라 %s)" % (iid, ",".join(cubes) or "-", ",".join(cameras or []) or "-"))
        return True
    except Exception as e:   # noqa: BLE001
        stats["failed"] += 1
        stats["last_error"] = str(e)[:200]
        _log("[broker] 인스턴스 %s 확인 실패: %s" % (iid, str(e)[:160]))
        return False


def forget(iid: str):
    _ensured.discard(iid)


def status():
    d = dict(stats)
    d.update(public_endpoint())
    return d
