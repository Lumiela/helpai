import React, { useCallback, useEffect, useState } from 'react';
import { FileJson, RefreshCw, RotateCcw, Send, X } from 'lucide-react';
import {
  getReport,
  getReportSettings,
  getReports,
  postReportNow,
  postReportRetry,
  putReportSettings,
  type ReportJob,
  type ReportSettings,
  type ReportStatus,
} from '../lib/backend';

// AI 리포트 탭 (2026-10-06, 10-07 젯슨 작성 방식 · 계획서 AI리포트 v4 · 사용자 결정).
// 리포트는 매일 02:00(한국 시간)에 젯슨이 작성하고(GPU 서버 LLM 은 추론만), 소유자는 간격(1~30일)만 정한다.
// 간격이 1일보다 길면 하루치씩 나눠 회차를 따로 만든다(목록에 '1/3' 처럼 표시).
// 이번 범위 = 간격 설정 · 회차 목록(작성 중이면 진행 단계) · 실패 회차 재요청 · 결과 JSON 열기(들여쓰기한 원문 그대로)
//   · "받기" = 입력한 범위(최대 24시간)로 곧바로 작성(정기 일정 무영향, 목록에 '즉시').
// 개발 단계 확인용(개발자 모드 전용). 나중에 사용자가 받는 것은 서버가 템플릿으로 변환한 HTML(사용자 설명 2026-10-06).
// 리포트 내용(그래프 단계·결과 형식)은 팀 회의 뒤 결정 — 지금 그래프는 임시 1단계.

interface ReportsPanelProps {
  cubes: string[];
  canControl: boolean;
}

const STATUS_LABEL: Record<ReportStatus, string> = {
  pending: '대기',
  writing: '작성 중',
  done: '완료',
  failed: '실패',
};

const STATUS_BADGE: Record<ReportStatus, string> = {
  pending: 'badge',
  writing: 'badge badge-info',
  done: 'badge badge-ok',
  failed: 'badge badge-danger',
};

const RETRYABLE: ReportStatus[] = ['failed'];

function statusText(job: ReportJob): string {
  const base = STATUS_LABEL[job.status] ?? job.status;
  if (job.status === 'writing' && job.progress) return `${base} (${job.progress.step}/${job.progress.total} 단계)`;
  return base;
}

const KST_FMT = new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'Asia/Seoul',
  month: 'numeric',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

function fmt(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : KST_FMT.format(d);
}

// datetime-local 값(한국 시간으로 본다) ↔ 시각
function kstInput(ms: number): string {
  return new Date(ms + 9 * 3600e3).toISOString().slice(0, 16);
}

function fromKstInput(v: string): string {
  return `${v}:00+09:00`;
}

function note(job: ReportJob): string {
  if (job.status === 'done') return job.result_bytes != null ? `${Math.max(1, Math.round(job.result_bytes / 1024))} KB` : '';
  if (job.status === 'writing') return job.progress?.label ?? '';
  if (job.reason) return job.reason;
  const a = job.last_attempt;
  if (a && !a.ok) return a.error ?? '';
  return '';
}

const ACTIVE: ReportStatus[] = ['pending', 'writing'];

export const ReportsPanel: React.FC<ReportsPanelProps> = ({ cubes, canControl }) => {
  const [cube, setCube] = useState<string>(cubes[0] ?? '');
  const [settings, setSettings] = useState<ReportSettings | null>(null);
  const [reports, setReports] = useState<ReportJob[]>([]);
  const [draft, setDraft] = useState<string>('');
  const [message, setMessage] = useState<string>('');
  const [busy, setBusy] = useState<string | null>(null);
  const [opened, setOpened] = useState<{ job: ReportJob; text: string } | null>(null);
  const [nowStart, setNowStart] = useState<string>(() => kstInput(Date.now() - 86400e3));
  const [nowEnd, setNowEnd] = useState<string>(() => kstInput(Date.now()));

  useEffect(() => {
    if (!cube && cubes.length > 0) setCube(cubes[0]);
  }, [cube, cubes]);

  const load = useCallback(
    async (quiet = false) => {
      if (!cube) return;
      if (!quiet) setBusy('load');
      try {
        const [s, r] = await Promise.all([getReportSettings(cube), getReports(cube)]);
        setSettings(s);
        setReports(r.reports);
        setDraft((d) => (d === '' || !quiet ? String(s.interval_days) : d));
        if (!quiet) setMessage('');
      } catch (e) {
        setMessage(`불러오기 실패: ${e instanceof Error ? e.message : String(e)}`);
      } finally {
        if (!quiet) setBusy(null);
      }
    },
    [cube],
  );

  useEffect(() => {
    setSettings(null);
    setReports([]);
    setDraft('');
    setOpened(null);
    void load();
  }, [load]);

  // 대기·작성 중인 사람 요청(받기·재요청)이 있으면 5초마다, 아니면 30초마다 새로 고침
  const busyJobs = reports.some((j) => ACTIVE.includes(j.status) && (j.manual || j.status === 'writing'));
  useEffect(() => {
    const t = window.setInterval(() => void load(true), busyJobs ? 5000 : 30000);
    return () => window.clearInterval(t);
  }, [load, busyJobs]);

  const days = Number(draft);
  const validDays = Number.isInteger(days) && settings != null && days >= settings.min && days <= settings.max;
  const changed = settings != null && validDays && days !== settings.interval_days;

  const nowMaxH = settings?.now_max_hours ?? 24;
  const nowSpanMs = new Date(fromKstInput(nowEnd)).getTime() - new Date(fromKstInput(nowStart)).getTime();
  const nowRangeError = !nowStart || !nowEnd
    ? ''
    : !(nowSpanMs > 0)
      ? '시작 시각이 끝 시각보다 앞이어야 합니다.'
      : nowSpanMs > nowMaxH * 3600e3
        ? `범위는 최대 ${nowMaxH}시간입니다.`
        : '';

  const save = async () => {
    if (!changed) return;
    setBusy('save');
    try {
      const s = await putReportSettings(cube, days);
      setSettings(s);
      setDraft(String(s.interval_days));
      setMessage(`리포트 간격을 ${s.interval_days}일로 바꿨습니다.`);
    } catch (e) {
      setMessage(`저장 실패: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  };

  const open = async (job: ReportJob) => {
    setBusy(`open:${job.job_id}`);
    try {
      const r = await getReport(cube, job.job_id);
      if (r.result == null) {
        setMessage('이 회차에는 받은 JSON 이 없습니다.');
        return;
      }
      setOpened({ job, text: JSON.stringify(r.result, null, 2) });
    } catch (e) {
      setMessage(`열기 실패: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  };

  const requestNow = async () => {
    setBusy('now');
    try {
      const j = await postReportNow(cube, fromKstInput(nowStart), fromKstInput(nowEnd));
      setReports((list) => [j, ...list.filter((x) => x.job_id !== j.job_id)]);
      setMessage('작성 요청을 넣었습니다. 차례가 되면 곧바로 작성합니다.');
    } catch (e) {
      setMessage(`요청 실패: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  };

  const retry = async (job: ReportJob) => {
    setBusy(job.job_id);
    try {
      const j = await postReportRetry(cube, job.job_id);
      setReports((list) => list.map((x) => (x.job_id === j.job_id ? j : x)));
      setMessage(`${fmt(job.period_start)} ~ ${fmt(job.period_end)} 회차를 다시 작성하도록 넣었습니다.`);
    } catch (e) {
      setMessage(`재요청 실패: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-base font-bold text-ink-strong">AI 리포트</h2>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-ink-muted">큐브</span>
          <select value={cube} onChange={(e) => setCube(e.target.value)} aria-label="큐브">
            {cubes.length === 0 && <option value="">큐브 없음</option>}
            {cubes.map((sn) => (
              <option key={sn} value={sn}>
                {sn}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => void load()} disabled={busy !== null || !cube}>
          <RefreshCw className={`w-4 h-4 ${busy === 'load' ? 'animate-spin' : ''}`} aria-hidden="true" />
          <span>새로 고침</span>
        </button>
        {message && <span className="text-sm text-ink-muted">{message}</span>}
      </div>

      <section className="panel p-4">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <input
            type="datetime-local"
            value={nowStart}
            onChange={(e) => setNowStart(e.target.value)}
            disabled={!canControl || busy !== null}
            aria-label="시작 시각"
          />
          <span>~</span>
          <input
            type="datetime-local"
            value={nowEnd}
            onChange={(e) => setNowEnd(e.target.value)}
            disabled={!canControl || busy !== null}
            aria-label="끝 시각"
          />
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => void requestNow()}
            disabled={!canControl || !cube || !nowStart || !nowEnd || nowRangeError !== '' || busy !== null}
          >
            <Send className={`w-4 h-4 ${busy === 'now' ? 'animate-pulse' : ''}`} aria-hidden="true" />
            <span>받기</span>
          </button>
          {nowRangeError && <span className="text-danger">{nowRangeError}</span>}
        </div>
      </section>

      <section className="panel p-4">
        <h3 className="text-sm font-semibold text-ink-strong mb-3">리포트 간격</h3>
        {!settings ? (
          <p className="text-sm text-ink-muted">{cube ? '불러오는 중…' : '큐브가 없습니다.'}</p>
        ) : (
          <div className="flex flex-col gap-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <input
                type="number"
                inputMode="numeric"
                min={settings.min}
                max={settings.max}
                step={1}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                disabled={!canControl || busy !== null}
                aria-label="리포트 간격(일)"
                className="w-20"
              />
              <span>일</span>
              <button type="button" className="btn btn-primary btn-sm" onClick={() => void save()} disabled={!canControl || !changed || busy !== null}>
                저장
              </button>
              {!validDays && draft !== '' && (
                <span className="text-danger">
                  {settings.min}~{settings.max} 사이 정수로 입력하세요.
                </span>
              )}
            </div>
            <p className="text-ink-muted">
              리포트는 {settings.anchor_time}에 작성하며, 간격만큼의 데이터를 하루치씩 나눠 작성합니다.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <div>
                <span className="text-ink-muted">다음 리포트</span>
                <div>{fmt(settings.next_anchor)}</div>
              </div>
              <div>
                <span className="text-ink-muted">담길 데이터</span>
                <div>
                  {fmt(settings.next_period_start)} ~ {fmt(settings.next_anchor)}
                </div>
              </div>
            </div>
            {!settings.llm_ready && (
              <p className="text-danger">LLM 서버 주소·키가 설정되지 않아 리포트를 작성하지 못합니다.</p>
            )}
          </div>
        )}
      </section>

      <section className="panel p-4">
        <h3 className="text-sm font-semibold text-ink-strong mb-2">회차</h3>
        {reports.length === 0 ? (
          <p className="text-sm text-ink-muted">아직 작성된 회차가 없습니다.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm [&_th]:pr-4 [&_th]:pb-1 [&_th]:whitespace-nowrap [&_td]:pr-4 [&_td]:py-1 [&_tr>*:last-child]:pr-0">
              <thead>
                <tr className="text-ink-muted text-left">
                  <th>작성 시각</th>
                  <th>데이터 범위</th>
                  <th>상태</th>
                  <th>시도</th>
                  <th>비고</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {reports.map((job) => (
                  <tr key={job.job_id}>
                    <td className="whitespace-nowrap">
                      {fmt(job.anchor)}
                      {job.kind === 'now' && <span className="badge badge-info ml-1">즉시</span>}
                    </td>
                    <td className="whitespace-nowrap">
                      {fmt(job.period_start)} ~ {fmt(job.period_end)}
                      {job.part && job.part[1] > 1 && (
                        <span className="badge ml-1" title={`${job.part[1]}일 간격을 하루치씩 나눈 회차`}>
                          {job.part[0]}/{job.part[1]}
                        </span>
                      )}
                    </td>
                    <td className="whitespace-nowrap">
                      <span className={STATUS_BADGE[job.status] ?? 'badge'}>{statusText(job)}</span>
                    </td>
                    <td>{job.attempt_count}</td>
                    <td className="text-xs min-w-[14rem]">{note(job)}</td>
                    <td className="text-right whitespace-nowrap">
                      {job.status === 'done' && (
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() => void open(job)}
                          disabled={busy !== null}
                          aria-pressed={opened?.job.job_id === job.job_id}
                        >
                          <FileJson className="w-4 h-4" aria-hidden="true" />
                          <span>열기</span>
                        </button>
                      )}
                      {RETRYABLE.includes(job.status) && (
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() => void retry(job)}
                          disabled={!canControl || busy !== null}
                        >
                          <RotateCcw className={`w-4 h-4 ${busy === job.job_id ? 'animate-spin' : ''}`} aria-hidden="true" />
                          <span>재요청</span>
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {opened && (
        <section className="panel p-4" aria-label="결과 JSON">
          <div className="flex flex-wrap items-center gap-3 mb-2">
            <h3 className="text-sm font-semibold text-ink-strong">
              결과 JSON — {fmt(opened.job.period_start)} ~ {fmt(opened.job.period_end)} 회차
            </h3>
            <span className="text-xs text-ink-muted font-mono break-all">{opened.job.job_id}.json</span>
            <button type="button" className="btn btn-ghost btn-sm ml-auto" onClick={() => setOpened(null)}>
              <X className="w-4 h-4" aria-hidden="true" />
              <span>닫기</span>
            </button>
          </div>
          <pre className="text-xs font-mono whitespace-pre-wrap break-all overflow-auto max-h-[60vh] rounded border border-line bg-surface p-3">{opened.text}</pre>
        </section>
      )}
    </div>
  );
};
