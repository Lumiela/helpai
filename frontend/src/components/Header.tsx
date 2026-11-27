import React, { useEffect, useState } from 'react';
import { LogOut, Menu, Volume2, VolumeX } from 'lucide-react';
import { soundManager } from '../lib/sound';
import { instanceDisplayName } from '../lib/instancePath';
import { EdgeNotice } from '../lib/agToolkit';
import { Dropdown } from './Dropdown';
import { BackendStatusLine } from './GreenhouseOverview';
import type { BackendState } from '../App';

interface HeaderProps {
  activeTab: string;
  setActiveTab: (tab: string) => void;
  unhandledAlertsCount: number;
  devMode: boolean;
  setDevMode: (value: boolean) => void;
  onEmergencyStop: () => void;
  // 2026-09-29 로그인(소유자 모델). user 가 null 이면 인증이 꺼진 빌드
  user?: { email: string; accessLabel: string } | null;
  // 2026-09-30 프로토타입 우회: api 가 AUTH_MODE=off 라 로그인 없이 소유자로 동작 중임을 표시
  authBypassed?: boolean;
  // 2026-09-29 오후 다중 인스턴스: 현재 인스턴스(주소 /i/<iid>/). 누르면 내 인스턴스 목록(/)
  instanceId?: string | null;
  instanceName?: string | null;
  // 2026-09-29 오후: 백엔드 연결 상태를 모든 화면의 상단 헤더에 표시
  backend?: BackendState | null;
  cubeCount?: number;
  onRefresh?: () => void;
  canStopAll?: boolean;
  onSignOut?: () => void;
}

const PAGE_LABEL: Record<string, string> = {
  overview: 'Home',
  'zone-detail': '큐브 상세',
  actuators: '프리셋',
  devices: '장치 연결',
  logs: '알림 이력',
  reports: 'AI 리포트',
  'notification-lab': '알림 API 테스트',
};

export const Header: React.FC<HeaderProps> = ({
  activeTab,
  setActiveTab,
  unhandledAlertsCount,
  devMode,
  setDevMode,
  onEmergencyStop,
  user = null,
  authBypassed = false,
  instanceId = null,
  instanceName = null,
  backend = null,
  cubeCount = 0,
  onRefresh,
  canStopAll = true,
  onSignOut,
}) => {
  const [now, setNow] = useState<Date>(() => new Date());
  const [isMuted, setIsMuted] = useState<boolean>(soundManager.getMuted());

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  const handleToggleSound = () => {
    const muted = soundManager.toggleMute();
    setIsMuted(muted);
    if (!muted) {
      soundManager.playBeep('toast');
      EdgeNotice.toast('경보음을 켰습니다.');
    } else {
      EdgeNotice.toast('경보음을 껐습니다.');
    }
  };

  // 큐브 상세는 종합 현황의 큐브를 눌러 들어가므로 메뉴에는 두지 않는다.
  const pages: Array<{ id: string; label: string; count?: number }> = [
    { id: 'overview', label: PAGE_LABEL.overview },
    { id: 'actuators', label: PAGE_LABEL.actuators },
    { id: 'devices', label: PAGE_LABEL.devices },
    { id: 'logs', label: PAGE_LABEL.logs, count: unhandledAlertsCount },
  ];
  // 2026-10-06 사용자 지시: AI 리포트는 당분간 개발자 모드 켜짐에서만
  if (devMode) pages.push({ id: 'reports', label: PAGE_LABEL.reports });
  if (devMode) pages.push({ id: 'notification-lab', label: PAGE_LABEL['notification-lab'] });

  const dateStr = now.toLocaleDateString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short' });
  const timeStr = now.toLocaleTimeString('ko-KR', { hour12: false });

  return (
    <header className="sticky top-0 z-40 shrink-0 bg-panel border-b border-line">
      <div className="mx-auto w-full max-w-[1840px] px-4 sm:px-6 h-14 flex items-center gap-3">
        <Dropdown
          label="화면 이동"
          role="menu"
          trigger={({ open, toggle, buttonProps }) => (
            <button
              type="button"
              {...buttonProps}
              onClick={toggle}
              className="btn btn-ghost btn-sm relative -ml-2 px-2"
              aria-label={open ? '메뉴 닫기' : '메뉴 열기'}
            >
              <Menu className="w-5 h-5" aria-hidden="true" />
              {unhandledAlertsCount > 0 && <span className="absolute top-1 right-1 w-2 h-2 rounded-full bg-danger" aria-hidden="true" />}
            </button>
          )}
        >
          {(close) => (
            <>
              {pages.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  role="menuitem"
                  aria-current={activeTab === p.id ? 'page' : undefined}
                  className="menu-item"
                  onClick={() => {
                    setActiveTab(p.id);
                    close();
                  }}
                >
                  <span>{p.label}</span>
                  {p.count ? <span className="badge badge-danger">{p.count}</span> : null}
                </button>
              ))}
              <div className="my-1 border-t border-line" role="separator" />
              <button type="button" role="menuitemcheckbox" aria-checked={devMode} className="menu-item" onClick={() => setDevMode(!devMode)}>
                <span>개발자 도구</span>
                <span className="text-xs text-ink-muted">{devMode ? '켜짐' : '꺼짐'}</span>
              </button>
            </>
          )}
        </Dropdown>

        <div className="flex items-baseline gap-2 min-w-0">
          <h1 className="text-base sm:text-lg font-bold text-ink-strong whitespace-nowrap">스마트큐브 관제</h1>
          {instanceId && (
            <a href="/login" className="text-xs text-ink-muted truncate hover:underline" title="내 인스턴스">
              {instanceDisplayName(instanceId) !== instanceId
                ? instanceDisplayName(instanceId)
                : instanceName ? `${instanceName} (${instanceId})` : instanceId}
            </a>
          )}
        </div>

        <div className="ml-auto flex items-center gap-2 shrink-0">
          {backend && onRefresh && (
            <div className="hidden sm:block">
              <BackendStatusLine backend={backend} cubeCount={cubeCount} onRefresh={onRefresh} />
            </div>
          )}
          <time className="hidden lg:block text-sm text-ink" dateTime={now.toISOString()}>
            {dateStr} {timeStr}
          </time>
          <button type="button" onClick={handleToggleSound} className="btn btn-ghost btn-sm" aria-label={isMuted ? '경보음 켜기' : '경보음 끄기'}>
            {isMuted ? <VolumeX className="w-4 h-4" aria-hidden="true" /> : <Volume2 className="w-4 h-4" aria-hidden="true" />}
            <span className="hidden sm:inline">{isMuted ? '경보음 꺼짐' : '경보음 켜짐'}</span>
          </button>
          {canStopAll && (
            <button type="button" onClick={onEmergencyStop} className="btn btn-danger btn-sm">
              긴급 정지
            </button>
          )}
          {authBypassed && (
            <span
              className="badge badge-warn ml-2"
              title="api 가 AUTH_MODE=off 로 동작 중입니다(프로토타입 기간). 로그인 없이 소유자 권한입니다."
            >
              인증 꺼짐
            </span>
          )}
          {user && (
            <div className="flex items-center gap-1 pl-2 border-l border-line">
              <span className="hidden md:inline text-xs text-ink-muted truncate max-w-[200px]" title={user.email}>
                {user.email} · {user.accessLabel}
              </span>
              <button type="button" onClick={onSignOut} className="btn btn-ghost btn-sm" aria-label="로그아웃">
                <LogOut className="w-4 h-4" aria-hidden="true" />
                <span className="hidden sm:inline">로그아웃</span>
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
};
