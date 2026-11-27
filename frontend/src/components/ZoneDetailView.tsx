import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown, X } from 'lucide-react';
import { ZoneConfig, ActuatorState } from '../types/smartfarm';
import { EdgeNotice, AG, AGIcon, formatKoreanTime } from '../lib/agToolkit';
import { visibleHistory, currentValue, allowedRange, decimalsOf, pointsFromRows, ReadingKey, HistoryPoint } from '../lib/history';
import { getHistory } from '../lib/backend';
import { fmt, relativeTime } from '../lib/deviceMap';
import { CubeActivity } from './CubeActivity';
import { Dropdown } from './Dropdown';
import { PhotoperiodGraph, formatHour } from './PhotoperiodGraph';
import { ReadingChart } from './ReadingChart';
import { zoneState } from '../lib/zoneState';
import { rangeOf, rangeText } from '../lib/zoneRanges';
import type { CropProfile } from '../lib/backend';
import { activeTargetTemp, restoredLedIntensity } from '../lib/actuatorControl';
import { DamperControl } from './DamperControl';
import { ManualControlCards } from './ManualControlCards';
import { ControlModeSelect } from './ControlModeSelect';
import { LiveCameraView } from './LiveCameraView';
import { DeviceControlCards } from './DeviceControlCards';

interface ZoneDetailViewProps {
  zone: ZoneConfig;
  actuator: ActuatorState;
  zones: ZoneConfig[];
  onChangeZone: (zoneId: string) => void;
  onUpdateActuator: (zoneId: string, updates: Partial<ActuatorState>) => void;
  onUpdateTargetTemp: (zoneId: string, target: number) => void;
  devMode: boolean;
  onStartIrrigation: (zoneId: string, simulated: boolean) => void;
  now: number;
  // 2026-10-06 작물 기반 자동 제어: 큐브 이름 아래 작물 드롭다운
  crops: CropProfile[];
  canControl: boolean;
  onSetCrop: (zoneId: string, cropId: string | null) => void;
}

interface ReadingItem {
  key: ReadingKey;
  label: string;
  value: string;
  unit: string;
  note: string;
  warn: boolean;
  chartWarn?: boolean;
  rows: Array<[string, string]>;
  missing: boolean; // 실측 센서 없음
}

type CardView = 'cross' | 'camera' | 'chart';

// 백엔드(InfluxDB)에 이력이 있는 항목과 그 필드 이름. scale 은 저장 단위 → 화면 단위(조도: lx → klx, EC: µS/cm → dS/m).
// (2026-09-30: 온도·습도·조도 추가 / 2026-11-03: EC·CO2 추가 — RS485 토양 센서·HX-2000U)
const HISTORY_FIELD: Partial<Record<ReadingKey, { field: string; scale?: number }>> = {
  soil: { field: 'moisture_pct' },
  temp: { field: 'temperature' },
  humid: { field: 'humidity' },
  light: { field: 'lux', scale: 1 / 1000 },
  ec: { field: 'soil_ec', scale: 1 / 1000 },
  co2: { field: 'co2_ppm' },
};
const HISTORY_RANGE = '-24h';
const HISTORY_EVERY = '5m';
const HISTORY_REFRESH_MS = 60_000;

export function buildReadings(zone: ZoneConfig, a: ActuatorState): ReadingItem[] {
  const s = zoneState(zone);
  const sensors = zone.sensors;
  const moisture = zone.moisture;
  const moistureNote = moisture
    ? !moisture.adcOk ? '수분 ADC 응답 없음'
      : !moisture.calibrated ? `보정 전, 원시값 ${moisture.raw ?? '—'}`
        : `원시값 ${moisture.raw ?? '—'}${moisture.volts !== null ? `, ${moisture.volts.toFixed(3)} V` : ''}`
    : '센서 없음';
  return [
    {
      key: 'temp',
      label: '온도',
      value: fmt(sensors.temperature, 1),
      unit: '°C',
      note: sensors.temperature === null ? '센서 없음' : `엽온 ${fmt(sensors.leafTemp, 1)}°C, 목표 ${activeTargetTemp(zone, a).toFixed(1)}°C`,
      warn: s.tempWarn,
      missing: sensors.temperature === null,
      rows: [
        ['엽온', `${fmt(sensors.leafTemp, 1)}°C`],
        ['목표', `${activeTargetTemp(zone, a).toFixed(1)}°C`],
        ['허용 범위', rangeText(rangeOf(zone, 'temp'), '°C')],
      ],
    },
    {
      key: 'humid',
      label: '습도',
      value: fmt(sensors.humidity, 1),
      unit: '%',
      note: sensors.humidity === null ? '센서 없음' : `VPD ${fmt(sensors.vpd, 2)} kPa`,
      warn: s.humidWarn,
      missing: sensors.humidity === null,
      rows: [
        ['VPD', `${fmt(sensors.vpd, 2)} kPa`],
        ['허용 범위', rangeText(rangeOf(zone, 'humidity'), '%')],
      ],
    },
    {
      key: 'co2',
      label: 'CO₂',
      value: sensors.co2 === null ? '—' : String(Math.round(sensors.co2)),
      unit: 'ppm',
      note: sensors.co2 === null ? '센서 없음' : `범위 ${rangeText(rangeOf(zone, 'co2'), ' ppm')}`,
      warn: s.co2Warn,
      missing: sensors.co2 === null,
      rows: [
        ['허용 범위', rangeText(rangeOf(zone, 'co2'), ' ppm')],
        ['CO₂ 공급', a.co2Generator ? '공급 중' : '정지'],
      ],
    },
    {
      key: 'light',
      label: '광량',
      value: fmt(sensors.ledIntensityKlx, 1),
      unit: 'klx',
      note: sensors.ledIntensityKlx === null ? `센서 없음, ${a.ledGrowLight ? `LED ${a.ledIntensity}% 점등` : 'LED 소등'}` : a.ledGrowLight ? `LED ${a.ledIntensity}% 점등` : 'LED 소등',
      warn: false,
      missing: sensors.ledIntensityKlx === null,
      rows: [
        ['LED', a.ledGrowLight ? `${a.ledIntensity}% 점등` : '소등'],
        ['광 주기', `${formatHour(zone.photoperiod.on)}~${formatHour(zone.photoperiod.off)}`],
        ['목표 조도', rangeText(rangeOf(zone, 'lux'), ' klx', 1 / 1000)],
      ],
    },
    {
      key: 'ec',
      label: '양액 EC',
      value: fmt(sensors.ec, 2),
      unit: 'dS/m',
      note: sensors.ec === null && sensors.ph === null ? '센서 없음' : `pH ${fmt(sensors.ph, 1)}${s.phWarn ? ' (pH 범위 이탈)' : ''}, EC 범위 ${rangeText(rangeOf(zone, 'ec'), '')}`,
      warn: s.ecWarn || s.phWarn,
      chartWarn: s.ecWarn,
      missing: sensors.ec === null,
      rows: [
        ['pH', `${fmt(sensors.ph, 1)}${s.phWarn ? ' (pH 범위 이탈)' : ''}`],
        ['EC 허용 범위', rangeText(rangeOf(zone, 'ec'), ' dS/m')],
        ['pH 허용 범위', rangeText(rangeOf(zone, 'ph'), '')],
      ],
    },
    {
      key: 'soil',
      label: '배지 수분',
      value: fmt(sensors.soilMoisture, 1),
      unit: '%',
      note: moistureNote,
      warn: false,
      missing: sensors.soilMoisture === null,
      rows: [
        ['원시값', moisture?.raw !== null && moisture?.raw !== undefined ? String(moisture.raw) : '—'],
        ['보정', moisture ? (moisture.calibrated ? '완료' : '전') : '—'],
        ['허용 범위', rangeText(rangeOf(zone, 'soil'), '%')],
        ['배지 온도', `${fmt(sensors.soilTemp, 1)}°C`],
        ['급수', a.irrigationPump ? '급수 중' : '대기'],
      ],
    },
  ];
}

const ReadingPanel: React.FC<{ item: ReadingItem; pressed: boolean; onSelect: () => void }> = ({ item, pressed, onSelect }) => (
  <div
    role="button"
    tabIndex={0}
    aria-pressed={pressed}
    aria-label={`${item.label} ${item.value}${item.unit}, ${item.missing ? '센서 없음' : '24시간 변화 보기'}`}
    onClick={onSelect}
    onKeyDown={(e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        onSelect();
      }
    }}
    className={`panel reading-panel ${item.warn ? 'reading-warn' : ''}`}
  >
    <div className="reading-label">{item.label}</div>
    <div className={`reading-value ${item.missing ? 'text-ink-muted' : ''}`}>
      {item.value}
      <span className="reading-unit">{item.unit}</span>
    </div>
    <div className="reading-note">{item.warn ? `${item.note}, 범위 이탈` : item.note}</div>
  </div>
);

const ControlBox: React.FC<{ label: string; value: string; action?: React.ReactNode; className?: string; children: React.ReactNode }> = ({
  label,
  value,
  action,
  className = '',
  children,
}) => (
  <div className={`bg-surface border border-line rounded-lg p-3 flex flex-col gap-2 h-full ${className}`}>
    <div className="flex items-center justify-between gap-2 text-sm">
      <span className="text-ink-muted">{label}</span>
      <div className="flex items-center gap-2 shrink-0">
        {action}
        <span className="font-semibold text-ink-strong">{value}</span>
      </div>
    </div>
    {children}
  </div>
);

type HistoryState = 'idle' | 'loading' | 'ok' | 'error' | 'none';

export const ZoneDetailView: React.FC<ZoneDetailViewProps> = ({
  zone,
  actuator,
  zones,
  onChangeZone,
  onUpdateActuator,
  onUpdateTargetTemp,
  devMode,
  onStartIrrigation,
  now,
  crops,
  canControl,
  onSetCrop,
}) => {
  const lastLed = restoredLedIntensity(actuator);
  const [cardView, setCardView] = useState<CardView>('cross');
  const [selectedReading, setSelectedReading] = useState<ReadingKey | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [historyState, setHistoryState] = useState<HistoryState>('idle');
  const [historyError, setHistoryError] = useState<string>('');
  const [controlsOpen, setControlsOpen] = useState<boolean>(false);
  const irrigationNotice = useRef<HTMLElement | null>(null);
  const controlsTrigger = useRef<HTMLButtonElement | null>(null);
  const controlsClose = useRef<HTMLButtonElement | null>(null);

  useEffect(() => () => {
    irrigationNotice.current?.remove();
    irrigationNotice.current = null;
  }, [zone.id, devMode]);

  // 팝업을 열면 닫기 버튼으로, Esc로 닫으면 여는 버튼으로 초점을 이동한다.
  useEffect(() => {
    if (!controlsOpen) return;
    controlsClose.current?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setControlsOpen(false);
        requestAnimationFrame(() => controlsTrigger.current?.focus({ preventScroll: true }));
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [controlsOpen]);

  const s = zoneState(zone);
  const readings = buildReadings(zone, actuator);
  const current = selectedReading ? readings.find((r) => r.key === selectedReading) ?? null : null;

  // 큐브를 바꾸면 단면도부터 다시 보여준다.
  useEffect(() => {
    setCardView('cross');
    setSelectedReading(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zone.id]);

  // 이력: 백엔드에서 24시간(5분 평균)을 가져오고 1분마다 갱신한다. 실측 없는 항목은 이력이 없다.
  useEffect(() => {
    if (cardView !== 'chart' || !selectedReading) return;
    const source = HISTORY_FIELD[selectedReading];
    if (!source) {
      setHistory([]);
      setHistoryState('none');
      return;
    }
    let alive = true;
    const load = async () => {
      setHistoryState((state) => (state === 'ok' ? state : 'loading'));
      try {
        const response = await getHistory(zone.id, source.field, HISTORY_RANGE, HISTORY_EVERY);
        if (!alive) return;
        setHistory(pointsFromRows(response.rows, source.scale));
        setHistoryState('ok');
        setHistoryError('');
      } catch (error) {
        if (!alive) return;
        setHistoryState('error');
        setHistoryError(error instanceof Error ? error.message : String(error));
      }
    };
    void load();
    const timer = setInterval(() => void load(), HISTORY_REFRESH_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [cardView, selectedReading, zone.id]);

  const currentReadingValue = selectedReading ? currentValue(zone, selectedReading) : null;
  const chartPoints = selectedReading && currentReadingValue !== null
    ? visibleHistory(history, new Date(now), currentReadingValue)
    : history;

  const { on, off } = zone.photoperiod;

  const selectReading = (key: ReadingKey) => {
    if (cardView === 'chart' && selectedReading === key) {
      setCardView('cross');
      setSelectedReading(null);
      return;
    }
    setHistory([]);
    setHistoryState('idle');
    setSelectedReading(key);
    setCardView('chart');
  };

  const backToCross = () => {
    setCardView('cross');
    setSelectedReading(null);
  };

  // 자동 제어는 서버(api)가 한다(2026-10-06). 켜기·끄기는 App 이 서버에 요청한다. 작물이 없으면 켤 수 없다.
  const cropAssigned = Boolean(zone.cropId);
  const handleToggleAuto = () => {
    onUpdateActuator(zone.id, { autoMode: !actuator.autoMode });
  };

  const pumpMaxSeconds = zone.device?.pumpMaxMs ? Math.round(zone.device.pumpMaxMs / 1000) : null;

  const handleIrrigationOneShot = () => {
    irrigationNotice.current?.remove();
    const limitText = pumpMaxSeconds ? `장치 설정상 최대 ${pumpMaxSeconds}초 뒤 스스로 멈춥니다.` : '장치가 정한 최대 가동 시간 뒤 스스로 멈춥니다.';
    const markup = `
      <group-row class="background-white">
        <row-start class="background-indigo-light padding-4">
          <span class="ag-icon">${AGIcon.water}</span>
          <div class="text-bold">${zone.code} 즉시 급수</div>
          <span class="margin-left-auto text-time">${formatKoreanTime()}</span>
        </row-start>
        <div class="padding-6">
          <p class="message-body">급수 펌프를 켭니다. ${EdgeNotice.text(limitText)} 시작할까요?</p>
          ${devMode ? '<p class="message-body">개발자 도구: 5초 뒤 정지 명령을 함께 보내는 모의 급수로 동작을 확인할 수 있습니다.</p>' : ''}
          <row-between class="margin-top-4 justify-end flex-wrap gap-2">
            <button type="button" data-action="close" class="button button-small color-gray">취소</button>
            ${devMode ? '<button type="button" data-action="simulate-irrigation" class="button button-small color-indigo">5초 모의 급수</button>' : ''}
            <button type="button" data-action="start-irrigation" class="button button-small color-indigo">급수 시작</button>
          </row-between>
        </div>
      </group-row>
    `;

    irrigationNotice.current = AG.Notification.Message(
      markup,
      function () {
        const item = this;
        const dismiss = () => {
          item.classList.add('slide-out');
          setTimeout(() => item.remove(), 200);
        };
        item.querySelector('[data-action="start-irrigation"]')?.addEventListener(
          'click',
          () => {
            dismiss();
            onStartIrrigation(zone.id, false);
          },
          { once: true }
        );
        item.querySelector('[data-action="simulate-irrigation"]')?.addEventListener('click', () => {
          dismiss();
          onStartIrrigation(zone.id, true);
        }, { once: true });
        item.querySelector('[data-action="close"]')?.addEventListener('click', dismiss, { once: true });
      },
      0
    );
  };

  const setLed = (value: number) => {
    onUpdateActuator(zone.id, { ledIntensity: value, ledGrowLight: value > 0 });
  };

  const toggleLed = () => {
    if (actuator.ledGrowLight) {
      onUpdateActuator(zone.id, { ledGrowLight: false, ledIntensity: 0 });
    } else {
      const restore = lastLed > 0 ? lastLed : 70;
      onUpdateActuator(zone.id, { ledGrowLight: true, ledIntensity: restore });
    }
  };

  const cardTitle = cardView === 'camera' ? `${zone.code} 생육 카메라` : cardView === 'chart' && current ? `${current.label} 변화` : '큐브 단면';
  const outputsNote = zone.device?.outputsEnabled === false ? '펌웨어 출력 비활성: 제어 명령은 장치가 거부합니다' : null;

  const chartPlaceholder =
    historyState === 'none' ? '이 항목은 실측 센서가 없어 이력이 없습니다.'
      : historyState === 'loading' || historyState === 'idle' ? '장치 이력을 불러오는 중입니다.'
        : historyState === 'error' ? `이력을 불러오지 못했습니다: ${historyError}`
          : chartPoints.length < 2 ? '최근 24시간에 저장된 이력이 아직 없습니다.' : '';

  return (
    <div className="zone-detail-page flex flex-col gap-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Dropdown
            label="큐브 선택"
            role="listbox"
            trigger={({ open, toggle, buttonProps }) => (
              <button
                type="button"
                {...buttonProps}
                onClick={toggle}
                className="flex items-center gap-1 -ml-1 px-1 py-0.5 rounded-md hover:bg-inset"
                title="다른 큐브로 이동"
              >
                <h2 className="text-lg font-semibold text-ink-strong">{zone.name}</h2>
                <ChevronDown className={`w-5 h-5 text-ink-muted ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
              </button>
            )}
          >
            {(close) =>
              zones.map((z) => (
                <button
                  key={z.id}
                  type="button"
                  role="option"
                  aria-selected={z.id === zone.id}
                  className="menu-item"
                  onClick={() => {
                    onChangeZone(z.id);
                    close();
                  }}
                >
                  <span>
                    <span className="font-semibold">{z.code}</span> <span className="text-ink-muted">{z.crop}</span>
                  </span>
                  <span className={`badge ${z.live ? 'badge-ok' : 'badge-danger'}`}>{z.live ? '실시간' : '통신 끊김'}</span>
                </button>
              ))
            }
          </Dropdown>
          {/* 2026-10-06 사용자 지시: 이 줄이 작물 선택 드롭다운(생육 단계 문구 없음). 작물이 정해지면 자동 제어를 켤 수 있다 */}
          <div>
          <Dropdown
            label="작물 선택"
            role="listbox"
            trigger={({ open, toggle, buttonProps }) => (
              <button
                type="button"
                {...buttonProps}
                onClick={toggle}
                disabled={!canControl}
                className="flex items-center gap-1 -ml-1 px-1 py-0.5 rounded-md text-sm text-ink-muted hover:bg-inset disabled:cursor-default disabled:hover:bg-transparent"
                title={canControl ? '작물 선택' : '큐브 소유자만 바꿀 수 있습니다'}
              >
                <span>{zone.crop}</span>
                {canControl && <ChevronDown className={`w-4 h-4 ${open ? 'rotate-180' : ''}`} aria-hidden="true" />}
              </button>
            )}
          >
            {(close) => (
              <>
                <button type="button" role="option" aria-selected={!zone.cropId} className="menu-item"
                  onClick={() => { close(); if (zone.cropId) onSetCrop(zone.id, null); }}>
                  <span>작물 미설정</span>
                  {zone.cropId && <span className="text-ink-muted">자동 제어도 꺼짐</span>}
                </button>
                {crops.map((c) => (
                  <button key={c.id} type="button" role="option" aria-selected={c.id === zone.cropId} className="menu-item"
                    onClick={() => { close(); if (c.id !== zone.cropId) onSetCrop(zone.id, c.id); }}>
                    <span className="font-semibold">{c.name}</span>
                  </button>
                ))}
              </>
            )}
          </Dropdown>
          </div>
          <p className="text-xs text-ink-muted mt-1">
            {zone.device?.model ?? '모델 미확인'}
            {zone.device?.firmware ? `, 펌웨어 ${zone.device.firmware}` : ''}
            {zone.device?.ip ? `, ${zone.device.ip}` : ''}
            {zone.signalDbm !== null ? `, 신호 ${zone.signalDbm} dBm` : ''}
            {`, 수신 ${relativeTime(zone.lastSeen, now)}`}
          </p>
          {outputsNote && <p className="text-xs text-heat mt-1">{outputsNote}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`badge ${zone.live ? 'badge-ok' : 'badge-danger'}`}>{zone.live ? '실시간' : '통신 끊김'}</span>
          <span className={`badge ${s.warn ? 'badge-warn' : 'badge-ok'}`}>{s.warn ? '주의' : '정상'}</span>
          <button type="button" aria-pressed={actuator.autoMode} onClick={handleToggleAuto}
            disabled={!cropAssigned && !actuator.autoMode}
            title={!cropAssigned && !actuator.autoMode ? '작물을 먼저 선택하세요' : undefined}
            className="btn btn-secondary btn-sm md:btn disabled:opacity-50 disabled:cursor-not-allowed">
            자동 제어 {actuator.autoMode ? '켜짐' : '꺼짐'}
          </button>
          <button type="button" onClick={handleIrrigationOneShot} className="btn btn-primary btn-sm md:btn">
            즉시 급수
          </button>
        </div>
      </header>

      <div className="zone-detail-grid grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_16rem] xl:grid-cols-[minmax(0,1fr)_18rem] gap-5 md:items-stretch">
        <section className="panel relative min-w-0 p-4 md:p-5 md:flex md:flex-col" aria-labelledby="cross-section-title">
          <div className="relative z-10 flex h-8 shrink-0 items-center justify-between gap-3 mb-2 md:mb-3">
            <h3 id="cross-section-title" className="text-base font-semibold text-ink-strong">
              {cardTitle}
            </h3>
            {cardView === 'cross' ? (
              <ControlModeSelect zone={zone} actuator={actuator} onUpdate={onUpdateActuator} />
            ) : (
              <button type="button" onClick={backToCross} className="btn btn-ghost btn-sm">
                단면도로 돌아가기
              </button>
            )}
          </div>

          <div className="relative h-72 min-w-0 md:h-auto md:min-h-0 md:flex-1">
          {cardView === 'camera' && (
            <div className="absolute inset-0 rounded-md overflow-hidden flex items-center justify-center p-2 text-center">
              {/* 2026-09-29 오후: 실시간 스트림(MJPEG). 열면 시작, 닫으면 정지 */}
              <LiveCameraView cubeSn={zone.id} devMode={devMode} />
            </div>
          )}

          {cardView === 'chart' && current && (
            <div className="absolute inset-0 flex min-h-0 flex-col overflow-y-auto">
              {/* 현재값과 부속값: 그래프 위에 함께 보여 준다 */}
              <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2 mb-3 shrink-0">
                <div className={`flex items-center gap-3 ${(current.chartWarn ?? current.warn) ? 'reading-warn' : ''}`}>
                  <div className={`reading-value ${current.missing ? 'text-ink-muted' : ''}`}>
                    {current.value}
                    <span className="reading-unit">{current.unit}</span>
                  </div>
                  {(current.chartWarn ?? current.warn) && <span className="badge badge-warn">허용 범위 이탈</span>}
                </div>
                <dl className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
                  {current.rows.map(([k, v]) => (
                    <div key={k} className="whitespace-nowrap">
                      <dt className="inline text-ink-muted">{k} </dt>
                      <dd className="inline font-semibold text-ink">{v}</dd>
                    </div>
                  ))}
                </dl>
              </div>
              {chartPlaceholder ? (
                <p className="flex-1 flex items-center justify-center text-sm text-ink-muted text-center px-4" role="status">{chartPlaceholder}</p>
              ) : (
                <ReadingChart
                  className="flex min-h-0 flex-1 flex-col"
                  fitHeight
                  label={current.label}
                  unit={current.unit}
                  points={chartPoints}
                  range={allowedRange(zone, current.key)}
                  decimals={decimalsOf(current.key)}
                  warn={current.chartWarn ?? current.warn}
                />
              )}
            </div>
          )}

          {cardView === 'cross' && (
            <div className="cube-detail-activity absolute inset-0">
              <CubeActivity actuator={actuator} live={zone.live} sensors={zone.sensors}
                plantLabel={zone.cropId ? `${zone.crop}${zone.growthStage ? ` · ${zone.growthStage}` : ''}` : '작물 미설정'} />
            </div>
          )}
          </div>

          <div className="mt-3 md:mt-4 flex shrink-0 flex-wrap items-center gap-x-6 gap-y-2 text-sm">
            <div>
              <span className="text-ink-muted">냉각 댐퍼 </span>
              <span className="font-semibold">{actuator.coolingDamper ? '가동' : '정지'}</span>
            </div>
            <div>
              <span className="text-ink-muted">가온 댐퍼 </span>
              <span className="font-semibold">{actuator.heatingDamper ? '가동' : '정지'}</span>
            </div>
            <button
              type="button"
              aria-pressed={cardView === 'camera'}
              onClick={() => setCardView((v) => (v === 'camera' ? 'cross' : 'camera'))}
              className="btn btn-secondary btn-sm ml-auto"
            >
              {cardView === 'camera' ? '단면도 보기' : '생육 카메라 보기'}
            </button>
          </div>
        </section>

        {/* 좁은 화면: 항목 타일을 누르면 위 카드에 현재값과 24시간 변화가 나온다 */}
        <div className="order-3 md:hidden">
          <div className="grid grid-cols-3 gap-2" role="group" aria-label="생육 환경 항목">
            {readings.map((r) => (
              <button
                key={r.key}
                type="button"
                className={`tile ${r.warn ? 'is-warn' : ''}`}
                aria-pressed={cardView === 'chart' && selectedReading === r.key}
                onClick={() => selectReading(r.key)}
              >
                <span className="tile-label">{r.label}</span>
                <span className={`tile-value ${r.missing ? 'text-ink-muted' : ''}`}>
                  {r.value}
                  <span className="tile-unit">{r.unit}</span>
                </span>
              </button>
            ))}
          </div>
        </div>

        <aside className="contents md:relative md:flex md:min-w-0 md:flex-col md:gap-3">
          <div className="detail-readings hidden md:grid md:gap-2" role="group" aria-label="생육 환경 항목" inert={controlsOpen}>
            {readings.map((r) => (
              <ReadingPanel key={r.key} item={r} pressed={cardView === 'chart' && selectedReading === r.key} onSelect={() => selectReading(r.key)} />
            ))}
          </div>

        {/* 데스크톱은 상태 카드 열 위에 세로 목록, 모바일은 한 장씩 넘기는 하단 팝업. */}
        <section
          id="control-sheet"
          className={`panel min-w-0 p-3 space-y-1 md:space-y-3 fixed inset-x-2 bottom-2 z-50 max-h-[55dvh] overflow-y-auto shadow-lg md:absolute md:inset-x-0 md:bottom-12 md:max-h-[min(44rem,calc(100dvh-8rem),calc(100%-3rem))] ${controlsOpen ? '' : 'hidden'}`}
          aria-labelledby="control-title"
        >
          <div className="sticky top-0 z-10 flex items-center justify-between gap-3 bg-panel pb-1">
            <h3 id="control-title" className="text-base font-semibold text-ink-strong">
              목표값·광주기 설정
            </h3>
            <button ref={controlsClose} type="button" aria-label="수동 제어 닫기" onClick={() => {
              setControlsOpen(false);
              requestAnimationFrame(() => controlsTrigger.current?.focus({ preventScroll: true }));
            }} className="btn btn-ghost btn-sm px-2">
              <X className="w-4 h-4" aria-hidden="true" />
            </button>
          </div>
          {outputsNote && <p className="text-xs text-heat">{outputsNote}</p>}

          <ManualControlCards zoneId={zone.id} disabled={actuator.autoMode} labels={['주간 온도', 'LED·광주기']}>
            <div className="bg-surface border border-line rounded-lg p-3 flex flex-col gap-2 h-full">
              <div className="flex justify-between items-center text-sm">
                <label htmlFor="target-temp" className="text-ink-muted">
                  목표 주간 온도
                </label>
                <span className="font-semibold text-ink-strong">{zone.targetTemp.currentTarget.toFixed(1)} °C</span>
              </div>
              <input
                id="target-temp"
                type="range"
                min="15"
                max="32"
                step="0.5"
                value={zone.targetTemp.currentTarget}
                onChange={(e) => onUpdateTargetTemp(zone.id, parseFloat(e.target.value))}
                className="w-full"
              />
              <div className="flex justify-between text-xs text-ink-muted">
                <span>15.0</span>
                <span>32.0</span>
              </div>
              <DamperControl actuator={actuator} showAutoToggle={false} onUpdate={(updates) => onUpdateActuator(zone.id, updates)} />
            </div>

            <ControlBox label="LED 밝기" value={`${actuator.ledGrowLight ? actuator.ledIntensity : 0}%`}
              action={
                <button type="button" className="btn btn-secondary btn-sm h-7 px-2" onClick={toggleLed}>
                  {actuator.ledGrowLight ? '소등' : `점등 (${lastLed}%)`}
                </button>
              }>
              <div className="flex items-start gap-3 min-w-0 pt-5">
                <div className="relative flex flex-col items-center w-8 shrink-0 text-xs text-ink-muted">
                  <span className="absolute bottom-full mb-1">100%</span>
                  <input
                    type="range"
                    min="0"
                    max="100"
                    step="5"
                    value={actuator.ledGrowLight ? actuator.ledIntensity : 0}
                    onChange={(e) => setLed(parseInt(e.target.value, 10))}
                    aria-label="LED 밝기"
                    aria-orientation="vertical"
                    className="h-16 w-6 touch-none"
                    style={{ writingMode: 'vertical-lr', direction: 'rtl' }}
                  />
                  <span className="absolute top-full mt-1">0%</span>
                </div>
                <div className="flex-1 min-w-0">
                  <PhotoperiodGraph
                    on={on}
                    off={off}
                    now={new Date(now)}
                    ledLevel={actuator.ledGrowLight ? actuator.ledIntensity : 0}
                    automatic={actuator.autoMode}
                    scheduledLevel={lastLed}
                    crop={zone.crop}
                    lightSaturation={zone.lightSaturation}
                    className="w-full"
                  />
                </div>
              </div>
            </ControlBox>
          </ManualControlCards>
        </section>

          <button
            ref={controlsTrigger}
            type="button"
            aria-expanded={controlsOpen}
            aria-controls="control-sheet"
            onClick={() => setControlsOpen((open) => !open)}
            className={`fixed bottom-4 right-4 z-40 btn btn-primary shadow-lg md:static md:w-full md:shadow-none ${controlsOpen ? 'hidden md:inline-flex' : ''}`}
          >
            목표값·광주기 설정
          </button>
        </aside>
      </div>

      {/* 2026-11-23: 조명·팬·급수 조작은 장치별 카드로 (웹 대시보드 기기 조작 화면 디자인) */}
      <DeviceControlCards
        zone={zone}
        actuator={actuator}
        now={now}
        canControl={canControl}
        cropAssigned={cropAssigned}
        lastLed={lastLed}
        onUpdate={(updates) => onUpdateActuator(zone.id, updates)}
        onToggleAuto={handleToggleAuto}
        onStartIrrigation={handleIrrigationOneShot}
      />
    </div>
  );
};
