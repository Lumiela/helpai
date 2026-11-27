import React, { useState } from 'react';
import { Download } from 'lucide-react';
import { NotificationLogItem } from '../types/smartfarm';
import { EdgeNotice } from '../lib/agToolkit';

interface AlarmRulesAndLogsProps {
  logs: NotificationLogItem[];
  onClearLogs: () => void;
  onSimulateAlarmScenario: (scenario: string) => void;
  devMode: boolean;
}

const TYPE_LABEL: Record<NotificationLogItem['type'], string> = {
  toast: '간단 알림',
  info: '정보 알림',
  message: '확인 요청',
  discover: '장치 발견',
};

const SEVERITY: Record<NotificationLogItem['severity'], { text: string; cls: string }> = {
  info: { text: '정보', cls: 'badge-info' },
  warning: { text: '주의', cls: 'badge-warn' },
  danger: { text: '위험', cls: 'badge-danger' },
  success: { text: '완료', cls: 'badge-ok' },
};

const SCENARIOS: Array<{ id: string; title: string; desc: string }> = [
  { id: 'heatwave', title: '폭염', desc: '외부 온도 36°C 초과, 정보 알림' },
  { id: 'frost', title: '한파', desc: 'SC-03 내부 8.2°C, 긴급 확인' },
  { id: 'ec_imbalance', title: '양액 혼합비 이상', desc: 'A액과 B액 편차 18%, 정보 알림' },
  { id: 'power_fail', title: '상용 전원 정전', desc: '비상 발전기 전환, 긴급 확인' },
];

const pad2 = (n: number) => String(n).padStart(2, '0');

// 표에 보이는 발생 시각: 날짜와 24시간제 시각
function formatLogTime(item: NotificationLogItem): string {
  if (item.at === undefined) return item.timestamp;
  const d = new Date(item.at);
  return `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

// datetime-local 입력값 ↔ epoch ms (현지 시간 기준)
function parseLocalInput(value: string): number | null {
  if (!value) return null;
  const ms = new Date(value).getTime();
  return Number.isNaN(ms) ? null : ms;
}

// 입력 칸의 어느 부분을 눌러도 날짜·시각 선택기가 열리게 한다 (기본은 달력 아이콘만 연다)
function openPicker(e: React.MouseEvent<HTMLInputElement>) {
  const el = e.currentTarget as HTMLInputElement & { showPicker?: () => void };
  try {
    el.showPicker?.();
  } catch {
    // 선택기를 지원하지 않는 브라우저에서는 기본 동작(글자 부분 편집)만 남는다.
  }
}

export const AlarmRulesAndLogs: React.FC<AlarmRulesAndLogsProps> = ({ logs, onClearLogs, onSimulateAlarmScenario, devMode }) => {
  const [filterType, setFilterType] = useState<string>('all');
  const [filterSeverity, setFilterSeverity] = useState<string>('all');
  const [fromInput, setFromInput] = useState<string>('');
  const [toInput, setToInput] = useState<string>('');

  const fromMs = parseLocalInput(fromInput);
  const toMs = parseLocalInput(toInput);
  const rangeInverted = fromMs !== null && toMs !== null && fromMs > toMs;
  const anyFilter = filterType !== 'all' || filterSeverity !== 'all' || fromInput !== '' || toInput !== '';

  const filteredLogs = logs.filter((log) => {
    if (filterType !== 'all' && log.type !== filterType) return false;
    if (filterSeverity !== 'all' && log.severity !== filterSeverity) return false;
    if (!rangeInverted && log.at !== undefined) {
      if (fromMs !== null && log.at < fromMs) return false;
      if (toMs !== null && log.at > toMs) return false;
    }
    return true;
  });

  const clearFilters = () => {
    setFilterType('all');
    setFilterSeverity('all');
    setFromInput('');
    setToInput('');
  };

  const handleExportJson = () => {
    const dataStr = 'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(filteredLogs, null, 2));
    const a = document.createElement('a');
    a.setAttribute('href', dataStr);
    a.setAttribute('download', `smartcube-notification-logs-${Date.now()}.json`);
    document.body.appendChild(a);
    a.click();
    a.remove();
    EdgeNotice.toast(`알림 이력 ${filteredLogs.length}건을 JSON 파일로 내려받았습니다.`);
  };

  const handleClear = () => {
    EdgeNotice.confirm('알림 이력 비우기', `기록 ${logs.length}건을 모두 지웁니다. 되돌릴 수 없습니다.`, onClearLogs, '모두 지우기');
  };

  return (
    <div className="bounded-page flex flex-col gap-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-ink-strong">알림 이력</h2>
          <p className="text-sm text-ink-muted">발송된 알림 {logs.length}건. 확인 요청과 위험 알림은 처리 전까지 헤더에 건수로 표시됩니다.</p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={handleExportJson} className="btn btn-secondary btn-sm">
            <Download className="w-4 h-4" aria-hidden="true" />
            <span>JSON 내려받기</span>
          </button>
          <button type="button" onClick={handleClear} disabled={logs.length === 0} className="btn btn-secondary btn-sm">
            이력 비우기
          </button>
        </div>
      </header>

      {devMode && (
        <section className="panel p-5 space-y-3" aria-labelledby="scenario-title">
          <div className="flex items-center justify-between">
            <h3 id="scenario-title" className="text-base font-semibold text-ink-strong">
              시나리오 모의 실행
            </h3>
            <span className="text-sm text-ink-muted">개발자 도구</span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
            {SCENARIOS.map((sc) => (
              <button
                key={sc.id}
                type="button"
                onClick={() => onSimulateAlarmScenario(sc.id)}
                className="bg-surface border border-line rounded-lg p-3 text-left hover:bg-inset"
              >
                <div className="text-sm font-semibold text-ink-strong">{sc.title}</div>
                <div className="text-xs text-ink-muted mt-1">{sc.desc}</div>
              </button>
            ))}
          </div>
        </section>
      )}

      <section className="log-panel panel flex min-h-0 flex-col" aria-labelledby="log-table-title">
        <div className="shrink-0 px-5 py-3 border-b border-line flex flex-wrap items-center justify-between gap-3">
          <h3 id="log-table-title" className="text-base font-semibold text-ink-strong">
            기록 {filteredLogs.length !== logs.length && <span className="text-sm font-normal text-ink-muted">{filteredLogs.length}건 표시</span>}
          </h3>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
            <label className="flex items-center gap-2">
              <span className="text-ink-muted">종류</span>
              <select value={filterType} onChange={(e) => setFilterType(e.target.value)}>
                <option value="all">전체</option>
                <option value="toast">간단 알림</option>
                <option value="info">정보 알림</option>
                <option value="message">확인 요청</option>
                <option value="discover">장치 발견</option>
              </select>
            </label>
            <label className="flex items-center gap-2">
              <span className="text-ink-muted">심각도</span>
              <select value={filterSeverity} onChange={(e) => setFilterSeverity(e.target.value)}>
                <option value="all">전체</option>
                <option value="info">정보</option>
                <option value="warning">주의</option>
                <option value="danger">위험</option>
                <option value="success">완료</option>
              </select>
            </label>
            <label className="flex items-center gap-2">
              <span className="text-ink-muted">시작일시</span>
              <input
                type="datetime-local"
                value={fromInput}
                max={toInput || undefined}
                onClick={openPicker}
                onChange={(e) => setFromInput(e.target.value)}
                aria-label="시작일시"
              />
            </label>
            <label className="flex items-center gap-2">
              <span className="text-ink-muted">종료일시</span>
              <input
                type="datetime-local"
                value={toInput}
                min={fromInput || undefined}
                onClick={openPicker}
                onChange={(e) => setToInput(e.target.value)}
                aria-label="종료일시"
              />
            </label>
            {anyFilter && (
              <button type="button" onClick={clearFilters} className="btn btn-ghost btn-sm">
                필터 지우기
              </button>
            )}
          </div>
        </div>

        {rangeInverted && (
          <p className="px-5 py-2 text-sm text-heat border-b border-line">시작일시가 종료일시보다 늦습니다. 기간 조건은 적용하지 않았습니다.</p>
        )}

        {filteredLogs.length === 0 ? (
          <div className="px-5 py-12 text-center text-sm text-ink-muted">
            {logs.length === 0
              ? '아직 기록된 알림이 없습니다. 큐브에서 이상이 감지되거나 구동기를 조작하면 여기에 쌓입니다.'
              : '조건에 맞는 알림이 없습니다. 종류, 심각도, 조회 기간을 넓혀 보세요.'}
          </div>
        ) : (
          <>
            {/* 좁은 화면: 표 대신 목록 */}
            <ul className="log-list list-scroll md:hidden divide-y divide-line" aria-label="알림 기록 목록" tabIndex={0}>
              {filteredLogs.map((item) => {
                const sev = SEVERITY[item.severity];
                return (
                  <li key={item.id} className="px-4 py-3 space-y-1">
                    <div className="flex flex-wrap items-center gap-2 text-xs text-ink-muted">
                      <span>{formatLogTime(item)}</span>
                      <span>{TYPE_LABEL[item.type]}</span>
                      <span className={`badge ${sev.cls}`}>{sev.text}</span>
                      <span className="ml-auto">{item.handled === false ? '확인 대기' : '전송 완료'}</span>
                    </div>
                    {item.title && <div className="text-sm font-semibold text-ink-strong">{item.title}</div>}
                    <div className="text-sm text-ink">{item.content}</div>
                  </li>
                );
              })}
            </ul>

            <div className="log-list list-scroll hidden md:block" role="region" aria-label="알림 기록 목록" tabIndex={0}>
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">발생 시각</th>
                  <th scope="col">종류</th>
                  <th scope="col">심각도</th>
                  <th scope="col">내용</th>
                  <th scope="col">상태</th>
                </tr>
              </thead>
              <tbody>
                {filteredLogs.map((item) => {
                  const sev = SEVERITY[item.severity];
                  return (
                    <tr key={item.id}>
                      <td className="whitespace-nowrap text-ink-muted">{formatLogTime(item)}</td>
                      <td className="whitespace-nowrap">{TYPE_LABEL[item.type]}</td>
                      <td className="whitespace-nowrap">
                        <span className={`badge ${sev.cls}`}>{sev.text}</span>
                      </td>
                      <td>
                        {item.title && <div className="font-semibold text-ink-strong">{item.title}</div>}
                        <div className="text-ink">{item.content}</div>
                      </td>
                      <td className="whitespace-nowrap text-ink-muted">{item.handled === false ? '확인 대기' : '전송 완료'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            </div>
          </>
        )}
      </section>
    </div>
  );
};
