import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Pause, Play } from 'lucide-react';
import { getHistory, getReport, getReports, type ReportJob } from '../lib/backend';
import { pointsFromRows, type HistoryPoint } from '../lib/history';
import { chartPaths, everyMs } from '../lib/chartPath';
import type { ChartMetric } from '../lib/extraMetrics';
import { reportSummary } from './data';

// 얇은 선 아이콘을 둥근 원 안에
export const IconCircle: React.FC<{ icon: React.ElementType; tone?: 'white' | 'lime' | 'soft' | 'warn'; size?: 'sm' | 'md' }> = ({ icon: Icon, tone = 'white', size = 'md' }) => (
  <span className={`a2-ic a2-ic-${tone} a2-ic-${size}`}><Icon aria-hidden="true" strokeWidth={1.6} /></span>
);

// ---------- 이력 선 그래프 (좋아하는 범위를 옅은 라임 띠로) ----------
export function useHistory(sn: string, metric: ChartMetric, start: string, every: string) {
  const [points, setPoints] = useState<HistoryPoint[] | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let alive = true;
    setPoints(null);
    setError(false);
    getHistory(sn, metric.field, start, every)
      .then((r) => { if (alive) setPoints(pointsFromRows(r.rows, metric.scale)); })
      .catch(() => { if (alive) setError(true); });
    return () => { alive = false; };
  }, [sn, metric.field, metric.scale, start, every]);
  return { points, error };
}

export const LineChart: React.FC<{ sn: string; metric: ChartMetric; start: string; every: string; height?: number }> = ({ sn, metric, start, every, height = 140 }) => {
  const { points, error } = useHistory(sn, metric, start, every);
  const [pick, setPick] = useState<number | null>(null);
  const W = 320, H = height;
  const shape = useMemo(() => {
    if (!points || points.length < 2) return null;
    const t0 = points[0].t, t1 = points[points.length - 1].t;
    const vs = points.map((p) => p.v);
    // 범위가 없는 항목(EC·CO₂)은 값만으로 축을 잡고 띠를 그리지 않는다
    const bounds = metric.range ? [metric.range.min, metric.range.max] : [];
    const lo = Math.min(...vs, ...bounds), hi = Math.max(...vs, ...bounds);
    const pad = (hi - lo) * 0.12 || 1;
    const X = (t: number) => 8 + ((t - t0) / (t1 - t0 || 1)) * (W - 16);
    const Y = (v: number) => H - 22 - ((v - (lo - pad)) / (hi - lo + pad * 2)) * (H - 34);
    const xy = points.map((p) => [X(p.t), Y(p.v)] as [number, number]);
    // 부드러운 곡선. 기록이 빈 구간에서는 끊는다(chartPath.ts)
    const { line: d } = chartPaths(xy, points.map((p) => p.t), everyMs(every), H - 22);
    return { d, xy, t0, t1, band: metric.range ? { y1: Y(metric.range.max), y2: Y(metric.range.min) } : null };
  }, [points, metric.range?.min, metric.range?.max, H, every]);

  if (error) return <p className="a2-chart-msg">기록을 불러오지 못했어요.</p>;
  if (!points) return <p className="a2-chart-msg">기록을 불러오는 중…</p>;
  if (!shape) return <p className="a2-chart-msg">아직 그래프로 보여 줄 기록이 없어요.</p>;
  const i = pick ?? shape.xy.length - 1;
  const fmtT = (t: number) => (start === '-24h'
    ? new Date(t).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false })
    : new Date(t).toLocaleDateString('ko-KR', { month: 'numeric', day: 'numeric' }));
  return (
    <div className="a2-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${metric.label} 변화 그래프. 마지막 값 ${points[points.length - 1].v.toFixed(metric.decimals)}${metric.unit}`}
        onPointerMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const x = ((e.clientX - r.left) / r.width) * W;
          let best = 0;
          shape.xy.forEach(([qx], j) => { if (Math.abs(qx - x) < Math.abs(shape.xy[best][0] - x)) best = j; });
          setPick(best);
        }}
        onPointerLeave={() => setPick(null)}>
        {shape.band && <rect x="0" y={shape.band.y1} width={W} height={Math.max(0, shape.band.y2 - shape.band.y1)} className="a2-chart-band" />}
        <path d={shape.d} className="a2-chart-line" strokeLinecap="round" />
        <circle cx={shape.xy[i][0]} cy={shape.xy[i][1]} r="5" className="a2-chart-dot" />
        <text x="8" y={H - 6} className="a2-chart-tick">{fmtT(shape.t0)}</text>
        <text x={W - 8} y={H - 6} textAnchor="end" className="a2-chart-tick">{fmtT(shape.t1)}</text>
      </svg>
      <p className="a2-chart-read">
        <b>{points[i].v.toFixed(metric.decimals)}{metric.unit}</b> · {fmtT(points[i].t)}{metric.range ? ` · 좋아하는 범위 ${metric.range.min}~${metric.range.max}${metric.unit}` : ''}
      </p>
    </div>
  );
};

// ---------- 오늘의 생육 리포트 (LLM 요약 + 읽어 주기) ----------
const kstToday = () => new Date(Date.now() + 9 * 3600_000).toISOString().slice(0, 10);

export function useTodayReport(sn: string) {
  const [state, setState] = useState<'loading' | 'none' | 'ok'>('loading');
  const [text, setText] = useState('');
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const list = await getReports(sn, 20);
        const job = list.reports.find((r: ReportJob) => r.status === 'done' && r.date === kstToday());
        if (!job) { if (alive) setState('none'); return; }
        const s = reportSummary((await getReport(sn, job.job_id)).result);
        if (!alive) return;
        if (s) { setText(s); setState('ok'); } else setState('none');
      } catch {
        if (alive) setState('none');
      }
    })();
    return () => { alive = false; };
  }, [sn]);
  return { state, text };
}

export const ReportCard: React.FC<{ sn: string; compact?: boolean }> = ({ sn, compact = false }) => {
  const { state, text } = useTodayReport(sn);
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const startedAt = useRef(0);
  const canSpeak = typeof window !== 'undefined' && 'speechSynthesis' in window;
  const seconds = Math.max(5, Math.round(text.length / 6)); // 한국어 읽기 속도(초당 약 6자)로 어림
  useEffect(() => () => { if (canSpeak) window.speechSynthesis.cancel(); }, [canSpeak]);
  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => setProgress(Math.min(1, (Date.now() - startedAt.current) / (seconds * 1000))), 200);
    return () => clearInterval(t);
  }, [playing, seconds]);
  const toggle = () => {
    if (!canSpeak || state !== 'ok') return;
    if (playing) { window.speechSynthesis.cancel(); setPlaying(false); return; }
    const u = new SpeechSynthesisUtterance(text);
    u.lang = 'ko-KR';
    u.onend = () => { setPlaying(false); setProgress(1); };
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
    startedAt.current = Date.now();
    setProgress(0);
    setPlaying(true);
  };
  const mmss = (s: number) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  return (
    <section className={`a2-report ${compact ? 'is-compact' : ''}`} aria-labelledby={`a2-report-${sn}`}>
      <div className="a2-row-between">
        <h3 id={`a2-report-${sn}`}>오늘의 생육 리포트</h3>
        <button type="button" className="a2-play" onClick={toggle} disabled={state !== 'ok' || !canSpeak} aria-label={playing ? '읽기 멈추기' : '리포트 읽어 주기'}>
          {playing ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}
        </button>
      </div>
      <p className="a2-report-text">{state === 'loading' ? '리포트를 확인하는 중…' : state === 'ok' ? text : '아직 오늘 리포트가 없어요'}</p>
      {!compact && (
        <>
          <div className="a2-wave" aria-hidden="true">
            {Array.from({ length: 30 }, (_, j) => <span key={j} className={j / 30 < progress ? 'is-done' : ''} style={{ height: `${30 + ((j * 37) % 70)}%` }} />)}
          </div>
          <p className="a2-report-meta">1x · {state === 'ok' ? `${mmss(Math.round(progress * seconds))} / 약 ${mmss(seconds)}` : '—'}</p>
        </>
      )}
    </section>
  );
};

// ---------- 확인 창 ----------
export const ConfirmSheet: React.FC<{ title: string; body: React.ReactNode; ok: string; danger?: boolean; onOk: () => void; onCancel: () => void }> = ({ title, body, ok, danger = false, onOk, onCancel }) => (
  <div className="a2-sheet-back" role="presentation" onClick={onCancel}>
    <div className="a2-sheet" role="alertdialog" aria-modal="true" aria-labelledby="a2-confirm-title" onClick={(e) => e.stopPropagation()}>
      <h3 id="a2-confirm-title">{title}</h3>
      <div className="a2-sheet-body">{body}</div>
      <div className="a2-sheet-btns">
        <button type="button" className="a2-btn-soft" onClick={onCancel}>취소</button>
        <button type="button" className={danger ? 'a2-btn-danger' : 'a2-btn-lime'} onClick={onOk}>{ok}</button>
      </div>
    </div>
  </div>
);
