# -*- coding: utf-8 -*-
"""
인증·권한 — 소유자 모델, 다중 인스턴스 (2026-09-29 오후. 사용자 결정 J56~J58 + 같은 날 판정).

    api 하나가 인스턴스 여러 개를 맡는다. 인스턴스 ID 는 요청 경로 /i/{iid}/... 에서 읽는다.
    (09-29 오전 판 = 환경변수 INSTANCE_ID 하나. 컨테이너를 늘리지 않고 경로로 나누기로 함)

  · 소유자: 토큰의 app_metadata.instances 에 그 iid 가 있으면 소유자 → 조회·제어·보정 전부.
  · 그 외: 전부 403. 보기 권한도 없다. 관리자 역할, 역할·소유 지정 화면은 없다(J57).
  · 운영자(서비스 제공자): app_metadata.operator = true 인 계정이 **마스터 키 + 사유**를 내면
    그 인스턴스에 대해 OPERATOR_GRANT_MINUTES(기본 60분) 동안 **읽기 전용** 접근. 발급·거절은 audit 에 기록.
  · 소유자·운영자 목록은 Supabase 표(instances, operators)에 있고 접근 토큰 훅이 토큰에 넣는다.
    api 는 요청마다 Supabase 에 물어보지 않는다(토큰 서명·만료만 검사). 인스턴스·큐브 목록은 registry.
  · AUTH_MODE=off: 모든 요청을 모든 인스턴스의 소유자로 통과(개발·병행 시험 전용).

환경변수
  AUTH_MODE                required(기본) | off
  SUPABASE_URL             https://<ref>.supabase.co
  SUPABASE_JWT_SECRET      구 프로젝트 HS256 비밀. 새 프로젝트(비대칭 키)는 빈 값
  SUPABASE_JWKS_URL        기본 <SUPABASE_URL>/auth/v1/.well-known/jwks.json
  SUPABASE_ISSUER          기본 <SUPABASE_URL>/auth/v1
  OPERATOR_MASTER_KEY      운영자 마스터 키. 비어 있으면 운영자 접근 자체가 불가
  OPERATOR_GRANT_MINUTES   운영자 읽기 접근 수명(기본 60)
"""
import asyncio
import hmac
import os
import time
import uuid
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request
from jwt import PyJWKClient

from . import registry

AUTH_MODE = os.environ.get("AUTH_MODE", "required").strip().lower()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "").strip()
SUPABASE_JWKS_URL = os.environ.get("SUPABASE_JWKS_URL", "").strip() or (
    SUPABASE_URL + "/auth/v1/.well-known/jwks.json" if SUPABASE_URL else "")
SUPABASE_ISSUER = os.environ.get("SUPABASE_ISSUER", "").strip() or (
    SUPABASE_URL + "/auth/v1" if SUPABASE_URL else "")
AUDIENCE = "authenticated"

OPERATOR_MASTER_KEY = os.environ.get("OPERATOR_MASTER_KEY", "").strip()
OPERATOR_GRANT_MINUTES = int(os.environ.get("OPERATOR_GRANT_MINUTES", "60") or "60")


class User:
    __slots__ = ("sub", "email", "instances", "operator", "local", "access")

    def __init__(self, sub: str, email: str, instances, operator: bool, local: bool = False):
        self.sub = sub
        self.email = email
        self.instances = [i for i in (instances or []) if isinstance(i, str)]
        self.operator = bool(operator)
        self.local = local          # AUTH_MODE=off 로 만들어진 가짜 사용자
        self.access = "none"        # none | owner | operator  (의존성이 현재 인스턴스에 대해 채운다)

    def is_owner(self, iid: str) -> bool:
        if self.local:
            return True
        return bool(iid) and iid in self.instances

    def access_to(self, iid: str) -> str:
        if self.is_owner(iid):
            return "owner"
        if self.operator and grant_for(self.sub, iid):
            return "operator"
        return "none"

    def user_uuid(self) -> Optional[str]:
        """감사 기록·등록용. Supabase 사용자 ID 는 UUID 다. 아니면 None."""
        try:
            return str(uuid.UUID(self.sub))
        except (ValueError, TypeError, AttributeError):
            return None

    def as_dict(self, iid: Optional[str] = None):
        """iid 없이: 전역(내 인스턴스 목록·운영자 여부·살아 있는 읽기 접근). iid 있으면 그 인스턴스의 접근."""
        owned = registry.ids() if self.local else [i for i in self.instances if registry.instance(i) is not None]
        d = {"sub": self.sub, "email": self.email, "instances": owned, "operator": self.operator,
             "grants": grants_of(self.sub) if self.operator else {}, "auth_mode": AUTH_MODE}
        if iid is not None:
            g = grant_for(self.sub, iid) if self.operator else None
            inst = registry.instance(iid) or {}
            d.update({"instance_id": iid, "instance_name": inst.get("name"), "is_owner": self.is_owner(iid),
                      "access": self.access_to(iid), "grant": g, "cubes": registry.cubes_of(iid)})
        return d


# ──────────────────────────────────────────────── 설정 검사·설명

def configured() -> bool:
    """서버를 띄워도 되는 상태인지. required 인데 검증 수단이 없으면 False."""
    if AUTH_MODE == "off":
        return True
    return bool(SUPABASE_JWT_SECRET or SUPABASE_JWKS_URL)


def describe() -> str:
    if AUTH_MODE == "off":
        return "off (모든 요청을 소유자로 통과 — 개발·병행 시험 전용)"
    how = []
    if SUPABASE_JWKS_URL:
        how.append("JWKS " + SUPABASE_JWKS_URL)
    if SUPABASE_JWT_SECRET:
        how.append("HS256 공유 비밀")
    return "required (%s, 발급자 %s, 운영자 마스터 키 %s)" % (
        " / ".join(how) or "검증 수단 없음", SUPABASE_ISSUER or "미지정",
        "설정됨" if OPERATOR_MASTER_KEY else "없음")


# ──────────────────────────────────────────────── 토큰 검증

_jwk_client: Optional[PyJWKClient] = None


def _jwks() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = PyJWKClient(SUPABASE_JWKS_URL, cache_keys=True, lifespan=3600)
    return _jwk_client


def verify(token: str) -> User:
    """JWT 를 검증하고 User 를 돌려준다. 실패하면 HTTPException(401)."""
    common = dict(audience=AUDIENCE, options={"require": ["exp", "sub"]})
    if SUPABASE_ISSUER:
        common["issuer"] = SUPABASE_ISSUER
    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg", "")
        if alg == "HS256":
            if not SUPABASE_JWT_SECRET:
                raise HTTPException(401, "HS256 토큰인데 SUPABASE_JWT_SECRET 이 없음")
            claims = jwt.decode(token, SUPABASE_JWT_SECRET, algorithms=["HS256"], **common)
        elif alg in ("ES256", "RS256"):
            if not SUPABASE_JWKS_URL:
                raise HTTPException(401, "비대칭 토큰인데 SUPABASE_JWKS_URL 이 없음")
            key = _jwks().get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=[alg], **common)
        else:
            raise HTTPException(401, "지원하지 않는 서명 방식: %s" % alg)
    except HTTPException:
        raise
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "토큰 만료 — 다시 로그인하세요")
    except (jwt.PyJWTError, ValueError, TypeError) as e:
        raise HTTPException(401, "토큰 검증 실패: %s" % e)

    meta = claims.get("app_metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    return User(str(claims.get("sub", "")), str(claims.get("email", "")),
                meta.get("instances") or [], meta.get("operator") is True)


def _bearer(request: Request) -> Optional[str]:
    h = request.headers.get("authorization", "")
    if h[:7].lower() == "bearer ":
        return h[7:].strip() or None
    return None


async def current_user(request: Request) -> User:
    """로그인만 확인. 접근 판정은 owner / reader 가 한다."""
    if AUTH_MODE == "off":
        u = User("local", "local@auth-off", registry.ids(), False, local=True)
        u.access = "owner"
        return u
    token = _bearer(request)
    if not token:
        raise HTTPException(401, "로그인이 필요합니다")
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, verify, token)   # JWKS 첫 조회는 네트워크라 스레드로


# ──────────────────────────────────────────────── 운영자 읽기 접근 (마스터 키, 인스턴스별)

_grants = {}   # (sub, iid) -> {"until": epoch, "issued_at": epoch, "reason": str}


def grant_for(sub: str, iid: str):
    g = _grants.get((sub, iid))
    if g is None:
        return None
    if g["until"] <= time.time():
        _grants.pop((sub, iid), None)
        return None
    return dict(g)


def grants_of(sub: str):
    out = {}
    for (s, iid) in list(_grants):
        if s == sub:
            g = grant_for(sub, iid)
            if g:
                out[iid] = g
    return out


def issue_grant(user: User, iid: str, reason: str):
    now = time.time()
    g = {"until": now + OPERATOR_GRANT_MINUTES * 60, "issued_at": now, "reason": reason}
    _grants[(user.sub, iid)] = g
    return dict(g)


def revoke_grant(sub: str, iid: str) -> bool:
    return _grants.pop((sub, iid), None) is not None


def master_key_ok(candidate: str) -> bool:
    if not OPERATOR_MASTER_KEY or not candidate:
        return False
    return hmac.compare_digest(OPERATOR_MASTER_KEY.encode("utf-8"), candidate.encode("utf-8"))


# ──────────────────────────────────────────────── 접근 의존성 (경로의 iid 를 받는다)

def require_instance(iid: str) -> str:
    """경로의 인스턴스 ID 가 등록부에 있어야 한다. 없으면 404."""
    if registry.instance(iid) is None:
        raise HTTPException(404, "없는 인스턴스: %s" % iid)
    return iid


async def owner(iid: str, user: User = Depends(current_user)) -> User:
    """소유자만. 제어·보정·refresh 에 쓴다."""
    require_instance(iid)
    if user.is_owner(iid):
        user.access = "owner"
        return user
    if user.operator and grant_for(user.sub, iid):
        raise HTTPException(403, "운영자 접근은 읽기 전용입니다")
    raise HTTPException(403, "이 인스턴스의 소유자가 아닙니다")


async def reader(iid: str, user: User = Depends(current_user)) -> User:
    """소유자, 또는 유효한 마스터 키 접근을 가진 운영자. 조회에 쓴다."""
    require_instance(iid)
    if user.is_owner(iid):
        user.access = "owner"
        return user
    if user.operator and grant_for(user.sub, iid):
        user.access = "operator"
        return user
    raise HTTPException(403, "이 인스턴스의 소유자가 아닙니다")
