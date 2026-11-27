import React from 'react';
import { Sun, Moon } from 'lucide-react';
import type { ZoneConfig } from '../types/smartfarm';
import { daylightFactor, saturationPpfdAt } from '../lib/lightSaturation';

interface PhotoperiodGraphProps {
  on: number; // 주간 시작 시각 (0~24)
  off: number; // 주간 종료 시각 (0~24)
  now: Date;
  ledLevel: number; // 점등 시 LED 밝기 (%)
  crop: string;
  automatic?: boolean;
  scheduledLevel?: number;
  lightSaturation: ZoneConfig['lightSaturation'];
  className?: string;
}

export function formatHour(h: number): string {
  const total = Math.round(h * 60) % (24 * 60);
  const hh = Math.floor(total / 60);
  const mm = total % 60;
  return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
}

const W = 480;
const H = 160;
const TOP = 12;
const BOTTOM = 150;
const TICKS = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24];

// 24시간 일주기 그래프 (압축형: 다른 제어 카드와 같은 높이에 맞춘다).
// 띠·격자·곡선은 SVG(가로로 늘어남)로, 아이콘·현재 시각 표시선·눈금·범례는 HTML로 그려 화면 폭과 무관하게 같은 크기로 보인다.
export const PhotoperiodGraph: React.FC<PhotoperiodGraphProps> = ({ on, off, now, ledLevel, crop, lightSaturation, automatic = true, scheduledLevel = ledLevel, className = '' }) => {
  const px = W / 24;
  const x = (h: number) => h * px;
  const hourNow = now.getHours() + now.getMinutes() / 60;
  const pct = (hourNow / 24) * 100;

  const daySegments: Array<[number, number]> = on < off ? [[on, off]] : [[on, 24], [0, off]];
  const nightSegments: Array<[number, number]> =
    on < off ? ([[0, on], [off, 24]].filter(([a, b]) => b - a > 0) as Array<[number, number]>) : [[off, on]];

  // 자동은 주간 설정 밝기와 점등 일정, 수동은 현재 밝기를 표시한다.
  const ledY = (h: number) => BOTTOM - (automatic ? daylightFactor(on, off, h, 0) * scheduledLevel : ledLevel) / 100 * (BOTTOM - TOP) * 0.85;
  // PPFD와 LED 출력 %는 독립 척도다. 조명 출력 보정값이 없으므로 단위 변환하지 않는다.
  const ppfdScale = Math.max(1200, lightSaturation.peakPpfd);
  const saturationAt = (h: number) => saturationPpfdAt(lightSaturation, { on, off }, h);
  const saturationY = (h: number) => BOTTOM - saturationAt(h) / ppfdScale * (BOTTOM - TOP) * 0.85;

  const pathOf = (yOf: (h: number) => number) => {
    const pts: string[] = [];
    for (let h = 0; h <= 24; h += 0.25) pts.push(`${x(h).toFixed(1)} ${yOf(h).toFixed(1)}`);
    return `M ${pts.join(' L ')}`;
  };
  const ledPath = pathOf(ledY);
  const saturationPath = pathOf(saturationY);

  const ariaLabel =
    `${crop} 광주기 그래프. LED ${ledLevel}%, 광포화점 최대 ${lightSaturation.peakPpfd} µmol/m²/s. ` +
    `LED 출력은 0~100%, 광포화점은 0~${ppfdScale} PPFD의 독립 척도입니다. ` +
    `현재 시각 ${formatHour(hourNow)}은 세로선으로 표시합니다.`;

  return (
    <div className={`@container ${className}`} role="img" aria-label={ariaLabel}>
      {/* 그래프 본체 */}
      <div className="relative h-16" aria-hidden="true">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="absolute inset-0 w-full h-full rounded-sm">
          <defs>
            <linearGradient id="pp-day" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--color-heat-soft)" />
              <stop offset="100%" stopColor="var(--color-surface)" />
            </linearGradient>
            <linearGradient id="pp-night" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--color-water-soft)" />
              <stop offset="100%" stopColor="var(--color-water)" stopOpacity="0.35" />
            </linearGradient>
          </defs>

          {/* 밤·낮 띠 */}
          <rect x="0" y="0" width={W} height={H} fill="url(#pp-night)" />
          {daySegments.map(([a, b]) => (
            <rect key={`day-${a}`} x={x(a)} y="0" width={x(b) - x(a)} height={H} fill="url(#pp-day)" />
          ))}

          {/* 격자 */}
          {TICKS.map((t) => (
            <line key={`v-${t}`} x1={x(t)} y1="0" x2={x(t)} y2={H} stroke="var(--color-line)" strokeWidth="1" opacity="0.7" vectorEffect="non-scaling-stroke" />
          ))}
          {[0.5].map((f) => (
            <line key={`h-${f}`} x1="0" y1={H * f} x2={W} y2={H * f} stroke="var(--color-line)" strokeWidth="1" opacity="0.7" vectorEffect="non-scaling-stroke" />
          ))}
          {/* 주간 경계선 */}
          {daySegments.map(([a, b]) => (
            <g key={`edge-${a}`}>
              {a > 0 && <line x1={x(a)} y1="0" x2={x(a)} y2={H} stroke="var(--color-water)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />}
              {b < 24 && <line x1={x(b)} y1="0" x2={x(b)} y2={H} stroke="var(--color-water)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />}
            </g>
          ))}

          {/* 곡선 */}
          <path d={saturationPath} fill="none" stroke="var(--color-water)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
          <path d={ledPath} fill="none" stroke="var(--color-heat)" strokeWidth="2" vectorEffect="non-scaling-stroke" />

          {/* 축 */}
          <line x1="0" y1={H} x2={W} y2={H} stroke="var(--color-line-strong)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
        </svg>

        {/* 해·달 아이콘 (띠 가운데 위쪽) */}
        {daySegments.map(([a, b]) => (
          <Sun key={`sun-${a}`} className="absolute top-1 w-4 h-4 text-heat -translate-x-1/2" style={{ left: `${((a + b) / 2 / 24) * 100}%` }} />
        ))}
        {nightSegments.map(([a, b]) => (
          <Moon key={`moon-${a}`} className="absolute top-1 w-4 h-4 text-water -translate-x-1/2" style={{ left: `${((a + b) / 2 / 24) * 100}%` }} />
        ))}

        {/* 현재 시각 표시선과 곡선 위 현재값 */}
        <div className="absolute top-0 bottom-0 w-0.5 bg-ink-strong -translate-x-1/2" style={{ left: `${pct}%` }} />
        <span
          className="absolute w-2.5 h-2.5 rounded-full bg-heat border-2 border-surface -translate-x-1/2 -translate-y-1/2"
          style={{ left: `${pct}%`, top: `${(ledY(hourNow) / H) * 100}%` }}
        />
        <span
          className="absolute w-2.5 h-2.5 rounded-full bg-water border-2 border-surface -translate-x-1/2 -translate-y-1/2"
          style={{ left: `${pct}%`, top: `${(saturationY(hourNow) / H) * 100}%` }}
        />
      </div>

      {/* 시각 눈금 */}
      <div className="relative h-3.5 mt-0.5 text-[11px] leading-none text-ink-muted" aria-hidden="true">
        {TICKS.map((t) => (
          <span
            key={t}
            className={`absolute whitespace-nowrap ${t === 0 ? '' : t === 24 ? '-translate-x-full' : '-translate-x-1/2'} ${
              t % 8 === 0 ? '' : t % 4 === 0 ? 'hidden @min-[320px]:inline' : 'hidden @min-[480px]:inline'
            }`}
            style={{ left: `${(t / 24) * 100}%` }}
          >
            {t}시
          </span>
        ))}
      </div>

      {/* 두 곡선은 단위가 다르므로 각 척도를 범례에 명시한다. */}
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] leading-tight text-ink-muted" aria-hidden="true">
        <span title="조명 출력 0~100%. 광포화점 PPFD와 별도 척도입니다.">
          <span className="inline-block w-2 h-2 rounded-full bg-heat align-middle mr-1" />
          {automatic ? '자동 LED' : '수동 LED'} 현재 {ledLevel}%
        </span>
        <span title={`${crop} 광포화점. PPFD 척도 0~${ppfdScale} µmol/m²/s.`}>
          <span className="inline-block w-2 h-2 rounded-full bg-water align-middle mr-1" />
          광포화점 {Math.round(saturationAt(hourNow))} PPFD
        </span>
      </div>
    </div>
  );
};
