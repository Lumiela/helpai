import React from 'react';
import { HistoryPoint } from '../lib/history';

interface ReadingChartProps {
  label: string;
  unit: string;
  points: HistoryPoint[]; // 시간순, 마지막 점 = 지금
  range?: { min: number; max: number }; // 허용 범위
  decimals: number;
  warn?: boolean;
  className?: string;
  fitHeight?: boolean;
}

const NICE_STEPS = [0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000];

function niceStep(span: number, target = 4): number {
  const raw = span / target;
  return NICE_STEPS.find((s) => s >= raw) ?? NICE_STEPS[NICE_STEPS.length - 1];
}

const VW = 1000;
const VH = 400;

// 최근 24시간 변화 곡선. 허용 범위는 하늘색 30% 띠로 깔린다.
// 곡선·띠·격자는 SVG(가로세로로 늘어남), 글자는 HTML로 그려 크기가 유지된다.
export const ReadingChart: React.FC<ReadingChartProps> = ({ label, unit, points, range, decimals, warn = false, className = '', fitHeight = false }) => {
  if (points.length < 2) return null;

  const values = points.map((p) => p.v);
  const dataMin = Math.min(...values);
  const dataMax = Math.max(...values);
  const lo = Math.min(dataMin, range?.min ?? dataMin);
  const hi = Math.max(dataMax, range?.max ?? dataMax);
  const step = niceStep(Math.max(hi - lo, 0.5));
  const yMin = Math.floor((lo - step * 0.3) / step) * step;
  const yMax = Math.ceil((hi + step * 0.3) / step) * step;
  const yTicks: number[] = [];
  for (let v = yMin; v <= yMax + 1e-9; v += step) yTicks.push(Math.round(v / step) * step);

  const t0 = points[0].t;
  const t1 = points[points.length - 1].t;
  const xOf = (t: number) => ((t - t0) / (t1 - t0)) * VW;
  const yOf = (v: number) => VH - ((v - yMin) / (yMax - yMin)) * VH;
  const fracY = (v: number) => 1 - (v - yMin) / (yMax - yMin);

  const linePath = `M ${points.map((p) => `${xOf(p.t).toFixed(1)} ${yOf(p.v).toFixed(1)}`).join(' L ')}`;
  const last = points[points.length - 1];
  const fmt = (v: number) => v.toFixed(decimals);

  // 4시간 간격 시각 눈금 (마지막은 "지금")
  const xTicks: Array<{ frac: number; text: string }> = [];
  for (let k = 0; k <= 6; k++) {
    const t = t0 + ((t1 - t0) * k) / 6;
    const date = new Date(t);
    const h = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
    xTicks.push({ frac: k / 6, text: k === 6 ? '지금' : h });
  }

  const lineColor = warn ? 'var(--color-heat)' : 'var(--color-ink-strong)';

  return (
    <div className={className}>
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 mb-2 shrink-0 text-xs text-ink-muted">
        <span>
          {label} 최근 24시간 · 장치 이력 · 5분 평균 + 현재값
        </span>
        <span className="flex flex-wrap items-center gap-x-3">
          {range && (
            <span className="whitespace-nowrap">
              <span className="inline-block w-3 h-2.5 align-middle mr-1 rounded-sm" style={{ background: 'var(--color-sky)', opacity: 0.5 }} />
              허용 범위 {fmt(range.min)}~{fmt(range.max)}
              {unit}
            </span>
          )}
          <span className="whitespace-nowrap">
            최저 {fmt(dataMin)}
            {unit}
          </span>
          <span className="whitespace-nowrap">
            최고 {fmt(dataMax)}
            {unit}
          </span>
        </span>
      </div>

      <div
        className={`@container relative ${fitHeight ? 'flex-1 min-h-28' : 'h-56 md:h-[340px]'}`}
        role="img"
        aria-label={`${label} 장치 이력, 최근 24시간 변화. 최저 ${fmt(dataMin)}${unit}, 최고 ${fmt(dataMax)}${unit}, 지금 ${fmt(last.v)}${unit}${
          range ? `. 허용 범위 ${fmt(range.min)}~${fmt(range.max)}${unit}` : ''
        }`}
      >
        {/* 그래프 영역: 왼쪽 44px 는 값 눈금, 아래 20px 는 시각 눈금 */}
        <div className="absolute top-2 right-3 bottom-5 left-11">
          <svg viewBox={`0 0 ${VW} ${VH}`} preserveAspectRatio="none" className="absolute inset-0 w-full h-full">
            {range && (
              <rect
                x="0"
                y={yOf(range.max)}
                width={VW}
                height={Math.max(0, yOf(range.min) - yOf(range.max))}
                fill="var(--color-sky)"
                fillOpacity="0.3"
              />
            )}
            {yTicks.map((v) => (
              <line key={`y-${v}`} x1="0" y1={yOf(v)} x2={VW} y2={yOf(v)} stroke="var(--color-line)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
            ))}
            {xTicks.map((tk) => (
              <line
                key={`x-${tk.frac}`}
                x1={tk.frac * VW}
                y1="0"
                x2={tk.frac * VW}
                y2={VH}
                stroke="var(--color-line)"
                strokeWidth="1"
                opacity="0.6"
                vectorEffect="non-scaling-stroke"
              />
            ))}
            <path d={linePath} fill="none" stroke={lineColor} strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
            <line x1="0" y1={VH} x2={VW} y2={VH} stroke="var(--color-line-strong)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
            <line x1="0" y1="0" x2="0" y2={VH} stroke="var(--color-line-strong)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
          </svg>

          {/* 현재값 점과 라벨 */}
          <span
            className="absolute w-3 h-3 rounded-full border-2 border-surface -translate-x-1/2 -translate-y-1/2"
            style={{ left: '100%', top: `${fracY(last.v) * 100}%`, background: lineColor }}
          />
          <span
            className="absolute -translate-x-full -translate-y-1/2 -ml-2 px-1.5 py-0.5 rounded text-xs font-semibold bg-surface/90 whitespace-nowrap"
            style={{ left: '100%', top: `${fracY(last.v) * 100}%`, color: lineColor }}
          >
            지금 {fmt(last.v)}
            {unit}
          </span>
        </div>

        {/* 값 눈금 (왼쪽) */}
        {yTicks.map((v) => (
          <span
            key={`yl-${v}`}
            className="absolute left-0 w-9 text-right text-[11px] leading-none text-ink-muted -translate-y-1/2"
            style={{ top: `calc(8px + (100% - 28px) * ${fracY(v)})` }}
          >
            {fmt(v)}
          </span>
        ))}

        {/* 시각 눈금 (아래) */}
        {xTicks.map((tk) => (
          <span
            key={`xl-${tk.frac}`}
            className={`absolute bottom-0 text-[11px] leading-none text-ink-muted whitespace-nowrap ${Math.round(tk.frac * 6) % 2 ? 'hidden @min-[400px]:inline' : ''} ${
              tk.frac === 0 ? '' : tk.frac === 1 ? '-translate-x-full' : '-translate-x-1/2'
            }`}
            style={{ left: `calc(44px + (100% - 56px) * ${tk.frac})` }}
          >
            {tk.text}
          </span>
        ))}
      </div>
    </div>
  );
};
