import React from 'react';
import { ArrowRight, ChevronRight, RefreshCw } from 'lucide-react';
import { ZoneConfig, ActuatorState } from '../types/smartfarm';
import { CubeCrossSection } from './CubeCrossSection';
import { zoneState } from '../lib/zoneState';
import { activeTargetTemp } from '../lib/actuatorControl';
import { DEFAULT_ACTUATOR, fmt, relativeTime } from '../lib/deviceMap';
import { rangeOf, rangeText } from '../lib/zoneRanges';
import type { BackendState } from '../App';

interface GreenhouseOverviewProps {
  zones: ZoneConfig[];
  actuators: Record<string, ActuatorState>;
  onSelectZone: (zoneId: string) => void;
  backend: BackendState;
  onRefresh: () => void;
  now: number;
  onOpenDevices?: () => void; // 큐브가 없을 때 '장치 연결' 페이지로 (2026-11-18)
}

// 실측이 없는 항목은 값 '—', 설명 '센서 없음' 으로 보인다.
const Reading: React.FC<{ label: string; value: string; unit: string; note: string; warn?: boolean; missing?: boolean }> = ({
  label,
  value,
  unit,
  note,
  warn = false,
  missing = false,
}) => (
  <div className={warn ? 'reading-warn' : ''}>
    <div className="reading-label">{label}</div>
    <div className={`reading-value ${missing ? 'text-ink-muted' : ''}`}>
      {value}
      <span className="reading-unit">{unit}</span>
    </div>
    <div className="reading-note">{missing ? '센서 없음' : warn ? `${note}, 범위 이탈` : note}</div>
  </div>
);

const Stat: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div>
    <span className="text-ink-muted">{label} </span>
    <span className="font-semibold text-ink">{value}</span>
  </div>
);

// 2026-09-29 오후: 백엔드 연결 상태는 상단 헤더(Header)로 옮겼다. 이 컴포넌트는 헤더가 쓴다.
export const BackendStatusLine: React.FC<{ backend: BackendState; cubeCount: number; onRefresh: () => void }> = ({ backend, cubeCount, onRefresh }) => {
  const text =
    backend.state === 'connecting' ? '백엔드에 연결하는 중'
      : backend.state === 'ok' ? `백엔드 연결됨, 큐브 ${cubeCount}대`
        : `백엔드 연결 안 됨: ${backend.message}`;
  const cls = backend.state === 'ok' ? 'badge-ok' : backend.state === 'error' ? 'badge-danger' : 'badge-info';
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className={`badge ${cls}`} role="status">{text}</span>
      <button type="button" onClick={onRefresh} className="btn btn-ghost btn-sm" aria-label="큐브 목록 다시 불러오기">
        <RefreshCw className="w-4 h-4" aria-hidden="true" />
      </button>
    </div>
  );
};

export const GreenhouseOverview: React.FC<GreenhouseOverviewProps> = ({ zones, actuators, onSelectZone, backend, onRefresh, now, onOpenDevices }) => {
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-ink-strong">Home</h2>
        </div>
      </div>

      {zones.length === 0 &&
        (backend.state === 'error' ? (
          <p className="panel p-6 text-sm text-ink-muted">백엔드에 연결되지 않아 큐브를 보여줄 수 없습니다.</p>
        ) : (
          // 2026-11-18 사용자 지시: 큐브가 없으면 추가 안내 + '장치 연결' 페이지로 가는 버튼
          <div className="panel p-6 flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-ink-muted">등록된 큐브가 없습니다. 장치를 추가해주세요.</p>
            {onOpenDevices && (
              <button type="button" className="btn btn-primary btn-sm" onClick={onOpenDevices}>
                <span>장치 연결</span>
                <ArrowRight className="w-4 h-4" aria-hidden="true" />
              </button>
            )}
          </div>
        ))}

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
        {zones.map((zone) => {
          const a = actuators[zone.id] ?? DEFAULT_ACTUATOR;
          const s = zoneState(zone);
          const stateText = s.warn ? '주의' : '정상';
          const sensors = zone.sensors;
          const open = () => onSelectZone(zone.id);
          const seenText = relativeTime(zone.lastSeen, now);
          return (
            <article
              key={zone.id}
              role="button"
              tabIndex={0}
              aria-label={`${zone.name} 상세 보기. ${zone.crop}, ${zone.live ? '실시간' : '통신 끊김'}, 상태 ${stateText}, 배지 수분 ${fmt(sensors.soilMoisture, 1)}퍼센트`}
              onClick={open}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  open();
                }
              }}
              className="panel flex flex-col cursor-pointer hover:border-line-strong hover:bg-panel/80"
            >
              <header className="px-5 pt-4 pb-2 flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-base font-semibold text-ink-strong flex items-center gap-1">
                    {zone.name}
                    <ChevronRight className="w-4 h-4 text-ink-muted" aria-hidden="true" />
                  </h3>
                  <p className="text-sm text-ink-muted">
                    {zone.crop}, {zone.growthStage}
                  </p>
                </div>
                <div className="flex flex-col items-end gap-1">
                  <span className={`badge ${zone.live ? 'badge-ok' : 'badge-danger'}`}>{zone.live ? '실시간' : '통신 끊김'}</span>
                  {a.autoMode && <span className="badge badge-info">자동 제어</span>}
                  <span className="text-xs text-ink-muted whitespace-nowrap">수신 {seenText}</span>
                </div>
              </header>

              <div className="px-5">
                <CubeCrossSection zone={zone} actuator={a} compact className="w-full h-44" />
              </div>

              <div className="px-5 pt-3 grid grid-cols-2 gap-x-6 gap-y-4">
                <Reading
                  label="온도"
                  value={fmt(sensors.temperature, 1)}
                  unit="°C"
                  note={`목표 ${activeTargetTemp(zone, a).toFixed(1)}°C`}
                  warn={s.tempWarn}
                  missing={sensors.temperature === null}
                />
                <Reading
                  label="습도"
                  value={fmt(sensors.humidity, 1)}
                  unit="%"
                  note={`범위 ${rangeText(rangeOf(zone, 'humidity'), '%')}`}
                  warn={s.humidWarn}
                  missing={sensors.humidity === null}
                />
                <Reading
                  label="CO₂"
                  value={sensors.co2 === null ? '—' : String(Math.round(sensors.co2))}
                  unit="ppm"
                  note={`범위 ${rangeText(rangeOf(zone, 'co2'), ' ppm')}`}
                  warn={s.co2Warn}
                  missing={sensors.co2 === null}
                />
                <Reading
                  label="배지 수분"
                  value={fmt(sensors.soilMoisture, 1)}
                  unit="%"
                  note={zone.moisture?.raw !== null && zone.moisture?.raw !== undefined ? `원시값 ${zone.moisture.raw}${zone.moisture.calibrated ? '' : ', 보정 전'}` : '실측'}
                  missing={sensors.soilMoisture === null && !(zone.moisture && !zone.moisture.calibrated)}
                />
              </div>

              <dl className="kv px-5 pt-4">
                <dt>양액 EC / pH</dt>
                <dd className={s.ecWarn || s.phWarn ? 'text-heat' : 'text-ink-muted'}>
                  {fmt(sensors.ec, 2)} dS/m / {fmt(sensors.ph, 1)}{(s.ecWarn || s.phWarn) && ' (범위 이탈)'}
                </dd>
                <dt>광량 / LED</dt>
                <dd>
                  <span className="text-ink-muted">{fmt(sensors.ledIntensityKlx, 1)} klx</span> / {a.ledGrowLight ? `${a.ledIntensity}% 점등` : '소등'}
                </dd>
                <dt>무선 신호</dt>
                <dd>{zone.signalDbm === null ? '—' : `${zone.signalDbm} dBm`}</dd>
              </dl>

              <div className="mx-5 mt-4 mb-4 pt-3 border-t border-line grid grid-cols-3 gap-2 text-sm">
                <Stat label="배기팬" value={a.outerFan ? `${a.outerFanSpeed}단` : '정지'} />
                <Stat label="순환팬" value={a.innerFan ? `${a.innerFanSpeed}단` : '정지'} />
                <Stat label="급수" value={a.irrigationPump ? '급수 중' : '대기'} />
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
};
