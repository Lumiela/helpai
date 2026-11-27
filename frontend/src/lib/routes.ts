// 화면별 주소 (2026-09-29 오후, 사용자 결정: 탭마다 주소를 나눈다. 새로 고침·뒤로 가기·링크 공유가 되게).
//
//   /i/<iid>/                Home (overview)
//   /i/<iid>/cubes/<sn>      큐브 상세 (zone-detail)
//   /i/<iid>/presets         프리셋 (actuators)
//   /i/<iid>/devices         장치 연결
//   /i/<iid>/logs            알림 이력 (+ 서버 상태·조작 기록)
//   /i/<iid>/reports         AI 리포트 (2026-10-06: 리포트 간격 설정·회차 목록·재요청)
//   /i/<iid>/lab             알림 API 테스트 (개발자 도구가 켜졌을 때만)
//
// 탭 ID(코드 내부 이름)는 그대로 두고 주소 조각만 읽기 쉬운 영어로 한다. 순수 함수만 두어 node:test 로 검사한다.

import { instanceFromPath, instanceHome } from './instancePath';

export type TabId = 'overview' | 'zone-detail' | 'actuators' | 'devices' | 'logs' | 'reports' | 'notification-lab';

const TAB_SEGMENT: Record<Exclude<TabId, 'overview' | 'zone-detail'>, string> = {
  actuators: 'presets',
  devices: 'devices',
  logs: 'logs',
  reports: 'reports',
  'notification-lab': 'lab',
};

const SEGMENT_TAB: Record<string, TabId> = Object.fromEntries(
  Object.entries(TAB_SEGMENT).map(([tab, seg]) => [seg, tab as TabId]),
);

export interface InstanceRoute {
  iid: string | null;
  tab: TabId;
  sn: string | null; // zone-detail 일 때 큐브 일련번호
  known: boolean; // 알 수 없는 조각이면 false (Home 으로 취급)
}

export function parseInstanceRoute(pathname: string): InstanceRoute {
  const iid = instanceFromPath(pathname);
  if (!iid) return { iid: null, tab: 'overview', sn: null, known: false };
  const rest = pathname.slice(instanceHome(iid).length).replace(/\/+$/, '');
  if (rest === '') return { iid, tab: 'overview', sn: null, known: true };
  const parts = rest.split('/').map((p) => decodeURIComponent(p));
  if (parts[0] === 'cubes') {
    return { iid, tab: 'zone-detail', sn: parts[1] && parts.length === 2 ? parts[1] : null, known: parts.length === 2 };
  }
  const tab = parts.length === 1 ? SEGMENT_TAB[parts[0]] : undefined;
  if (tab) return { iid, tab, sn: null, known: true };
  return { iid, tab: 'overview', sn: null, known: false };
}

export function pathForTab(iid: string, tab: TabId, sn?: string | null): string {
  const home = instanceHome(iid);
  if (tab === 'overview') return home;
  if (tab === 'zone-detail') return sn ? `${home}cubes/${encodeURIComponent(sn)}` : home;
  return `${home}${TAB_SEGMENT[tab]}`;
}
