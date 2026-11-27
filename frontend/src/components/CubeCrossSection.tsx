import React from 'react';
import { ZoneConfig, ActuatorState } from '../types/smartfarm';
import { fmt } from '../lib/deviceMap';

interface CubeCrossSectionProps {
  zone: ZoneConfig;
  actuator: ActuatorState;
  compact?: boolean;
  fitFrame?: boolean;
  className?: string;
}

const NOZZLES = [160, 195, 230, 265, 300, 335, 370, 405];
const PLANTS = [200, 240, 280, 320, 360, 400];

// 큐브 단면도. 색은 CSS 토큰을 그대로 읽어 화면 팔레트와 항상 일치한다.
// 작은 글자는 .cube-label 클래스로 묶어 좁은 화면에서는 숨긴다.
export const CubeCrossSection: React.FC<CubeCrossSectionProps> = ({
  zone,
  actuator,
  compact = false,
  fitFrame = false,
  className = '',
}) => {
  const c = {
    frame: 'var(--color-accent)',
    inner: 'var(--color-surface)',
    ink: 'var(--color-ink)',
    inkStrong: 'var(--color-ink-strong)',
    muted: 'var(--color-ink-muted)',
    line: 'var(--color-line-strong)',
    water: 'var(--color-water)',
    heat: 'var(--color-heat)',
    accent: 'var(--color-accent)',
    onDark: 'var(--color-ink-on-dark)',
  };
  const gradId = `led-glow-${zone.id}`;
  const showLabels = !compact;
  const onOff = (v: boolean) => (v ? '가동' : '정지');
  const label =
    `${zone.name} 단면도. 냉각 댐퍼 ${onOff(actuator.coolingDamper)}, 가온 댐퍼 ${onOff(actuator.heatingDamper)}, ` +
    `배기팬 ${onOff(actuator.outerFan)}, 순환팬 ${onOff(actuator.innerFan)}, 급수 ${actuator.irrigationPump ? '진행 중' : '대기'}, ` +
    `LED ${actuator.ledGrowLight ? `${actuator.ledIntensity}% 점등` : '소등'}`;

  return (
    <svg viewBox={fitFrame ? '82 22 436 356' : '0 0 600 400'} role="img" aria-label={label} className={className}>
      <defs>
        <linearGradient id={gradId} x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#f5d76e" stopOpacity="0.55" />
          <stop offset="100%" stopColor="#f5d76e" stopOpacity="0" />
        </linearGradient>
      </defs>

      {/* 프레임 */}
      <rect x="90" y="30" width="420" height="340" fill={c.frame} rx="6" />
      <rect x="100" y="40" width="400" height="320" fill={c.inner} rx="4" />
      <line x1="100" y1="90" x2="500" y2="90" stroke={c.frame} strokeWidth="4" />
      <line x1="100" y1="320" x2="500" y2="320" stroke={c.frame} strokeWidth="4" />
      <rect x="250" y="335" width="100" height="8" fill={c.frame} rx="4" />
      {showLabels && (
        <text className="cube-label" x="112" y="345" fill={c.muted} fontSize="11">
          {zone.code}
        </text>
      )}

      {/* 냉각 댐퍼 */}
      <g transform="translate(190, 90)">
        <rect x="-15" y="-10" width="30" height="15" fill={c.ink} />
        {showLabels && (
          <text className="cube-label" x="-12" y="15" fill={c.ink} fontSize="9">
            냉각
          </text>
        )}
        {actuator.coolingDamper &&
          [0, -8, 8].map((dx, i) => (
            <line
              key={dx}
              x1={dx}
              y1={i === 0 ? 10 : 15}
              x2={dx}
              y2={i === 0 ? 35 : 40}
              stroke={c.water}
              strokeWidth={i === 0 ? 3 : 2}
              strokeDasharray="4 2"
              className="flow"
            />
          ))}
      </g>

      {/* 가온 댐퍼 */}
      <g transform="translate(410, 90)">
        <rect x="-15" y="-10" width="30" height="15" fill={c.ink} />
        {showLabels && (
          <text className="cube-label" x="-12" y="15" fill={c.ink} fontSize="9">
            가온
          </text>
        )}
        {actuator.heatingDamper &&
          [0, -8, 8].map((dx, i) => (
            <line
              key={dx}
              x1={dx}
              y1={i === 0 ? 10 : 15}
              x2={dx}
              y2={i === 0 ? 35 : 40}
              stroke={c.heat}
              strokeWidth={i === 0 ? 3 : 2}
              strokeDasharray="4 2"
              className="flow"
            />
          ))}
      </g>

      {/* 배기팬 (오른쪽 벽) */}
      <g transform="translate(500, 140)">
        <rect x="-5" y="-15" width="10" height="30" fill={c.ink} />
        <path
          d="M -5 -10 L -15 10 M -15 -10 L -5 10"
          stroke={actuator.outerFan ? c.water : c.line}
          strokeWidth="2"
          className={actuator.outerFan ? 'spin' : ''}
        />
      </g>

      {/* 생육 카메라 */}
      <g transform="translate(460, 105) rotate(35)">
        <rect x="-10" y="-8" width="20" height="16" fill={c.ink} rx="2" />
        <circle cx="5" cy="0" r="4" fill={c.inner} stroke={c.line} strokeWidth="1" />
      </g>

      {/* 급수 라인 */}
      <path d="M 125 320 L 125 230 L 450 230" fill="none" stroke={c.water} strokeWidth="4" strokeLinejoin="round" />
      {NOZZLES.map((x) => (
        <rect key={`nozzle-${x}`} x={x - 3} y={230} width="6" height="8" fill={c.water} />
      ))}
      {actuator.irrigationPump &&
        NOZZLES.map((x) =>
          [245, 255, 265].map((y, i) => (
            <circle
              key={`drop-${x}-${y}`}
              cx={x}
              cy={y}
              r="2"
              fill={c.water}
              className="drop"
              style={{ animationDelay: `${i * 0.15 + (x % 2) * 0.1}s` }}
            />
          ))
        )}
      {actuator.irrigationPump && showLabels && (
        <text className="cube-label" x="300" y="190" fill={c.water} fontSize="12" textAnchor="middle" fontWeight="600">
          급수 중
        </text>
      )}

      {/* 온습도 센서, 광량 센서 */}
      <g transform="translate(490, 220)">
        <rect x="0" y="0" width="10" height="22" fill={c.ink} rx="2" />
        <circle cx="5" cy="16" r="2" fill={c.accent} />
        {showLabels && (
          <text className="cube-label" x="-38" y="15" fill={c.ink} fontSize="9">
            온습도
          </text>
        )}
      </g>
      <g transform="translate(490, 275)">
        <rect x="0" y="-6" width="12" height="12" fill={c.ink} />
        {showLabels && (
          <text className="cube-label" x="-24" y="4" fill={c.ink} fontSize="9">
            광량
          </text>
        )}
      </g>

      {/* 재배 트레이와 배지 */}
      <rect x="140" y="295" width="320" height="25" fill={c.ink} rx="2" />
      <rect x="145" y="298" width="310" height="12" fill="#6b4a2b" />
      {showLabels && (
        <text className="cube-label" x="300" y="313" fill={c.onDark} fontSize="10" textAnchor="middle">
          배지 수분 {fmt(zone.sensors.soilMoisture, 1)}%, EC {fmt(zone.sensors.ec, 2)} dS/m
        </text>
      )}
      {/* 배지 센서 */}
      <rect x="160" y="290" width="8" height="14" fill={c.ink} />

      {/* 순환팬 */}
      <g transform="translate(170, 280)">
        <circle cx="0" cy="0" r="14" fill={c.inner} stroke={c.ink} strokeWidth="2" />
        <path
          d="M -10 -10 L 10 10 M -10 10 L 10 -10"
          stroke={actuator.innerFan ? c.water : c.line}
          strokeWidth="2"
          className={actuator.innerFan ? 'spin' : ''}
        />
      </g>

      {/* 작물 */}
      {PLANTS.map((x) => (
        <g key={`plant-${x}`} transform={`translate(${x}, 248)`}>
          <path d="M 0 50 Q -5 25 -15 20 Q 0 20 0 0 Q 0 20 15 20 Q 5 25 0 50" fill={c.accent} />
          <path d="M 0 35 Q -10 25 -12 15 Q 0 25 0 35" fill={c.inkStrong} />
          <path d="M 0 40 Q 10 30 12 20 Q 0 30 0 40" fill={c.inkStrong} />
        </g>
      ))}

      {/* LED 조명 */}
      <g transform="translate(300, 90)">
        <rect x="-80" y="0" width="160" height="8" fill={c.line} rx="2" />
        {actuator.ledGrowLight && (
          <>
            <polygon points="-80,8 80,8 140,250 -140,250" fill={`url(#${gradId})`} opacity={actuator.ledIntensity / 100} />
            {[...Array(8)].map((_, i) => (
              <circle key={i} cx={-70 + i * 20} cy="4" r="2.5" fill="#f5d76e" />
            ))}
          </>
        )}
      </g>
    </svg>
  );
};
