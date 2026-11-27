import React from 'react';
import { Fan, Lightbulb, Waves } from 'lucide-react';
import type { ActuatorState, ZoneConfig } from '../types/smartfarm';
import { relativeTime } from '../lib/deviceMap';

// 2026-11-23 기기 조작 카드: 웹 대시보드(smart-cube-web)의 기기 조작 화면처럼 장치마다 카드 한 장.
// 카드 위쪽에 장치가 보고한 지금 상태를 크게 보여 주고, 아래 큰 버튼으로 조작한다.
// 명령 결과(성공·거부·실패)는 App 이 장치 응답을 받은 뒤 알림으로 띄운다.

interface DeviceControlCardsProps {
  zone: ZoneConfig;
  actuator: ActuatorState;
  now: number;
  canControl: boolean;
  cropAssigned: boolean;
  lastLed: number;
  onUpdate: (updates: Partial<ActuatorState>) => void;
  onToggleAuto: () => void;
  onStartIrrigation: () => void;
}

const DeviceCard: React.FC<{ icon: React.ReactNode; title: string; state: string; active: boolean; children: React.ReactNode }> = ({
  icon,
  title,
  state,
  active,
  children,
}) => (
  <div className="bg-surface border border-line rounded-lg p-4 flex flex-col gap-3 h-full">
    <div className="flex items-center gap-2 text-sm font-semibold text-ink-strong">
      {icon}
      {title}
    </div>
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-xs text-ink-muted">장치 보고 상태</span>
      <strong className={`text-lg ${active ? 'text-ink-strong' : 'text-ink-muted'}`}>{state}</strong>
    </div>
    <div className="flex flex-1 flex-col justify-end gap-3">{children}</div>
  </div>
);

const FanSeg: React.FC<{ label: string; on: boolean; speed: number; onChange: (speed: number) => void }> = ({ label, on, speed, onChange }) => (
  <div className="flex items-center gap-2">
    <span className="w-12 shrink-0 text-xs text-ink-muted">{label}</span>
    <div className="seg flex-1 min-w-0" role="group" aria-label={`${label} 단수`}>
      <button type="button" className="flex-1 min-w-0 px-1" aria-pressed={!on} onClick={() => onChange(0)}>
        정지
      </button>
      {[1, 2, 3].map((n) => (
        <button key={n} type="button" className="flex-1 min-w-0 px-1" aria-pressed={on && speed === n} onClick={() => onChange(n)}>
          {n}단
        </button>
      ))}
    </div>
  </div>
);

export const DeviceControlCards: React.FC<DeviceControlCardsProps> = ({
  zone,
  actuator: a,
  now,
  canControl,
  cropAssigned,
  lastLed,
  onUpdate,
  onToggleAuto,
  onStartIrrigation,
}) => {
  const locked = !canControl || a.autoMode;
  const led = a.ledGrowLight ? a.ledIntensity : 0;
  const pumpMaxSeconds = zone.device?.pumpMaxMs ? Math.round(zone.device.pumpMaxMs / 1000) : null;
  const fanState = !a.outerFan && !a.innerFan
    ? '모두 정지'
    : `배기 ${a.outerFan ? `${a.outerFanSpeed}단` : '정지'} · 순환 ${a.innerFan ? `${a.innerFanSpeed}단` : '정지'}`;

  const caption = !canControl
    ? '조회 전용 · 소유자만 조작할 수 있습니다'
    : a.autoMode
      ? '자동 제어 중 · 서버가 작물 범위에 맞춰 조작합니다'
      : '조명·팬·급수를 직접 조작합니다';

  return (
    <section className="panel shrink-0 p-4 md:p-5 flex flex-col gap-4" aria-labelledby="device-control-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 id="device-control-title" className="text-base font-semibold text-ink-strong">기기 조작</h3>
          <p className="text-xs text-ink-muted mt-0.5">{caption}</p>
        </div>
        <div className="seg" role="group" aria-label="제어 모드">
          <button type="button" className="px-4" aria-pressed={!a.autoMode} disabled={!canControl}
            onClick={() => { if (a.autoMode) onToggleAuto(); }}>
            수동
          </button>
          <button type="button" className="px-4" aria-pressed={a.autoMode}
            disabled={!canControl || (!cropAssigned && !a.autoMode)}
            title={!cropAssigned && !a.autoMode ? '작물을 먼저 선택하세요' : undefined}
            onClick={() => { if (!a.autoMode) onToggleAuto(); }}>
            자동
          </button>
        </div>
      </div>

      {!zone.live && (
        <p className="text-xs text-heat" role="status">통신 끊김: 지금 보내는 명령은 장치에 닿지 않을 수 있습니다.</p>
      )}
      {canControl && a.autoMode && (
        <p className="text-xs text-ink-muted">직접 조작하려면 수동으로 바꾸세요.</p>
      )}

      <fieldset disabled={locked}
        className="grid grid-cols-1 md:grid-cols-3 gap-3 min-w-0 border-0 p-0 m-0 disabled:opacity-60 [&_button:disabled]:cursor-not-allowed [&_input:disabled]:cursor-not-allowed">
        <legend className="sr-only">장치별 조작</legend>

        <DeviceCard icon={<Lightbulb className="w-4 h-4" aria-hidden="true" />} title="재배 조명 (LED)"
          state={a.ledGrowLight ? `켜짐 · ${led}%` : '꺼짐'} active={a.ledGrowLight}>
          <label className="flex flex-col gap-1 text-xs text-ink-muted">
            <span className="flex justify-between">
              <span>밝기</span>
              <span className="font-semibold text-ink">{led}%</span>
            </span>
            <input type="range" min="0" max="100" step="5" value={led} aria-label="LED 밝기"
              onChange={(e) => {
                const value = parseInt(e.target.value, 10);
                onUpdate({ ledIntensity: value, ledGrowLight: value > 0 });
              }} />
          </label>
          <button type="button" className="btn btn-primary w-full"
            onClick={() => onUpdate(a.ledGrowLight ? { ledGrowLight: false, ledIntensity: 0 } : { ledGrowLight: true, ledIntensity: lastLed > 0 ? lastLed : 70 })}>
            {a.ledGrowLight ? '조명 끄기' : `조명 켜기 (${lastLed > 0 ? lastLed : 70}%)`}
          </button>
        </DeviceCard>

        <DeviceCard icon={<Fan className="w-4 h-4" aria-hidden="true" />} title="환기·순환팬"
          state={fanState} active={a.outerFan || a.innerFan}>
          <FanSeg label="배기팬" on={a.outerFan} speed={a.outerFanSpeed}
            onChange={(speed) => onUpdate({ outerFan: speed > 0, outerFanSpeed: speed })} />
          <FanSeg label="순환팬" on={a.innerFan} speed={a.innerFanSpeed}
            onChange={(speed) => onUpdate({ innerFan: speed > 0, innerFanSpeed: speed })} />
          <p className="text-xs text-ink-muted">1·2·3단 = 장치 출력 33·66·100%</p>
        </DeviceCard>

        <DeviceCard icon={<Waves className="w-4 h-4" aria-hidden="true" />} title="물주기 펌프"
          state={a.irrigationPump ? '급수 중' : '대기'} active={a.irrigationPump}>
          <p className="text-xs text-ink-muted">
            {pumpMaxSeconds ? `켜면 최대 ${pumpMaxSeconds}초 뒤 장치가 스스로 멈춥니다.` : '켜면 장치가 정한 최대 시간 뒤 스스로 멈춥니다.'}
          </p>
          <button type="button" className="btn btn-primary w-full"
            onClick={() => (a.irrigationPump ? onUpdate({ irrigationPump: false, nutrientValve: false }) : onStartIrrigation())}>
            {a.irrigationPump ? '급수 정지' : '급수 시작'}
          </button>
        </DeviceCard>
      </fieldset>

      <p className="text-xs text-ink-muted">
        장치가 보고한 값 기준 · 마지막 수신 {relativeTime(zone.lastSeen, now)} · 명령 결과는 장치 응답 후 알림으로 표시됩니다
      </p>
    </section>
  );
};
