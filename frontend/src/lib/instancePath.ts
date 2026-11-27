// 주소 경로의 인스턴스 ID (2026-09-29 오후, 다중 인스턴스. 사용자 결정: 경로 분할 /i/<인스턴스ID>/).
//
//   /login                통합 로그인 화면(사용자 결정 09-29 오후). 로그인되면 소유 인스턴스로 이동.
//                         ?next=/i/<iid>/ 가 있으면 거기로. 소유 인스턴스가 여럿이면 목록, 없으면 큐브 등록.
//   /                     /login 과 같음
//   /i/jetson-01/         그 인스턴스의 대시보드. api 는 /i/jetson-01/api/…
//
// 순수 함수만 두어 node:test 로 검사한다. 인스턴스 ID 규칙은 api(IID_RE)·nginx 와 같다: 소문자·숫자·-·_ 1~64자.

export const INSTANCE_ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const PATH_RE = /^\/i\/([a-z0-9][a-z0-9_-]{0,63})(?:\/|$)/;

export function instanceFromPath(pathname: string): string | null {
  const m = PATH_RE.exec(pathname);
  return m ? m[1] : null;
}

export function instanceHome(iid: string): string {
  return `/i/${iid}/`;
}

// api 기준 경로. 전역 라우트(/health, /me, /claim)는 /api, 인스턴스 라우트는 /i/<iid>/api
export function apiBaseFor(iid: string | null, root = '/api'): string {
  const base = root.replace(/\/+$/, '');
  if (!iid) return base;
  // root 가 '/api' 꼴이면 그 앞에 /i/<iid> 를 끼운다. 다른 호스트(VITE_API_BASE=http://…/api)도 같은 규칙.
  const idx = base.lastIndexOf('/api');
  if (idx >= 0 && idx === base.length - 4) return `${base.slice(0, idx)}/i/${iid}/api`;
  return `${base}/i/${iid}`;
}

export const LOGIN_PATH = '/login';

// 로그인 뒤 돌아갈 곳. 인스턴스 홈(/i/<iid>/)만 허용한다(열린 리다이렉트 방지).
export function safeNext(value: string | null | undefined): string | null {
  if (!value) return null;
  const iid = instanceFromPath(value);
  return iid ? instanceHome(iid) : null;
}

export function loginUrl(next?: string | null): string {
  const n = safeNext(next);
  return n ? `${LOGIN_PATH}?next=${encodeURIComponent(n)}` : LOGIN_PATH;
}

// 화면에 보여 줄 인스턴스 이름(2026-11-04 사용자 지시, 프론트 전용 표기). 주소·api 경로의 ID 는 그대로 둔다.
const DISPLAY_NAME: Record<string, string> = { 'jetson-01': 'user-1' };

export function instanceDisplayName(iid: string): string {
  return DISPLAY_NAME[iid] ?? iid;
}

export function currentInstance(): string | null {
  try {
    return instanceFromPath(window.location.pathname);
  } catch {
    return null; // node 등 window 가 없는 환경
  }
}
