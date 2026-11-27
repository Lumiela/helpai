import React, { useEffect, useState } from 'react';
import {
  ArrowLeft, ArrowUpRight, Camera, ChevronDown, Droplets, Fan, Leaf, Lightbulb, Sun, Thermometer, Waves, Wind, Box, WifiOff, Loader2,
} from 'lucide-react';
import type { ActuatorState, ValueRange, ZoneConfig } from '../types/smartfarm';
import { getHistory, type CropProfile } from '../lib/backend';
import { pointsFromRows, type HistoryPoint } from '../lib/history';
import { rangeOf } from '../lib/zoneRanges';
import { fmt, relativeTime, DEFAULT_ACTUATOR } from '../lib/deviceMap';
import { CubeActivity, type CubeDevice } from '../components/CubeActivity';
import { LiveCameraView } from '../components/LiveCameraView';
import { ReadingChart } from '../components/ReadingChart';
import { POTS, potPhotoStyle, type Pot } from './pots';
import './mobile.css';

// 2026-11-24 모바일 앱 프로토타입: 교육생·도시 초보 재배자용 "내 식물" 앱.
//   홈(화분 고르기 + 지금 큐브에서 일어나는 일 + 대표 환경값) → 화분 상세(환경 · 큐브 동작 · 카메라) → 직접 조절.
//   측정·제어는 큐브 단위다. 화분은 살펴볼 식물을 고르는 탐색 기능이며 이름·사진은 데모.
//   표시와 애니메이션은 장치가 보고한 상태(reported)를 따른다. 명령 뒤 보고가 바뀔 때까지 "장치 응답을 기다리는 중".

type Level = 'low' | 'ok' | 'high' | 'none';
type EnvKey = 'temp' | 'light' | 'soil' | 'air';
type DetailTab = 'env' | 'motion' | 'camera';

const GENERAL: Record<EnvKey, ValueRange> = { temp: { min: 18, max: 28 }, light: { min: 5, max: 20 }, soil: { min: 35, max: 70 }, air: { min: 40, max: 70 } };
const RESPONSE_TIMEOUT_MS = 10_000; // 장치 응답을 기다리는 시간 (업무 규칙 B_R_113: 10초 안에 응답 없으면 응답 없음)

interface EnvItem {
  key: EnvKey;
  label: string; // 일상 표현
  sensor: string; // 상세에서 보여 줄 센서 이름
  icon: React.ElementType;
  value: number | null;
  unit: string;
  decimals: number;
  range: ValueRange;
  general: boolean;
  history: { field: string; scale?: number };
}

const levelOf = (v: number | null, r: ValueRange): Level => (v === null ? 'none' : v < r.min ? 'low' : v > r.max ? 'high' : 'ok');
const LEVEL_WORD: Record<Level, string> = { low: '낮아요', ok: '알맞아요', high: '높아요', none: '센서 없음' };

function envItems(zone: ZoneConfig): EnvItem[] {
  const pick = (key: 'temp' | 'soil' | 'humidity' | 'lux', general: ValueRange, scale = 1): [ValueRange, boolean] => {
    const r = rangeOf(zone, key);
    return r ? [{ min: Math.round(r.min * scale * 10) / 10, max: Math.round(r.max * scale * 10) / 10 }, false] : [general, true];
  };
  const [t, tg] = pick('temp', GENERAL.temp);
  const [l, lg] = pick('lux', GENERAL.light, 1 / 1000);
  const [s, sg] = pick('soil', GENERAL.soil);
  const [h, hg] = pick('humidity', GENERAL.air);
  return [
    { key: 'temp', label: '온도', sensor: '온습도 센서', icon: Thermometer, value: zone.sensors.temperature, unit: '°C', decimals: 1, range: t, general: tg, history: { field: 'temperature' } },
    { key: 'light', label: '빛의 밝기', sensor: '조도 센서 (단위 klx = 1,000 lx)', icon: Sun, value: zone.sensors.ledIntensityKlx, unit: 'klx', decimals: 1, range: l, general: lg, history: { field: 'lux', scale: 1 / 1000 } },
    { key: 'soil', label: '흙 수분', sensor: '흙 수분 센서 (정전용량식)', icon: Droplets, value: zone.sensors.soilMoisture, unit: '%', decimals: 0, range: s, general: sg, history: { field: 'moisture_pct' } },
    { key: 'air', label: '공기 촉촉함', sensor: '온습도 센서 (상대습도)', icon: Waves, value: zone.sensors.humidity, unit: '%', decimals: 0, range: h, general: hg, history: { field: 'humidity' } },
  ];
}

// ---------- 장치 ----------
const FAN_WORDS = ['끄기', '약하게', '보통', '세게'];
const LED_STEPS = [0, 25, 50, 75, 100];

interface DeviceInfo {
  key: CubeDevice;
  label: string;
  icon: React.ElementType;
  role: string; // 역할 한 줄
}
const DEVICES: DeviceInfo[] = [
  { key: 'exhaust', label: '배기팬', icon: Fan, role: '큐브 안의 공기를 밖으로 내보내요. 덥거나 습할 때 도움이 돼요.' },
  { key: 'circulation', label: '순환팬', icon: Wind, role: '큐브 안에서 공기를 골고루 돌려요. 줄기가 튼튼해지고 곰팡이를 막아요.' },
  { key: 'led', label: '조명', icon: Lightbulb, role: '햇빛 대신 식물에게 빛을 비춰요. 분홍빛은 빨간빛과 파란빛을 섞은 색이에요.' },
  { key: 'water', label: '물 주기', icon: Droplets, role: '아래 물통의 물을 화분 밑 트레이로 올려 줘요. 뿌리가 아래에서 물을 빨아올려요.' },
];

// 장치가 보고한 상태로 지금 값을 읽는다 (조명 밝기 %, 팬 단계, 급수 여부)
function reportedValue(a: ActuatorState, key: CubeDevice): number | boolean {
  if (key === 'led') return a.ledGrowLight ? a.ledIntensity : 0;
  if (key === 'exhaust') return a.outerFan ? a.outerFanSpeed : 0;
  if (key === 'circulation') return a.innerFan ? a.innerFanSpeed : 0;
  return a.irrigationPump;
}

function deviceStateText(a: ActuatorState, key: CubeDevice, live: boolean): string {
  if (!live) return '상태 확인 불가';
  if (key === 'led') return a.ledGrowLight && a.ledIntensity > 0 ? `켜짐 · 밝기 ${a.ledIntensity}%` : '꺼짐';
  if (key === 'exhaust') return a.outerFan && a.outerFanSpeed > 0 ? `공기를 밖으로 내보내는 중 · ${FAN_WORDS[a.outerFanSpeed]}` : '쉬는 중';
  if (key === 'circulation') return a.innerFan && a.innerFanSpeed > 0 ? `안에서 바람을 돌리는 중 · ${FAN_WORDS[a.innerFanSpeed]}` : '쉬는 중';
  return a.irrigationPump ? '지금 물을 주고 있어요' : '기다리는 중';
}

function isActive(a: ActuatorState, key: CubeDevice): boolean {
  const v = reportedValue(a, key);
  return typeof v === 'boolean' ? v : v > 0;
}

// 홈의 한 문장: 지금 큐브에서 일어나는 일
function nowSentence(a: ActuatorState, live: boolean): string {
  if (!live) return '상태 확인 불가 · 큐브와 연결되지 않았어요';
  if (a.irrigationPump) return '지금 물을 주고 있어요';
  const led = a.ledGrowLight && a.ledIntensity > 0;
  const fan = (a.innerFan && a.innerFanSpeed > 0) || (a.outerFan && a.outerFanSpeed > 0);
  if (led && fan) return `조명을 켜고(밝기 ${a.ledIntensity}%) 바람을 돌리고 있어요`;
  if (led) return `조명만 켜져 있어요 (밝기 ${a.ledIntensity}%)`;
  if (fan) return '조명은 꺼져 있고 바람만 돌고 있어요';
  return '지금은 큐브가 쉬고 있어요';
}

// ---------- 공용 조각 ----------
const PotPhoto: React.FC<{ pot: Pot; className?: string; wide?: boolean }> = ({ pot, className = '', wide = false }) => (
  <div className={`m-pot-photo ${className}`} style={potPhotoStyle(pot, wide)} role="img" aria-label={`${pot.name} 사진(데모)`} />
);

const StatusPill: React.FC<{ live: boolean; lastSeen: number | null; now: number }> = ({ live, lastSeen, now }) => (
  <span className={`m-pill ${live ? 'is-live' : 'is-off'}`}>
    {live ? <span className="m-dot" aria-hidden="true" /> : <WifiOff className="m-ic-xs" aria-hidden="true" />}
    {live ? `연결됨 · ${relativeTime(lastSeen, now)}` : '상태 확인 불가'}
  </span>
);

// ---------- 앱 ----------
export interface MobileAppProps {
  zones: ZoneConfig[];
  reported: Record<string, ActuatorState>;
  canControl: boolean;
  crops: CropProfile[];
  now: number;
  devMode: boolean;
  dashboardHref: string;
  entryHref: string;
  onUpdate: (sn: string, updates: Partial<ActuatorState>) => void;
  onStartIrrigation: (sn: string) => void;
  onSetCrop: (sn: string, cropId: string | null) => void;
}

type PendingMap = Partial<Record<CubeDevice, { target: number | boolean; at: number }>>;

export const MobileApp: React.FC<MobileAppProps> = (props) => {
  const { zones, reported, now } = props;
  const zone = zones[0] ?? null; // 지금 프로토타입은 첫 큐브 하나를 보여 준다
  const [potId, setPotId] = useState(POTS[0].id);
  const [screen, setScreen] = useState<'home' | 'detail'>('home');
  const [tab, setTab] = useState<DetailTab>('env');
  const [pending, setPending] = useState<PendingMap>({});
  const pot = POTS.find((p) => p.id === potId) ?? POTS[0];
  const a = zone ? reported[zone.id] ?? DEFAULT_ACTUATOR : DEFAULT_ACTUATOR;

  // 화면을 바꾸면 맨 위부터 보여 준다
  useEffect(() => { window.scrollTo(0, 0); }, [screen]);

  // 장치가 목표 상태를 보고하면 기다림 표시를 지운다
  useEffect(() => {
    setPending((prev) => {
      let changed = false;
      const next: PendingMap = { ...prev };
      for (const key of Object.keys(prev) as CubeDevice[]) {
        const p = prev[key];
        if (p && reportedValue(a, key) === p.target) {
          delete next[key];
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [a]);

  if (!zone) {
    return (
      <div className="m-root"><div className="m-app"><p className="m-empty">연결된 큐브가 없어요. 큐브 전원과 와이파이를 확인해 주세요.</p></div></div>
    );
  }

  const send = (key: CubeDevice, target: number | boolean, updates: Partial<ActuatorState>) => {
    setPending((prev) => ({ ...prev, [key]: { target, at: Date.now() } }));
    props.onUpdate(zone.id, updates);
  };
  const pendingState = (key: CubeDevice): 'waiting' | 'noresponse' | null => {
    const p = pending[key];
    if (!p) return null;
    return Date.now() - p.at < RESPONSE_TIMEOUT_MS ? 'waiting' : 'noresponse';
  };
  const anyWaiting = (Object.keys(pending) as CubeDevice[]).some((k) => pendingState(k) === 'waiting');

  const common = { zone, a, now, pot, pendingState, anyWaiting };
  return (
    <div className="m-root">
      <div className="m-app">
        {screen === 'home'
          ? <Home {...common} pots={POTS} onPickPot={setPotId} links={{ dashboard: props.dashboardHref, entry: props.entryHref }}
            onOpen={(t) => { setTab(t); setScreen('detail'); }} />
          : <Detail {...common} tab={tab} onTab={setTab} onBack={() => setScreen('home')}
            canControl={props.canControl} crops={props.crops} devMode={props.devMode}
            onSend={send} onStartIrrigation={() => props.onStartIrrigation(zone.id)}
            onWaterRequested={() => setPending((prev) => ({ ...prev, water: { target: true, at: Date.now() } }))}
            onSetCrop={(cropId) => props.onSetCrop(zone.id, cropId)} />}
      </div>
    </div>
  );
};

interface CommonProps {
  zone: ZoneConfig;
  a: ActuatorState;
  now: number;
  pot: Pot;
  pendingState: (key: CubeDevice) => 'waiting' | 'noresponse' | null;
  anyWaiting: boolean;
}

// ---------- 홈: 내 식물 ----------
const Home: React.FC<CommonProps & { pots: Pot[]; onPickPot: (id: string) => void; onOpen: (tab: DetailTab) => void; links: { dashboard: string; entry: string } }> = ({
  zone, a, now, pot, pots, onPickPot, onOpen, anyWaiting, links,
}) => {
  const items = envItems(zone).filter((i) => i.key !== 'air'); // 홈은 온도·빛의 밝기·흙 수분만
  const cropName = zone.cropId ? zone.crop : null;
  return (
    <>
      <header className="m-top">
        <div className="min-w-0">
          <p className="m-eyebrow">{zone.name}</p>
          <h1 className="m-title">내 식물</h1>
        </div>
        <StatusPill live={zone.live} lastSeen={zone.lastSeen} now={now} />
      </header>

      {/* 선택한 화분: 사진 · 이름 · 지금 큐브에서 일어나는 일 */}
      <section className="m-hero" aria-label="선택한 화분">
        <PotPhoto pot={pot} className="m-hero-photo" wide />
        <span className="m-demo m-hero-demo">데모 사진</span>
        <div className="m-hero-body">
          <p className="m-hero-name">{pot.name}<span>{cropName ?? '작물 미설정'}</span></p>
          <p className="m-hero-note">{pot.note}</p>
          <div className={`m-now ${zone.live ? '' : 'is-off'}`} role="status">
            {zone.live ? <span className={`m-now-dot ${isActive(a, 'water') ? 'is-water' : ''}`} aria-hidden="true" /> : <WifiOff className="m-ic-sm" aria-hidden="true" />}
            <span>
              <small>지금 큐브에서는</small>
              <b>{nowSentence(a, zone.live)}</b>
              {anyWaiting && <em className="m-waiting"><Loader2 className="m-ic-xs m-spin" aria-hidden="true" />장치 응답을 기다리는 중</em>}
            </span>
          </div>
          <button type="button" className="m-btn-primary" onClick={() => onOpen('motion')}>
            큐브 동작 보기 <ArrowUpRight className="m-ic-sm" aria-hidden="true" />
          </button>
        </div>
      </section>

      {/* 화분 고르기 (참고 2: 사진과 이름 카드, 선택한 카드는 진하게) */}
      <section aria-labelledby="m-pots-title">
        <div className="m-sec-head">
          <h2 id="m-pots-title">화분 고르기</h2>
          <span className="m-demo">데모 화분</span>
        </div>
        <div className="m-pots" role="listbox" aria-label="화분 목록">
          {pots.map((p) => {
            const on = p.id === pot.id;
            return (
              <button key={p.id} type="button" role="option" aria-selected={on} className={`m-pot ${on ? 'is-on' : ''}`} onClick={() => onPickPot(p.id)}>
                <span className="m-pot-text">
                  <b>{p.name}</b>
                  <small>{p.note}</small>
                  <span className="m-pot-check">{on ? '보는 중' : '보기'}</span>
                </span>
                <PotPhoto pot={p} className="m-pot-thumb" />
                <span className="m-pot-arrow" aria-hidden="true"><ArrowUpRight /></span>
              </button>
            );
          })}
        </div>
        <p className="m-note">센서와 장치는 큐브 하나에 한 벌이라 모든 화분이 같은 환경을 나눠 써요.</p>
      </section>

      {/* 대표 환경값: 온도 · 빛의 밝기 · 흙 수분 */}
      <section aria-labelledby="m-env-title">
        <div className="m-sec-head">
          <h2 id="m-env-title">큐브 환경</h2>
          <span className="m-tag">큐브 공통 환경</span>
        </div>
        <div className="m-env3">
          {items.map((i) => {
            const lv = zone.live ? levelOf(i.value, i.range) : 'none';
            const Icon = i.icon;
            return (
              <button key={i.key} type="button" className={`m-env-tile lv-${lv}`} onClick={() => onOpen('env')}>
                <Icon className="m-ic" aria-hidden="true" />
                <small>{i.label}</small>
                <b>{zone.live && i.value !== null ? fmt(i.value, i.decimals) : '—'}<span>{i.unit}</span></b>
                <em>{zone.live ? LEVEL_WORD[lv] : '확인 불가'}</em>
              </button>
            );
          })}
        </div>
        <button type="button" className="m-link" onClick={() => onOpen('env')}>환경 자세히 보기 · 변화 그래프</button>
      </section>

      <footer className="m-foot">
        <a href={links.dashboard}>기존 대시보드로</a>
        <a href={links.entry}>화면 고르기</a>
      </footer>
    </>
  );
};

// ---------- 화분 상세 ----------
const TABS: Array<{ key: DetailTab; label: string; icon: React.ElementType }> = [
  { key: 'env', label: '환경', icon: Leaf },
  { key: 'motion', label: '큐브 동작', icon: Box },
  { key: 'camera', label: '카메라', icon: Camera },
];

const Detail: React.FC<CommonProps & {
  tab: DetailTab; onTab: (t: DetailTab) => void; onBack: () => void;
  canControl: boolean; crops: CropProfile[]; devMode: boolean;
  onSend: (key: CubeDevice, target: number | boolean, updates: Partial<ActuatorState>) => void;
  onStartIrrigation: () => void; onWaterRequested: () => void; onSetCrop: (cropId: string | null) => void;
}> = (p) => {
  const { zone, pot, now, tab, onTab, onBack } = p;
  return (
    <>
      {/* 선택한 화분은 상단에 유지 */}
      <header className="m-detail-top">
        <button type="button" className="m-icon-btn" onClick={onBack} aria-label="내 식물로 돌아가기"><ArrowLeft /></button>
        <PotPhoto pot={pot} className="m-detail-photo" />
        <div className="min-w-0 flex-1">
          <p className="m-detail-name">{pot.name} <span className="m-demo">데모</span></p>
          <p className="m-detail-sub">{zone.cropId ? zone.crop : '작물 미설정'} · {zone.code}</p>
        </div>
        <StatusPill live={zone.live} lastSeen={zone.lastSeen} now={now} />
      </header>

      {/* 참고 3: 둥근 아이콘 버튼 중 고른 것은 크고 밝은 녹색 + 위쪽 표시 */}
      <nav className="m-tabs" role="tablist" aria-label="보고 싶은 항목">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} className={`m-tab ${tab === key ? 'is-on' : ''}`} onClick={() => onTab(key)}>
            <span className="m-tab-caret" aria-hidden="true" />
            <span className="m-tab-circle"><Icon aria-hidden="true" /></span>
            <span className="m-tab-label">{label}</span>
          </button>
        ))}
      </nav>

      {tab === 'env' && <EnvTab {...p} />}
      {tab === 'motion' && <MotionTab {...p} />}
      {tab === 'camera' && (
        <section className="m-card">
          <div className="m-sec-head"><h2>생육 카메라</h2><span className="m-tag">큐브 전체</span></div>
          <p className="m-note">천장 가운데 카메라가 큐브 안 화분 전체를 비춰요. 화분마다 따로 찍지는 않아요.</p>
          <div className="m-camera"><LiveCameraView cubeSn={zone.id} devMode={p.devMode} /></div>
        </section>
      )}
    </>
  );
};

// 환경: 현재값 + 변화 그래프. 단위·센서·기기 정보는 펼쳐서 본다.
const EnvTab: React.FC<CommonProps & { crops: CropProfile[]; canControl: boolean; onSetCrop: (cropId: string | null) => void }> = ({ zone, now, crops, canControl, onSetCrop }) => {
  const items = envItems(zone);
  const [sel, setSel] = useState<EnvKey>('temp');
  const item = items.find((i) => i.key === sel) ?? items[0];
  const [points, setPoints] = useState<HistoryPoint[]>([]);
  const [state, setState] = useState<'loading' | 'ok' | 'error'>('loading');

  useEffect(() => {
    let alive = true;
    setState('loading');
    setPoints([]);
    getHistory(zone.id, item.history.field, '-24h', '5m')
      .then((r) => { if (alive) { setPoints(pointsFromRows(r.rows, item.history.scale)); setState('ok'); } })
      .catch(() => { if (alive) setState('error'); });
    return () => { alive = false; };
  }, [zone.id, item.history.field, item.history.scale]);

  return (
    <>
      <section className="m-card">
        <div className="m-sec-head"><h2>지금 환경</h2><span className="m-tag">큐브 공통 환경</span></div>
        <div className="m-env-list" role="listbox" aria-label="그래프로 볼 항목">
          {items.map((i) => {
            const lv = zone.live ? levelOf(i.value, i.range) : 'none';
            const Icon = i.icon;
            return (
              <button key={i.key} type="button" role="option" aria-selected={sel === i.key} className={`m-env-row lv-${lv} ${sel === i.key ? 'is-on' : ''}`} onClick={() => setSel(i.key)}>
                <Icon className="m-ic" aria-hidden="true" />
                <span className="m-env-name">{i.label}</span>
                <b>{zone.live && i.value !== null ? fmt(i.value, i.decimals) : '—'}<small>{i.unit}</small></b>
                <em>{zone.live ? LEVEL_WORD[lv] : '확인 불가'}</em>
              </button>
            );
          })}
        </div>
      </section>

      <section className="m-card">
        <div className="m-sec-head"><h2>{item.label} 변화</h2><span className="m-tag">최근 24시간</span></div>
        <div className="m-chart">
          {state === 'loading' && <p className="m-note">기록을 불러오는 중이에요…</p>}
          {state === 'error' && <p className="m-note">기록을 불러오지 못했어요. 잠시 뒤 다시 열어 주세요.</p>}
          {state === 'ok' && points.length < 2 && <p className="m-note">아직 그래프로 보여 줄 기록이 없어요.</p>}
          {state === 'ok' && points.length >= 2 && (
            <ReadingChart className="m-chart-svg" label={item.label} unit={item.unit} points={points} range={item.range} decimals={item.decimals} />
          )}
        </div>
        <p className="m-note">초록 띠가 알맞은 범위예요 ({item.range.min}~{item.range.max}{item.unit}{item.general ? ', 일반 식물 기준' : ''}).</p>
      </section>

      <details className="m-more">
        <summary>단위와 기준 보기 <ChevronDown className="m-ic-sm" aria-hidden="true" /></summary>
        <ul className="m-kv">
          {items.map((i) => <li key={i.key}><span>{i.label}</span><b>{i.sensor}</b><em>알맞음 {i.range.min}~{i.range.max}{i.unit}</em></li>)}
        </ul>
        <label className="m-crop">작물 기준
          <select disabled={!canControl} value={zone.cropId ?? ''} onChange={(e) => onSetCrop(e.target.value || null)}>
            <option value="">일반 식물 기준 (미설정)</option>
            {crops.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
      </details>

      <details className="m-more">
        <summary>기기 정보 <ChevronDown className="m-ic-sm" aria-hidden="true" /></summary>
        <ul className="m-kv">
          <li><span>큐브</span><b>{zone.code}</b></li>
          <li><span>모델</span><b>{zone.device?.model ?? '—'}</b></li>
          <li><span>펌웨어</span><b>{zone.device?.firmware ?? '—'}</b></li>
          <li><span>IP 주소</span><b>{zone.device?.ip ?? '—'}</b></li>
          <li><span>와이파이 신호</span><b>{zone.signalDbm !== null ? `${zone.signalDbm} dBm` : '—'}</b></li>
          <li><span>마지막 수신</span><b>{relativeTime(zone.lastSeen, now)}</b></li>
        </ul>
      </details>
    </>
  );
};

// 큐브 동작: 정면 그림 + 장치 목록(누르면 그림에서 강조 + 역할 한 줄) + 직접 조절
const MotionTab: React.FC<CommonProps & {
  canControl: boolean; onSend: (key: CubeDevice, target: number | boolean, updates: Partial<ActuatorState>) => void;
  onStartIrrigation: () => void; onWaterRequested: () => void;
}> = ({ zone, a, pendingState, canControl, onSend, onStartIrrigation, onWaterRequested }) => {
  const [focus, setFocus] = useState<CubeDevice>('exhaust');
  const [confirmWater, setConfirmWater] = useState(false);
  const live = zone.live;
  const locked = !canControl || a.autoMode || !live;
  const info = DEVICES.find((d) => d.key === focus)!;
  const pumpMax = zone.device?.pumpMaxMs ? Math.round(zone.device.pumpMaxMs / 1000) : null;

  const pend = (key: CubeDevice) => {
    const s = pendingState(key);
    if (s === 'waiting') return <p className="m-pend" role="status"><Loader2 className="m-ic-xs m-spin" aria-hidden="true" />장치 응답을 기다리는 중</p>;
    if (s === 'noresponse') return <p className="m-pend is-bad" role="status">장치가 아직 응답하지 않았어요. 표시는 장치가 보고한 상태예요.</p>;
    return null;
  };

  return (
    <>
      <section className="m-card">
        <div className="m-sec-head"><h2>큐브 동작</h2><span className="m-tag">큐브 전체</span></div>
        <div className="m-cube cube-detail-activity">
          <CubeActivity actuator={a} live={live} sensors={zone.sensors} plantLabel="" minimal highlight={focus} ledBadge />
        </div>
        <p className="m-note m-center">앞에서 본 모습이에요. 두 팬은 뒷면에 달려 있어요.</p>

        <ul className="m-devices" role="listbox" aria-label="장치 고르기">
          {DEVICES.map((d) => {
            const Icon = d.icon;
            const on = live && isActive(a, d.key);
            return (
              <li key={d.key}>
                <button type="button" role="option" aria-selected={focus === d.key} className={`m-device ${focus === d.key ? 'is-focus' : ''} ${on ? 'is-on' : ''}`} onClick={() => setFocus(d.key)}>
                  <span className="m-device-ic"><Icon aria-hidden="true" className={on && (d.key === 'exhaust' || d.key === 'circulation') ? 'm-rotate' : ''} /></span>
                  <span className="m-device-text">
                    <b>{d.label}</b>
                    <small>{deviceStateText(a, d.key, live)}</small>
                  </span>
                  <span className={`m-onoff ${!live ? 'is-unknown' : on ? 'is-on' : ''}`}>{!live ? '확인 불가' : on ? '작동' : '멈춤'}</span>
                </button>
              </li>
            );
          })}
        </ul>
        <p className="m-role" aria-live="polite"><b>{info.label}</b> {info.role}</p>
      </section>

      <section className="m-card">
        <div className="m-sec-head"><h2>직접 조절</h2><span className="m-tag">큐브 전체에 적용</span></div>
        <p className="m-note">
          {!live ? '상태 확인 불가 · 큐브와 연결되면 조절할 수 있어요.'
            : !canControl ? '보기 전용이에요. 큐브 주인만 조절할 수 있어요.'
              : a.autoMode ? '자동 관리 중이에요. 직접 조절하려면 기존 대시보드에서 자동 관리를 꺼 주세요.'
                : '누르면 큐브로 바로 보내요. 표시는 장치가 보고한 상태를 따라가요.'}
        </p>
        <fieldset className="m-controls" disabled={locked}>
          <div className="m-ctl">
            <p className="m-ctl-head"><Lightbulb className="m-ic-sm" aria-hidden="true" />조명 밝기<b>{deviceStateText(a, 'led', live)}</b></p>
            <div className="m-seg m-seg-5">
              {LED_STEPS.map((v) => (
                <button key={v} type="button" aria-pressed={live && reportedValue(a, 'led') === v}
                  onClick={() => onSend('led', v, v === 0 ? { ledGrowLight: false, ledIntensity: 0 } : { ledGrowLight: true, ledIntensity: v })}>
                  {v === 0 ? '끄기' : `${v}%`}
                </button>
              ))}
            </div>
            {pend('led')}
          </div>
          {(['circulation', 'exhaust'] as const).map((key) => (
            <div key={key} className="m-ctl">
              <p className="m-ctl-head">
                {key === 'exhaust' ? <Fan className="m-ic-sm" aria-hidden="true" /> : <Wind className="m-ic-sm" aria-hidden="true" />}
                {key === 'exhaust' ? '배기팬 (밖으로)' : '순환팬 (안에서)'}<b>{live ? FAN_WORDS[reportedValue(a, key) as number] : '확인 불가'}</b>
              </p>
              <div className="m-seg">
                {FAN_WORDS.map((w, n) => (
                  <button key={w} type="button" aria-pressed={live && reportedValue(a, key) === n}
                    onClick={() => onSend(key, n, key === 'exhaust' ? { outerFan: n > 0, outerFanSpeed: n } : { innerFan: n > 0, innerFanSpeed: n })}>
                    {w}
                  </button>
                ))}
              </div>
              {pend(key)}
            </div>
          ))}
          <div className="m-ctl">
            <p className="m-ctl-head"><Droplets className="m-ic-sm" aria-hidden="true" />물 주기<b>{deviceStateText(a, 'water', live)}</b></p>
            {a.irrigationPump && live
              ? <button type="button" className="m-water is-on" onClick={() => onSend('water', false, { irrigationPump: false, nutrientValve: false })}>물 주기 멈추기</button>
              : <button type="button" className="m-water" onClick={() => setConfirmWater(true)}>물 주기</button>}
            {pend('water')}
          </div>
        </fieldset>
      </section>

      {confirmWater && (
        <div className="m-sheet-back" role="presentation" onClick={() => setConfirmWater(false)}>
          <div className="m-sheet" role="dialog" aria-modal="true" aria-labelledby="m-water-title" onClick={(e) => e.stopPropagation()}>
            <h3 id="m-water-title">물을 줄까요?</h3>
            <p>아래 물통의 물을 트레이로 올려서 <b>큐브 안 모든 화분</b>에 물을 줘요.{pumpMax ? ` 펌프는 최대 ${pumpMax}초 뒤 스스로 멈춰요.` : ''}</p>
            <div className="m-sheet-btns">
              <button type="button" className="m-btn-ghost" onClick={() => setConfirmWater(false)}>취소</button>
              <button type="button" className="m-btn-primary" onClick={() => { setConfirmWater(false); onWaterRequested(); onStartIrrigation(); }}>물 주기</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};
