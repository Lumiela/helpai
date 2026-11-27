// 새 화면(모바일 앱·웹 대시보드) 메뉴에서 들어가는 기능 페이지 (2026-11-25 사용자 결정).
// 기존 관제 화면(Home·큐브 상세)은 뺐고 아래 페이지들은 지금 모양 그대로 둔다. 개발자용 둘은 개발자 도구가 켜졌을 때만.
import { pathForTab, type TabId } from './routes';

export interface AppMenuItem {
  key: TabId;
  label: string;
  sub: string;
  to: string;
}

const ITEMS: Array<{ key: TabId; label: string; sub: string; dev?: boolean }> = [
  { key: 'devices', label: '장치 연결', sub: '장치 추가 · 연결 초기화 · 장치 제거' },
  { key: 'logs', label: '알림 이력', sub: '알림 · 서버 상태 · 조작 기록' },
  { key: 'actuators', label: '프리셋', sub: '자주 쓰는 제어 값' },
  { key: 'reports', label: 'AI 리포트', sub: '리포트 간격 · 회차 목록', dev: true },
  { key: 'notification-lab', label: '알림 API 테스트', sub: '카메라 촬영 · 줌 · 동시 촬영', dev: true },
];

export function appMenu(iid: string | null, devMode: boolean): AppMenuItem[] {
  if (!iid) return [];
  return ITEMS.filter((x) => !x.dev || devMode).map(({ key, label, sub }) => ({ key, label, sub, to: pathForTab(iid, key) }));
}
