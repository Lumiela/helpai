// 로그인·접근 상태를 화면 전체에 제공한다 — 소유자 모델, 다중 인스턴스 (2026-09-29 오후).
//
//   enabled=false : 빌드에 Supabase 설정이 없음 → 로그인 화면 없이 소유자로 취급 (api 도 AUTH_MODE=off 여야 함)
//   bypassed=true : 빌드에는 설정이 있지만 api /health 가 auth_mode "off" → 로그인 화면 없이 소유자로 취급
//                   (2026-09-30 프로토타입 우회. 스위치는 젯슨 .env 의 AUTH_MODE. auth.ts 머리말 참고)
//   loading       : 저장된 세션을 확인하는 중
//   claims        : 로그인한 사용자의 토큰 본문(표시용)
//   instanceId    : 주소 경로의 인스턴스 ID (/i/<iid>/). 없으면 /login (로그인 뒤 소유 인스턴스로 자동 이동)
//   meGlobal      : api /me — 내 소유 인스턴스 목록·운영자 여부
//   me            : api /i/<iid>/me — 이 인스턴스에 대한 접근(서버가 정함): owner | operator | none
//   access        : 'loading' | 'owner' | 'operator' | 'none'  (인스턴스 경로에서만 의미 있음)
//   claim(sn)     : 큐브 SN 으로 인스턴스 등록 → 토큰 재발급 → 인스턴스 ID
//
// 회원가입 = 첫 구글 로그인 + 큐브 등록. 로그인 카드의 SN 입력값은 구글 왕복 동안 sessionStorage 에 두었다가
// 로그인 직후 등록한다(사용자 결정: 로그인 카드에 SN 입력칸 항상 표시).
// 2026-11-18 사용자 결정 "로그인만 하고 큐브 등록은 나중에": SN 없이 로그인했고 소유 인스턴스가 0개면 큐브 없는
// 인스턴스를 자동으로 만들고(POST /instances) 토큰을 갱신해 그 인스턴스로 간다. 큐브는 장치 연결 → 장치 추가.
// 로그인 경로는 /login 하나(사용자 결정 09-29 오후). 로그인되면 ?next= 또는 유일한 소유 인스턴스로 이동한다.

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { authEnabled, claimsFromToken, setAuthBypass, signInWithGoogle, signOut as sbSignOut, supabase, type AuthClaims } from './auth';
import { BackendError, getHealth, getMe, getMeGlobal, INSTANCE_ID, postClaim, postInstance, type Me, type MeGlobal } from './backend';
import { instanceHome, safeNext } from './instancePath';

export type Access = 'loading' | 'owner' | 'operator' | 'none';

export const PENDING_SN_KEY = 'smartcube-pending-sn';

export interface AuthValue {
  enabled: boolean;
  bypassed: boolean; // 빌드엔 로그인이 있으나 api 가 AUTH_MODE=off (프로토타입 우회)
  loading: boolean;
  claims: AuthClaims | null;
  instanceId: string | null;
  meGlobal: MeGlobal | null;
  me: Me | null;
  access: Access;
  error: string | null;
  signIn: (pendingSn?: string) => Promise<void>;
  signOut: () => Promise<void>;
  refreshMe: () => Promise<void>;
  claim: (sn: string) => Promise<string>;
  claiming: boolean;
  canControl: boolean;
}

const AuthContext = createContext<AuthValue | null>(null);

function readRedirectError(): string | null {
  try {
    const hash = window.location.hash.replace(/^#/, '');
    const params = new URLSearchParams(hash);
    const desc = params.get('error_description') ?? params.get('error');
    return desc ? decodeURIComponent(desc.replace(/\+/g, ' ')) : null;
  } catch {
    return null;
  }
}

function readPendingSn(): string | null {
  try {
    const v = sessionStorage.getItem(PENDING_SN_KEY);
    return v && v.trim() ? v.trim() : null;
  } catch {
    return null;
  }
}

function writePendingSn(sn: string | null) {
  try {
    if (sn) sessionStorage.setItem(PENDING_SN_KEY, sn);
    else sessionStorage.removeItem(PENDING_SN_KEY);
  } catch {
    // 저장 공간을 못 쓰는 환경이면 등록은 로그인 뒤 화면에서 다시 입력한다
  }
}

// 빌드에 로그인이 있을 때 api 의 인증 모드를 한 번 확인한다. checking → on(로그인 켬) | off(우회).
type Mode = 'checking' | 'on' | 'off';

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const instanceId = INSTANCE_ID;
  const [mode, setMode] = useState<Mode>(authEnabled ? 'checking' : 'off');
  const enabled = authEnabled && mode === 'on';
  const bypassed = authEnabled && mode === 'off';
  const [loading, setLoading] = useState<boolean>(authEnabled);
  const [claims, setClaims] = useState<AuthClaims | null>(null);
  const [meGlobal, setMeGlobal] = useState<MeGlobal | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [access, setAccess] = useState<Access>(authEnabled ? 'loading' : 'owner');
  const [error, setError] = useState<string | null>(() => (authEnabled ? readRedirectError() : null));
  const [claiming, setClaiming] = useState(false);

  // api 인증 모드 확인. /health 는 공개 라우트. 확인에 실패하면 로그인을 켠 채로 둔다(닫힌 쪽이 안전).
  useEffect(() => {
    if (!authEnabled) return;
    let alive = true;
    getHealth()
      .then((h) => {
        if (!alive) return;
        if (h.auth_mode === 'off') {
          setAuthBypass(true);
          setMode('off');
        } else {
          setMode('on');
        }
      })
      .catch(() => {
        if (alive) setMode('on');
      });
    return () => {
      alive = false;
    };
  }, []);

  const refreshMe = useCallback(async () => {
    if (!authEnabled) {
      setAccess('owner');
      return;
    }
    try {
      const g = await getMeGlobal();
      setMeGlobal(g);
      if (instanceId) {
        const m = await getMe();
        setMe(m);
        setAccess(m.access);
      } else {
        setMe(null);
        setAccess('none');
      }
    } catch (e) {
      setMe(null);
      setAccess('none');
      if (e instanceof BackendError && e.status === 404 && instanceId) setError(`없는 인스턴스입니다: ${instanceId}`);
      else if (e instanceof BackendError && e.status !== 401) setError(`접근 확인 실패: ${e.message}`);
    }
  }, [instanceId]);

  // 큐브 SN 으로 인스턴스 등록 → 갱신 토큰으로 새 접근 토큰(소유 인스턴스 포함) → 인스턴스 ID
  const claim = useCallback(async (sn: string): Promise<string> => {
    setClaiming(true);
    setError(null);
    try {
      const r = await postClaim(sn.trim().toUpperCase());
      const s = supabase();
      if (s) await s.auth.refreshSession(); // 훅이 app_metadata.instances 를 다시 넣는다
      await refreshMe();
      return r.instance_id;
    } finally {
      setClaiming(false);
    }
  }, [refreshMe]);

  // 우회 모드: 세션 없이 소유자. 인스턴스 경로가 아니면(/, /login) api /me 의 인스턴스 목록으로 이동한다.
  useEffect(() => {
    if (!bypassed) return;
    setLoading(false);
    setAccess('owner');
    let alive = true;
    (async () => {
      try {
        const g = await getMeGlobal();
        if (!alive) return;
        setMeGlobal(g);
        if (instanceId) {
          const m = await getMe();
          if (alive) setMe(m);
        } else if (g.instances.length === 1) {
          window.location.replace(instanceHome(g.instances[0]));
        } else if (g.instances.length === 0) {
          // 등록부가 비어 있으면 큐브 없는 인스턴스를 만들고 그리로 간다(2026-11-18)
          const r = await postInstance();
          if (alive) window.location.replace(instanceHome(r.instance_id));
        }
      } catch (e) {
        if (!alive) return;
        if (e instanceof BackendError && e.status === 404 && instanceId) setError(`없는 인스턴스입니다: ${instanceId}`);
        else setError(`접근 확인 실패: ${e instanceof Error ? e.message : String(e)}`);
      }
    })();
    return () => {
      alive = false;
    };
  }, [bypassed, instanceId]);

  useEffect(() => {
    if (mode !== 'on') return;
    const s = supabase();
    if (!s) return;
    let alive = true;
    const apply = (token: string | null) => {
      if (!alive) return;
      const c = token ? claimsFromToken(token) : null;
      setClaims(c);
      setLoading(false);
      if (c) void refreshMe();
      else {
        setMeGlobal(null);
        setMe(null);
        setAccess('none');
      }
    };
    void s.auth.getSession().then(({ data }) => apply(data.session?.access_token ?? null));
    // 로그인·로그아웃·토큰 갱신마다. 갱신 때 바뀐 소유 인스턴스·운영자 여부가 여기서 반영된다.
    const { data: sub } = s.auth.onAuthStateChange((_event, session) => apply(session?.access_token ?? null));
    return () => {
      alive = false;
      sub.subscription.unsubscribe();
    };
  }, [mode, refreshMe]);

  // 로그인 카드에서 SN 을 넣고 구글 로그인한 경우: 로그인이 끝나면 한 번 등록하고 그 인스턴스로 이동
  const [pendingHandled, setPendingHandled] = useState(false);
  useEffect(() => {
    if (!claims || instanceId) return;
    const pending = readPendingSn();
    if (!pending) {
      setPendingHandled(true);
      return;
    }
    writePendingSn(null);
    void claim(pending)
      .then((iid) => {
        window.location.assign(instanceHome(iid));
      })
      .catch((e) => {
        setError(`큐브 등록 실패 (${pending}): ${e instanceof Error ? e.message : String(e)}`);
        setPendingHandled(true);
      });
  }, [claims, instanceId, claim]);

  // 로그인만 하고 큐브 등록은 나중에(2026-11-18): 소유 인스턴스·운영자 읽기 접근이 하나도 없으면 큐브 없는
  // 인스턴스를 한 번 만들고, 토큰을 갱신(훅이 app_metadata.instances 를 다시 넣음)한 뒤 그 인스턴스로 간다.
  const [autoCreated, setAutoCreated] = useState(false);
  useEffect(() => {
    if (!claims || instanceId || !meGlobal || !pendingHandled || autoCreated) return;
    if (meGlobal.instances.length > 0 || meGlobal.operator || Object.keys(meGlobal.grants ?? {}).length > 0) return;
    setAutoCreated(true);
    void (async () => {
      try {
        const r = await postInstance();
        const s = supabase();
        if (s) await s.auth.refreshSession();
        window.location.replace(instanceHome(r.instance_id));
      } catch (e) {
        setError(`인스턴스를 만들지 못했습니다: ${e instanceof Error ? e.message : String(e)}`);
      }
    })();
  }, [claims, instanceId, meGlobal, pendingHandled, autoCreated]);

  // /login 에서 로그인된 상태: ?next= 가 있으면 거기로, 소유 인스턴스가 하나면 그리로 자동 이동
  useEffect(() => {
    if (!claims || instanceId || !meGlobal || !pendingHandled) return;
    let next: string | null = null;
    try {
      next = safeNext(new URLSearchParams(window.location.search).get('next'));
    } catch {
      next = null;
    }
    const target = next ?? (meGlobal.instances.length === 1 ? instanceHome(meGlobal.instances[0]) : null);
    if (target) window.location.replace(target);
  }, [claims, instanceId, meGlobal, pendingHandled]);

  const signIn = useCallback(async (pendingSn?: string) => {
    setError(null);
    writePendingSn(pendingSn?.trim() ? pendingSn.trim().toUpperCase() : null);
    try {
      await signInWithGoogle();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const signOut = useCallback(async () => {
    await sbSignOut();
    setClaims(null);
    setMeGlobal(null);
    setMe(null);
    setAccess('none');
  }, []);

  const value = useMemo<AuthValue>(
    () => ({
      enabled,
      bypassed,
      loading,
      claims,
      instanceId,
      meGlobal,
      me,
      access,
      error,
      signIn,
      signOut,
      refreshMe,
      claim,
      claiming,
      canControl: access === 'owner',
    }),
    [enabled, bypassed, loading, claims, instanceId, meGlobal, me, access, error, signIn, signOut, refreshMe, claim, claiming],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const v = useContext(AuthContext);
  if (!v) throw new Error('useAuth 는 AuthProvider 안에서만 쓴다');
  return v;
}
