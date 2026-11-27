// Supabase Auth 연동 — 소유자 모델 (2026-09-29).
//
//   로그인   : 구글 OAuth → Supabase 가 접근 토큰(JWT, 60분) + 갱신 토큰 발급 (supabase-js 가 보관·자동 갱신)
//   권한     : 토큰의 app_metadata.instances(소유 인스턴스 목록) / app_metadata.operator(운영자 여부).
//              접근 판정은 서버(api /me)가 한다. 화면은 표시·안내용으로만 읽는다.
//   api 호출 : backend.ts 가 Authorization: Bearer <접근 토큰> 을 붙인다
//
// VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY 가 없으면 인증 없이 동작한다(AUTH_MODE=off 인 api 와 짝).
//
// 실행 시점 우회 (2026-09-30, 프로토타입 기간): 빌드에 Supabase 설정이 있어도 api /health 의 auth_mode 가 "off" 면
// 로그인 화면·토큰 부착을 건너뛰고 소유자로 동작한다(AuthContext 가 시작할 때 한 번 확인). 기능을 빼는 것이 아니라
// 우회다 — 스위치는 젯슨 /data/.env 의 AUTH_MODE 하나. required 로 되돌리고 api 를 다시 만들면 다음 새로고침부터
// 로그인이 다시 켜진다(화면 재빌드 불필요).

import { createClient, type Session, type SupabaseClient } from '@supabase/supabase-js';

export interface AuthClaims {
  sub: string;
  email: string;
  instances: string[];
  operator: boolean;
  exp: number; // epoch 초
}

function readEnv(name: string): string {
  try {
    const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env;
    return (env?.[name] ?? '').trim();
  } catch {
    return '';
  }
}

export const SUPABASE_URL = readEnv('VITE_SUPABASE_URL').replace(/\/+$/, '');
export const SUPABASE_ANON_KEY = readEnv('VITE_SUPABASE_ANON_KEY');
export const authEnabled = Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);

// api 가 AUTH_MODE=off 로 응답했을 때 true. 그동안 supabase() 는 null → 세션 조회·토큰 부착·로그인 화면 전부 건너뜀.
let bypassed = false;

export function setAuthBypass(on: boolean): void {
  bypassed = on;
}

export function authBypassed(): boolean {
  return bypassed;
}

let client: SupabaseClient | null = null;

export function supabase(): SupabaseClient | null {
  if (!authEnabled || bypassed) return null;
  if (!client) {
    client = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true, flowType: 'pkce' },
    });
  }
  return client;
}

// base64url → 문자열 (브라우저·node 공통. UTF-8 안전)
function base64UrlDecode(part: string): string {
  const b64 = part.replace(/-/g, '+').replace(/_/g, '/');
  const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4);
  const bin = atob(padded);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}

// 서명 검증은 하지 않는다(그건 api 의 몫). 화면 표시용으로 본문만 읽는다.
export function decodeJwtPayload(token: string): Record<string, unknown> | null {
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  try {
    const obj = JSON.parse(base64UrlDecode(parts[1])) as unknown;
    return obj && typeof obj === 'object' ? (obj as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

export function claimsFromToken(token: string): AuthClaims | null {
  const p = decodeJwtPayload(token);
  if (!p) return null;
  const meta = (p.app_metadata && typeof p.app_metadata === 'object' ? p.app_metadata : {}) as Record<string, unknown>;
  const instances = Array.isArray(meta.instances) ? meta.instances.filter((c): c is string => typeof c === 'string') : [];
  return {
    sub: typeof p.sub === 'string' ? p.sub : '',
    email: typeof p.email === 'string' ? p.email : '',
    instances,
    operator: meta.operator === true,
    exp: typeof p.exp === 'number' ? p.exp : 0,
  };
}

export async function getSession(): Promise<Session | null> {
  const s = supabase();
  if (!s) return null;
  const { data } = await s.auth.getSession();
  return data.session ?? null;
}

// api 요청에 붙일 접근 토큰. supabase-js 가 만료 전에 알아서 갱신한다.
export async function getAccessToken(): Promise<string | null> {
  const session = await getSession();
  return session?.access_token ?? null;
}

export async function signInWithGoogle(): Promise<void> {
  const s = supabase();
  if (!s) return;
  const { error } = await s.auth.signInWithOAuth({
    provider: 'google',
    options: { redirectTo: window.location.origin + window.location.pathname + window.location.search },
  });
  if (error) throw error;
}

export async function signOut(): Promise<void> {
  const s = supabase();
  if (!s) return;
  await s.auth.signOut();
}

// 접근 종류 표시 이름
export const ACCESS_LABEL: Record<'owner' | 'operator' | 'none', string> = {
  owner: '소유자',
  operator: '운영자 · 읽기',
  none: '접근 없음',
};
