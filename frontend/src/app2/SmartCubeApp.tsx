import React, { useEffect, useState } from 'react';
import {
  ArrowLeft, ArrowUpRight, Bell, Camera, Droplets, Fan, Activity, LayoutGrid, Lightbulb, Loader2, Menu, OctagonAlert,
  Search, SlidersHorizontal, Wind, WifiOff, CircleAlert,
} from 'lucide-react';
import type { ActuatorState, NotificationLogItem, ZoneConfig } from '../types/smartfarm';
import type { CropProfile } from '../lib/backend';
import { DEFAULT_ACTUATOR, relativeTime } from '../lib/deviceMap';
import { LiveCameraView } from '../components/LiveCameraView';
import { EmptyCubes, type EmptyState } from '../components/EmptyCubes';
import type { AppMenuItem } from '../lib/appMenu';
import {
  FAN_WORDS, categoryOf, isOff, lightHours, levelOf, metricsOf, overall, reportedOf, val, word,
  type Category, type DeviceKey, type Metric, type MetricKey,
} from './data';
import { ConfirmSheet, IconCircle } from './shared';
import { CubeCallouts, type CalloutItem } from './CubeCallouts';
import { AlertsScreen, MenuSheet, RecordsScreen, SettingsSheet } from './Screens';
import './app2.css';

// 2026-11-24 SMART CUBE 모바일 앱(4차: 사용자 지정 흐름).
// ① 첫 화면 = 내 화분 목록 → ② 화분을 누르면 그 화분의 상태 화면(상태/조절/기록/알림 탭). 뒤로 가면 언제나 ①.
// 수치는 실제 API 값만. 표시는 장치가 보고한 상태(reported)를 따른다. 제어는 App 이 바뀐 값만 보낸다.

const CUBE_IMG = '/smart-cube-front.webp';
const RESPONSE_TIMEOUT_MS = 10_000;

export interface SmartCubeAppProps {
  zones: ZoneConfig[];
  reported: Record<string, ActuatorState>;
  canControl: boolean;
  crops: CropProfile[];
  now: number;
  devMode: boolean;
  logs: NotificationLogItem[];
  menu: AppMenuItem[]; // 기능 페이지(장치 연결 등)
  empty: EmptyState; // 큐브가 없을 때 보여 줄 안내
  devicesHref: string | null;
  userEmail: string | null;
  canSignOut: boolean;
  onSignOut: () => void;
  onUpdate: (sn: string, updates: Partial<ActuatorState>) => void;
  onStartIrrigation: (sn: string) => void;
  onSetCrop: (sn: string, cropId: string | null) => void;
  onEmergencyStop: () => void;
}

export type Tab = 'status' | 'control' | 'records' | 'alerts';
type PendingMap = Partial<Record<DeviceKey, { target: number | boolean; at: number }>>;

export interface Ctx {
  zone: ZoneConfig;
  a: ActuatorState;
  m: Record<MetricKey, Metric>;
  crop: CropProfile | undefined;
  now: number;
  canControl: boolean;
  lockReason: string | null; // 조작을 막는 이유(없으면 null)
  send: (k: DeviceKey, target: number | boolean, updates: Partial<ActuatorState>) => void;
  askWater: () => void;
  pend: (k: DeviceKey) => 'waiting' | 'late' | null;
}

// ---------- ?demo 체험 모드 ----------
// 실제 큐브에는 아무 명령도 보내지 않는다(/control 호출 없음). 화면 안에서만 장치가 "응답"한 것처럼 바꾼다.
const DEMO_REPLY_MS = 900; // 누른 뒤 큐브가 대답하기까지(흉내)
const DEMO_WATER_MS = 8_000; // 물 주기는 이만큼 뒤 스스로 멈춘다
const isDemoUrl = () => { try { return new URLSearchParams(window.location.search).has('demo'); } catch { return false; } };

export const SmartCubeApp: React.FC<SmartCubeAppProps> = (p) => {
  const [demo] = useState(isDemoUrl);
  const [local, setLocal] = useState<Record<string, ActuatorState>>({});
  if (!demo) return <SmartCubeAppInner {...p} />;
  const base = (sn: string) => local[sn] ?? p.reported[sn] ?? DEFAULT_ACTUATOR;
  const later = (sn: string, updates: Partial<ActuatorState>, ms = DEMO_REPLY_MS) => {
    window.setTimeout(() => setLocal((prev) => ({ ...prev, [sn]: { ...(prev[sn] ?? p.reported[sn] ?? DEFAULT_ACTUATOR), ...updates } })), ms);
  };
  const reported: Record<string, ActuatorState> = {};
  for (const z of p.zones) reported[z.id] = base(z.id);
  return (
    <SmartCubeAppInner
      {...p}
      demo
      zones={p.zones.map((z) => ({ ...z, live: true }))}
      reported={reported}
      canControl
      onUpdate={(sn, u) => later(sn, u)}
      onStartIrrigation={(sn) => { later(sn, { irrigationPump: true }); later(sn, { irrigationPump: false }, DEMO_REPLY_MS + DEMO_WATER_MS); }}
      onEmergencyStop={() => p.zones.forEach((z) => later(z.id, {
        ledGrowLight: false, ledIntensity: 0, innerFan: false, innerFanSpeed: 0, outerFan: false, outerFanSpeed: 0, irrigationPump: false, nutrientValve: false, autoMode: false,
      }))}
    />
  );
};

const SmartCubeAppInner: React.FC<SmartCubeAppProps & { demo?: boolean }> = (p) => {
  const [open, setOpen] = useState<string | null>(null); // 연 화분(없으면 목록)
  const [tab, setTab] = useState<Tab>('status');
  const [menu, setMenu] = useState(false);
  const [settings, setSettings] = useState(false);
  const [waterAsk, setWaterAsk] = useState(false);
  const [stopAsk, setStopAsk] = useState(false);
  const [pending, setPending] = useState<PendingMap>({});
  const zone = (open && p.zones.find((z) => z.id === open)) || p.zones[0] || null;
  const a = zone ? p.reported[zone.id] ?? DEFAULT_ACTUATOR : DEFAULT_ACTUATOR;

  useEffect(() => { window.scrollTo(0, 0); }, [tab, open]);
  // 휴대폰 뒤로 가기 버튼으로도 목록으로 돌아오게
  useEffect(() => {
    if (!open) return undefined;
    window.history.pushState({ a2: 'cube' }, '');
    const onPop = () => setOpen(null);
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, [open]);
  // 장치가 목표 상태를 보고하면 "응답 기다리는 중"을 지운다
  useEffect(() => {
    setPending((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const k of Object.keys(prev) as DeviceKey[]) if (prev[k] && reportedOf(a, k) === prev[k]!.target) { delete next[k]; changed = true; }
      return changed ? next : prev;
    });
  }, [a]);

  if (!zone) {
    return <div className="a2-root"><div className="a2-app"><div className="a2-empty"><EmptyCubes state={p.empty} devicesHref={p.devicesHref} buttonClass="a2-btn-lime a2-empty-btn" /></div></div></div>;
  }
  const m = metricsOf(zone);
  const crop = p.crops.find((c) => c.id === zone.cropId);
  const lockReason = !zone.live ? '큐브와 연락이 안 돼서 지금은 조작할 수 없어요.'
    : !p.canControl ? '보기 전용이에요. 큐브 주인만 조작할 수 있어요.'
      : a.autoMode ? '자동 돌보기 중이에요. 직접 조절하려면 위의 자동 돌보기를 꺼 주세요.' : null;
  const ctx: Ctx = {
    zone, a, m, crop, now: p.now, canControl: p.canControl, lockReason,
    send: (k, target, updates) => { setPending((prev) => ({ ...prev, [k]: { target, at: Date.now() } })); p.onUpdate(zone.id, updates); },
    askWater: () => setWaterAsk(true),
    pend: (k) => { const x = pending[k]; if (!x) return null; return Date.now() - x.at < RESPONSE_TIMEOUT_MS ? 'waiting' : 'late'; },
  };
  const unhandled = p.logs.filter((l) => l.handled === false && (l.severity === 'danger' || l.severity === 'warning')).length;
  const alertCount = activeAlertCount(zone, m) + unhandled;
  const listAlertCount = p.zones.reduce((n, z) => n + activeAlertCount(z, metricsOf(z)), 0) + unhandled;
  const pumpMax = zone.device?.pumpMaxMs ? Math.round(zone.device.pumpMaxMs / 1000) : null;
  const back = () => { if (window.history.state?.a2 === 'cube') window.history.back(); else setOpen(null); };

  return (
    <div className="a2-root">
      <div className="a2-app">
        {p.demo && <p className="sc-demo" role="status">체험 모드 · 실제 큐브는 움직이지 않아요</p>}
        {!open
          ? <PlantList zones={p.zones} crops={p.crops} userEmail={p.userEmail} alertCount={listAlertCount}
            onPick={(id) => { setOpen(id); setTab('status'); setPending({}); }}
            onBell={() => { setOpen(zone.id); setTab('alerts'); }} onMenu={() => setMenu(true)} />
          : (
            <>
              <CubeHeader ctx={ctx} alertCount={alertCount} onBack={back} onAlerts={() => setTab('alerts')} onStop={() => setStopAsk(true)} />
              {tab === 'status' && <StatusScreen ctx={ctx} devMode={p.devMode} onGo={setTab} />}
              {tab === 'control' && <ControlScreen ctx={ctx} cropAssigned={Boolean(zone.cropId)} onSettings={() => setSettings(true)}
                onToggleAuto={() => p.onUpdate(zone.id, { autoMode: !a.autoMode })} onStop={() => setStopAsk(true)} />}
              {tab === 'records' && <RecordsScreen ctx={ctx} />}
              {tab === 'alerts' && <AlertsScreen ctx={ctx} logs={p.logs} />}
            </>
          )}
      </div>
      {open && <TabBar tab={tab} onTab={setTab} alertCount={alertCount} />}
      {menu && <MenuSheet onClose={() => setMenu(false)} onSettings={() => { setMenu(false); setSettings(true); }}
        onEmergencyStop={() => { setMenu(false); setStopAsk(true); }} canControl={p.canControl}
        items={p.menu} canSignOut={p.canSignOut} onSignOut={p.onSignOut} />}
      {settings && <SettingsSheet ctx={ctx} crops={p.crops} onClose={() => setSettings(false)} onSetCrop={(id) => p.onSetCrop(zone.id, id)} />}
      {waterAsk && (
        <ConfirmSheet title="물을 줄까요?" ok="물 주기"
          body={<p>아래 물통의 물을 화분 밑 트레이로 올려요(저면관수). 큐브 안 모든 화분에 함께 물이 가요.{pumpMax ? ` 펌프는 최대 ${pumpMax}초 뒤 스스로 멈춰요.` : ' 펌프는 정해진 시간이 지나면 스스로 멈춰요.'}</p>}
          onCancel={() => setWaterAsk(false)}
          onOk={() => { setWaterAsk(false); setPending((prev) => ({ ...prev, water: { target: true, at: Date.now() } })); p.onStartIrrigation(zone.id); }} />
      )}
      {stopAsk && (
        <ConfirmSheet title="모두 멈출까요?" ok="긴급 정지" danger
          body={<p>조명·순환팬·배기팬·급수를 한 번에 꺼요. 자동 돌보기도 멈춰요. 다시 켜려면 조절 화면에서 하나씩 켜 주세요.</p>}
          onCancel={() => setStopAsk(false)}
          onOk={() => { setStopAsk(false); p.onEmergencyStop(); }} />
      )}
    </div>
  );
};

export function activeAlertCount(zone: ZoneConfig, m: Record<MetricKey, Metric>): number {
  if (!zone.live) return 1;
  return Object.values(m).filter(isOff).length;
}

// ---------- ① 첫 화면: 내 화분 ----------
const CATS: Array<{ key: Category | 'all'; label: string }> = [
  { key: 'all', label: '전체' }, { key: 'leafy', label: '잎채소' }, { key: 'flower', label: '꽃' }, { key: 'herb', label: '허브' },
];

const PlantList: React.FC<{ zones: ZoneConfig[]; crops: CropProfile[]; userEmail: string | null; alertCount: number; onPick: (id: string) => void; onBell: () => void; onMenu: () => void }> = ({
  zones, crops, userEmail, alertCount, onPick, onBell, onMenu,
}) => {
  const [cat, setCat] = useState<Category | 'all'>('all');
  const [q, setQ] = useState('');
  const name = userEmail ? userEmail.split('@')[0] : null;
  const list = zones.filter((z) => {
    const cropName = z.cropId ? z.crop : null;
    if (cat !== 'all' && categoryOf(cropName) !== cat) return false;
    const text = `${cropName ?? ''} ${z.code} ${z.name}`;
    return !q.trim() || text.includes(q.trim());
  });
  return (
    <section className="a2-list" aria-labelledby="a2-mine">
      <div className="a2-greet">
        <span className="a2-avatar" aria-hidden="true">{(name ?? '나').slice(0, 1).toUpperCase()}</span>
        <div className="min-w-0 flex-1">
          <p className="a2-greet-hi">안녕하세요{name ? `, ${name}님` : ''}</p>
          <small className="a2-label">오늘도 새로 자란 잎을 찾아봐요</small>
        </div>
        <button type="button" className="a2-round" onClick={onBell} aria-label={`알림${alertCount ? ` ${alertCount}건` : ''}`}>
          <Bell aria-hidden="true" strokeWidth={1.6} />{alertCount > 0 && <span className="a2-badge">{alertCount}</span>}
        </button>
        <button type="button" className="a2-round" onClick={onMenu} aria-label="메뉴 (재배 설정 · 장치 연결 · 긴급 정지)"><Menu aria-hidden="true" strokeWidth={1.6} /></button>
      </div>
      <div className="a2-search-row">
        <label className="a2-search">
          <Search aria-hidden="true" strokeWidth={1.6} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="식물 이름, 큐브 번호 찾기" aria-label="화분 찾기" />
        </label>
        <button type="button" className="a2-round" aria-label="검색어와 분류 초기화" onClick={() => { setCat('all'); setQ(''); }}><SlidersHorizontal aria-hidden="true" strokeWidth={1.6} /></button>
      </div>
      <h1 id="a2-mine" className="a2-h2 a2-mine">내 화분</h1>
      <div className="a2-chips" role="radiogroup" aria-label="분류">
        {CATS.map((c) => <button key={c.key} type="button" role="radio" aria-checked={cat === c.key} className={cat === c.key ? 'is-on' : ''} onClick={() => setCat(c.key)}>{c.label}</button>)}
      </div>
      {list.length === 0 && <p className="a2-empty-sm">조건에 맞는 화분이 없어요.</p>}
      <div className="a2-cubes">
        {list.map((z) => {
          const mm = metricsOf(z);
          const cropName = z.cropId ? z.crop : (crops.length ? '작물 미설정' : '내 식물');
          return (
            <button key={z.id} type="button" className={`a2-cube-card ${z.live ? '' : 'is-off'}`} onClick={() => onPick(z.id)} aria-label={`${cropName} ${z.code} 상태 보기`}>
              <span className="a2-cube-text">
                <b>{cropName}</b>
                <small>{z.code} · 실내{z.live ? '' : ' · 연결 끊김'}</small>
              </span>
              <span className="a2-arrow is-lime" aria-hidden="true"><ArrowUpRight strokeWidth={1.8} /></span>
              <img src={CUBE_IMG} alt="" className="a2-cube-img" />
              <span className="a2-cube-stats">
                <span><b>{mm.soil.value === null ? '—' : `${val(mm.soil)}%`}</b><small>흙 촉촉함</small></span>
                <span><b>{mm.temp.value === null ? '—' : `${val(mm.temp)}°C`}</b><small>온도</small></span>
              </span>
              {!z.live && <span className="a2-off-tag">연결 끊김</span>}
            </button>
          );
        })}
      </div>
    </section>
  );
};

// ---------- ② 화분 화면 상단 ----------
const CubeHeader: React.FC<{ ctx: Ctx; alertCount: number; onBack: () => void; onAlerts: () => void; onStop: () => void }> = ({ ctx, alertCount, onBack, onAlerts, onStop }) => {
  const { zone } = ctx;
  return (
    <header className="a2-detail-top">
      <button type="button" className="a2-round" onClick={onBack} aria-label="내 화분 목록으로"><ArrowLeft aria-hidden="true" strokeWidth={1.6} /></button>
      <div className="min-w-0 flex-1">
        <p className="a2-detail-name">{zone.cropId ? zone.crop : '작물 미설정'}</p>
        <small className="a2-label">{zone.code} · {zone.live ? `연결됨 · ${relativeTime(zone.lastSeen, ctx.now)}` : '연결 끊김'}</small>
      </div>
      <button type="button" className="a2-round" onClick={onAlerts} aria-label={`알림${alertCount ? ` ${alertCount}건` : ''}`}><Bell aria-hidden="true" strokeWidth={1.6} />{alertCount > 0 && <span className="a2-badge">{alertCount}</span>}</button>
      <button type="button" className="a2-stop-sm" onClick={onStop} disabled={!ctx.canControl}>긴급 정지</button>
    </header>
  );
};

// ---------- ② 상태 탭 ----------
const hhmm = (t: number) => new Date(t).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });

const StatusScreen: React.FC<{ ctx: Ctx; devMode: boolean; onGo: (t: Tab) => void }> = ({ ctx, devMode, onGo }) => {
  const { zone, m, a } = ctx;
  const [camera, setCamera] = useState(false);
  const o = overall(zone, m);
  // 그림은 장치가 보고한 상태만 따른다
  const scene = {
    live: zone.live,
    led: reportedOf(a, 'led') as number,
    inner: reportedOf(a, 'inner') as number,
    outer: reportedOf(a, 'outer') as number,
    pump: a.irrigationPump,
  };
  const stateText = !scene.live ? '연결 끊김 · 지금 큐브 모습을 알 수 없어요'
    : `조명 ${scene.led > 0 ? `켜짐 ${scene.led}%` : '꺼짐'} · 순환팬 ${FAN_WORDS[scene.inner]} · 배기팬 ${FAN_WORDS[scene.outer]} · ${scene.pump ? '물 올리는 중' : '물 대기'}`;
  const sceneLabel = `${zone.code} 큐브 그림. ${stateText}`;
  const NAMES: Record<DeviceKey, string> = { led: '조명', water: '물 주기', inner: '순환팬', outer: '환기' };
  const waits = (Object.keys(NAMES) as DeviceKey[]).map((k) => ({ k, st: ctx.pend(k) })).filter((x) => x.st);
  const lit = scene.led > 0;
  const venting = scene.outer > 0;
  const pendOf = (k: DeviceKey) => ctx.pend(k);
  const onLevel = a.lastLedIntensity && a.lastLedIntensity > 0 ? a.lastLedIntensity : 70;
  const qSub = (k: DeviceKey, idle: string) => { const st = pendOf(k); return st === 'waiting' ? '응답 기다리는 중' : st === 'late' ? '응답 없음' : scene.live ? idle : '상태 알 수 없음'; };
  const soilLow = levelOf(m.soil.value, m.soil.range) === 'low';
  const stuffy = isOff(m.temp) || isOff(m.air);
  const next = !zone.live ? { icon: WifiOff, title: '큐브 연결 확인', sub: '전원과 와이파이를 확인해 주세요', action: null }
    : soilLow ? { icon: Droplets, title: '물 주기', sub: `지금 흙이 말랐어요 (${val(m.soil)}%)`, action: { label: '물 주기', run: ctx.askWater } }
      : stuffy ? {
        icon: Fan, title: '환기하기', sub: (isOff(m.temp) ? `온도가 ${word(m.temp)}` : `공기가 ${word(m.air)}`) + (a.outerFan ? ' · 지금 환기 중이에요' : ''),
        action: a.outerFan ? null : { label: '환기 켜기', run: () => ctx.send('outer', 2, { outerFan: true, outerFanSpeed: 2 }) },
      }
        : { icon: Droplets, title: '물 주기', sub: `흙이 ${m.soil.range.min}% 아래로 내려가면`, action: null };
  // 콜아웃 5개: 실제 값만, 없으면 "—", 범위 밖이면 주황 + 쉬운 말
  const v = (mm: Metric, unit: string) => (mm.value === null ? '—' : `${val(mm)}${unit}`);
  // 잎 상태: AI 분석 값이 아직 없어서, 재배 환경이 식물이 좋아하는 범위를 벗어났는지로 판단한다
  const offList = (Object.values(m) as Metric[]).filter(isOff);
  const leaf = !zone.live || o.measured === 0 ? { name: '잎 상태', value: '—' }
    : offList.length ? { name: '잎 상태', value: offList.length > 1 ? '주의 · 살펴봐 주세요' : `주의 · ${word(offList[0])}`, alert: true }
      : { name: '잎 상태', value: '건강해요' };
  const callouts: CalloutItem[] = [
    { key: 'light', name: '빛', value: m.light.value === null ? '—' : `${val(m.light)} klx · ${scene.led > 0 ? `밝기 ${scene.led}%` : '조명 꺼짐'}`, warn: isOff(m.light), onPick: () => onGo('control') },
    { key: 'camera', name: '카메라', value: '실시간 보기', onPick: () => setCamera(true) },
    { key: 'soil', name: '흙 촉촉함', value: m.soil.value === null ? '—' : `${val(m.soil)}% ${word(m.soil)}`, warn: isOff(m.soil), onPick: () => onGo('control') },
    { key: 'air', name: '온도 · 습도', value: isOff(m.temp) ? `${v(m.temp, '°C')} ${word(m.temp)}` : isOff(m.air) ? `${v(m.air, '%')} ${word(m.air)}` : m.temp.value === null && m.air.value === null ? '—' : `${v(m.temp, '°C')} · ${v(m.air, '%')}`, warn: isOff(m.temp) || isOff(m.air), onPick: () => onGo('control') },
    { key: 'water', name: '급수', value: !zone.live ? '—' : scene.pump ? '물 올리는 중' : '저면관수 · 대기', onPick: () => onGo('control') },
  ];
  return (
    <section className="a2-overview" aria-label="지금 상태">
      <div className="a2-frame">
        {camera
          ? <div className="a2-frame-cam"><LiveCameraView cubeSn={zone.id} devMode={devMode} /></div>
          : (
            <CubeCallouts scene={scene} sceneLabel={sceneLabel} items={callouts} lens={leaf}
              offline={zone.live ? null : `연결 끊김 · 마지막 수신 ${zone.lastSeen ? hhmm(zone.lastSeen) : '—'}`} />
          )}
        {camera && (
          <button type="button" className="a2-cam-btn a2-cam-top is-on" onClick={() => setCamera(false)} aria-pressed>
            <Camera aria-hidden="true" strokeWidth={1.6} />그림 보기
          </button>
        )}
        {!camera && a.autoMode && zone.live && (
          <div className="sc-badges is-row" aria-hidden="true"><span className="sc-badge is-auto">자동 돌보기 중</span></div>
        )}
      </div>
      {!camera && <p className="sr-only" aria-live="polite">{stateText}</p>}

      <div className="sc-quick" role="group" aria-label="바로 하기">
        <QuickBtn icon={Lightbulb} on={lit} pend={pendOf('led')} locked={ctx.lockReason !== null}
          label={lit ? '조명 끄기' : '조명 켜기'} sub={qSub('led', lit ? `지금 ${scene.led}%` : '지금 꺼짐')}
          onClick={() => ctx.send('led', lit ? 0 : onLevel, lit ? { ledGrowLight: false, ledIntensity: 0 } : { ledGrowLight: true, ledIntensity: onLevel })} />
        <QuickBtn icon={Droplets} on={scene.pump} pend={pendOf('water')} locked={ctx.lockReason !== null}
          label={scene.pump ? '물 멈추기' : '물 주기'} sub={qSub('water', scene.pump ? '물 올리는 중' : '트레이로')}
          onClick={() => (scene.pump ? ctx.send('water', false, { irrigationPump: false, nutrientValve: false }) : ctx.askWater())} />
        <QuickBtn icon={Wind} on={venting} pend={pendOf('outer')} locked={ctx.lockReason !== null}
          label={venting ? '환기 끄기' : '환기 켜기'} sub={qSub('outer', venting ? `배기팬 ${FAN_WORDS[scene.outer]}` : '지금 꺼짐')}
          onClick={() => (venting ? ctx.send('outer', 0, { outerFan: false, outerFanSpeed: 0 }) : ctx.send('outer', 2, { outerFan: true, outerFanSpeed: 2 }))} />
      </div>
      {ctx.lockReason && <p className="sc-lock">{ctx.lockReason}</p>}

      {/* 전체 상태 + 다음 할 일 (한 카드) */}
      <div className={`a2-card sc-summary ${o.pct === null ? 'is-none' : offList.length ? 'is-warn' : 'is-good'}`}>
        <div className="sc-sum-top">
          <span className="sc-ring" role="img" aria-label={o.pct === null ? '전체 상태 알 수 없음' : `전체 상태 ${o.pct}%`}>
            <svg viewBox="0 0 64 64" aria-hidden="true">
              <circle cx="32" cy="32" r="27" className="sc-ring-bg" />
              <circle cx="32" cy="32" r="27" className="sc-ring-fg" pathLength={100} style={{ strokeDasharray: `${o.pct ?? 0} 100` }} />
            </svg>
            <b>{o.pct === null ? '—' : o.pct}<small>{o.pct === null ? '' : '%'}</small></b>
          </span>
          <div className="min-w-0 flex-1">
            <small className="a2-label">전체 상태</small>
            <p className="sc-sum-word">{o.word}</p>
            <small className="a2-label">{o.measured ? `${o.measured}개 중 ${o.inRange}개가 좋은 범위` : '측정값이 아직 없어요'} · {a.autoMode ? '자동 돌보기' : '직접 돌보는 중'}</small>
          </div>
        </div>
        <div className="sc-sum-next">
          <span className="sc-sum-icon" aria-hidden="true"><next.icon strokeWidth={1.7} /></span>
          <div className="min-w-0 flex-1">
            <small className="a2-label">다음 할 일</small>
            <p><b>{next.title}</b> · {next.sub}</p>
          </div>
          {next.action && <button type="button" className="sc-sum-btn" disabled={ctx.lockReason !== null} onClick={next.action.run}>{next.action.label}</button>}
        </div>
      </div>
    </section>
  );
};

// 그림 아래 바로가기: 둥근 아이콘 + 이름 + 작은 상태 글자. 켜짐 = 진한 초록 원, 응답 대기 = 도는 링, 응답 없음 = 주황 점
const QuickBtn: React.FC<{ icon: React.ElementType; on: boolean; pend: 'waiting' | 'late' | null; locked: boolean; label: string; sub: string; onClick: () => void }> = ({
  icon: Icon, on, pend, locked, label, sub, onClick,
}) => (
  <button type="button" className={`sc-qbtn ${on ? 'is-on' : ''} ${pend ? `is-${pend}` : ''}`} disabled={locked} aria-pressed={on} onClick={onClick}>
    <span className="sc-qicon" aria-hidden="true"><Icon strokeWidth={1.8} />{pend === 'late' && <i />}</span>
    <b>{label}</b>
    <small>{sub}</small>
  </button>
);

// ---------- ② 조절 탭(장치 전부 한 화면) ----------
const ControlScreen: React.FC<{ ctx: Ctx; cropAssigned: boolean; onSettings: () => void; onToggleAuto: () => void; onStop: () => void }> = ({
  ctx, cropAssigned, onSettings, onToggleAuto, onStop,
}) => {
  const { zone, a, m } = ctx;
  const led = a.ledGrowLight ? a.ledIntensity : 0;
  const inner = reportedOf(a, 'inner') as number;
  const outer = reportedOf(a, 'outer') as number;
  const st = (on: string) => (zone.live ? on : '—');
  return (
    <section className="a2-screen ctl2" aria-labelledby="a2-ctl-title">
      <div className="a2-row-between">
        <h1 id="a2-ctl-title" className="a2-title-sm">직접 <span>조절</span></h1>
        <button type="button" className="a2-round" onClick={onSettings} aria-label="재배 설정 열기"><SlidersHorizontal aria-hidden="true" strokeWidth={1.6} /></button>
      </div>

      <div className={`a2-card a2-auto ${a.autoMode ? 'is-on' : ''}`}>
        <div className="min-w-0">
          <p><b>자동 돌보기</b></p>
          <small className="a2-label">{a.autoMode ? '큐브가 작물 기준으로 알아서 조절하고 있어요.' : cropAssigned ? '켜면 큐브가 작물 기준으로 알아서 조절해요.' : '재배 설정에서 식물을 먼저 고르면 켤 수 있어요.'}</small>
        </div>
        <button type="button" role="switch" aria-checked={a.autoMode} className={`a2-switch ${a.autoMode ? 'is-on' : ''}`}
          disabled={!ctx.canControl || !zone.live || (!cropAssigned && !a.autoMode)} onClick={onToggleAuto}>
          <span aria-hidden="true" /><span className="sr-only">자동 돌보기 {a.autoMode ? '끄기' : '켜기'}</span>
        </button>
      </div>

      {ctx.lockReason && <p className="a2-lock"><CircleAlert aria-hidden="true" strokeWidth={1.6} />{ctx.lockReason}</p>}

      <Device icon={Lightbulb} title="조명" state={st(led > 0 ? `켜짐 ${led}%` : '꺼짐')} on={zone.live && led > 0} pend={ctx.pend('led')}><LightItem ctx={ctx} /></Device>
      <Device icon={Droplets} title="물" state={st(a.irrigationPump ? '물 올리는 중' : `흙 ${word(m.soil)}`)} on={zone.live && a.irrigationPump} pend={ctx.pend('water')}><WaterItem ctx={ctx} /></Device>
      <Device icon={Wind} title="바람 · 순환팬" state={st(FAN_WORDS[inner])} on={zone.live && inner > 0} pend={ctx.pend('inner')}><FanItem ctx={ctx} which="inner" /></Device>
      <Device icon={Fan} title="환기 · 배기팬" state={st(FAN_WORDS[outer])} on={zone.live && outer > 0} pend={ctx.pend('outer')}><FanItem ctx={ctx} which="outer" /></Device>

      <button type="button" className="a2-stop-wide" onClick={onStop} disabled={!ctx.canControl}>
        <OctagonAlert aria-hidden="true" strokeWidth={1.8} />긴급 정지 · 모두 끄기
      </button>
    </section>
  );
};

// 장치 카드 머리: 상태 화면 바로가기와 같은 둥근 아이콘 + 콜아웃 라벨(작은 회색 이름 / 큰 값)
const Device: React.FC<{ icon: React.ElementType; title: string; state: string; on: boolean; pend?: 'waiting' | 'late' | null; children: React.ReactNode }> = ({ icon: Icon, title, state, on, pend = null, children }) => (
  <div className={`a2-card a2-device ${on ? 'is-on' : ''}`}>
    <div className="ctl2-head">
      <span className={`sc-qicon ${pend === 'waiting' ? 'is-waiting' : ''}`} aria-hidden="true"><Icon strokeWidth={1.8} />{pend === 'late' && <i />}</span>
      <div className="min-w-0 flex-1">
        <h2 className="ctl2-name">{title}</h2>
        <p className="ctl2-value">{state}</p>
      </div>
    </div>
    {children}
  </div>
);

const Pend: React.FC<{ ctx: Ctx; k: DeviceKey }> = ({ ctx, k }) => {
  const s = ctx.pend(k);
  if (s === 'waiting') return <p className="a2-pend"><Loader2 className="a2-spin" aria-hidden="true" />장치 응답을 기다리는 중</p>;
  if (s === 'late') return <p className="a2-pend is-late">장치가 아직 응답하지 않았어요 · 표시는 장치가 보고한 상태예요</p>;
  return null;
};

const Big: React.FC<{ value: string; unit?: string; label: string }> = ({ value, unit, label }) => (
  <div className="a2-big"><small>{label}</small><b>{value}{unit && <span>{unit}</span>}</b></div>
);

const LightItem: React.FC<{ ctx: Ctx }> = ({ ctx }) => {
  const { a, m, crop, zone } = ctx;
  const shown = a.ledGrowLight ? a.ledIntensity : 0;
  const [led, setLed] = useState(shown);
  const [drag, setDrag] = useState(false);
  useEffect(() => { if (!drag) setLed(shown); }, [shown, drag]);
  const hours = lightHours(crop);
  return (
    <>
      <div className="a2-bigs">
        <Big value={hours === null ? '—' : String(hours)} unit={hours === null ? undefined : 'h'} label="하루 조명 시간" />
        <Big value={val(m.light)} unit={m.light.value === null ? undefined : 'klx'} label={`지금 빛 · ${word(m.light)}`} />
      </div>
      <fieldset className="a2-ctl" disabled={ctx.lockReason !== null}>
        <div className="a2-row-between">
          <span>조명 {zone.live ? (shown > 0 ? '켜짐' : '꺼짐') : '—'}</span>
          <button type="button" role="switch" aria-checked={shown > 0} className={`a2-switch ${shown > 0 ? 'is-on' : ''}`}
            onClick={() => ctx.send('led', shown > 0 ? 0 : 70, shown > 0 ? { ledGrowLight: false, ledIntensity: 0 } : { ledGrowLight: true, ledIntensity: 70 })}>
            <span aria-hidden="true" /><span className="sr-only">조명 {shown > 0 ? '끄기' : '켜기'}</span>
          </button>
        </div>
        <label className="a2-slider"><span>밝기 {led}%</span>
          <input type="range" min="0" max="100" step="5" value={led} onPointerDown={() => setDrag(true)} onPointerUp={() => setDrag(false)}
            onChange={(e) => { const v = Number(e.target.value); setLed(v); ctx.send('led', v, v > 0 ? { ledGrowLight: true, ledIntensity: v } : { ledGrowLight: false, ledIntensity: 0 }); }} />
        </label>
        <Pend ctx={ctx} k="led" />
      </fieldset>
      <p className="a2-note">좋아하는 빛 {m.light.range.min}~{m.light.range.max}klx · 지금 {val(m.light)}{m.light.value === null ? '' : 'klx'} ({word(m.light)}){hours === null ? ' · 조명 시간은 식물을 고르면 보여요' : ''}</p>
    </>
  );
};

const WaterItem: React.FC<{ ctx: Ctx }> = ({ ctx }) => {
  const { a, m, zone } = ctx;
  const pumpMax = zone.device?.pumpMaxMs ? Math.round(zone.device.pumpMaxMs / 1000) : null;
  return (
    <>
      <div className="a2-bigs">
        <Big value={val(m.soil)} unit={m.soil.value === null ? undefined : '%'} label={`흙 촉촉함 · ${word(m.soil)}`} />
        <Big value={pumpMax === null ? '—' : String(pumpMax)} unit={pumpMax === null ? undefined : '초'} label="1회 최대 급수" />
      </div>
      <fieldset className="a2-ctl" disabled={ctx.lockReason !== null}>
        {a.irrigationPump && zone.live
          ? <button type="button" className="a2-btn-lime is-on" onClick={() => ctx.send('water', false, { irrigationPump: false, nutrientValve: false })}>물 주는 중 · 멈추기</button>
          : <button type="button" className="a2-btn-lime" onClick={ctx.askWater}><Droplets aria-hidden="true" strokeWidth={1.8} />물 주기</button>}
        <Pend ctx={ctx} k="water" />
      </fieldset>
      <p className="a2-note">이 큐브는 <b>저면관수</b>예요. 아래 칸 물통의 물을 화분 밑 트레이로 올리면 뿌리가 필요한 만큼 아래에서 빨아올려요. 흙이 {m.soil.range.min}% 아래로 내려가면 물을 주세요.</p>
    </>
  );
};

const FanItem: React.FC<{ ctx: Ctx; which: 'inner' | 'outer' }> = ({ ctx, which }) => {
  const { a, m, zone } = ctx;
  const now = reportedOf(a, which) as number;
  return (
    <>
      <div className="a2-bigs">
        <Big value={val(m.temp)} unit={m.temp.value === null ? undefined : '°C'} label={`온도 · ${word(m.temp)}`} />
        <Big value={val(m.air)} unit={m.air.value === null ? undefined : '%'} label={`습도 · ${word(m.air)}`} />
      </div>
      <fieldset className="a2-ctl" disabled={ctx.lockReason !== null}>
        <div className="a2-pills">
          {FAN_WORDS.map((w, n) => (
            <button key={w} type="button" aria-pressed={zone.live && now === n} className={zone.live && now === n ? 'is-on' : ''}
              onClick={() => ctx.send(which, n, which === 'inner' ? { innerFan: n > 0, innerFanSpeed: n } : { outerFan: n > 0, outerFanSpeed: n })}>{w}</button>
          ))}
        </div>
        <Pend ctx={ctx} k={which} />
      </fieldset>
      <p className="a2-note">{which === 'inner'
        ? '큐브 안에서 공기를 골고루 돌려요. 바람을 맞으면 줄기가 튼튼해지고 곰팡이가 덜 생겨요.'
        : `큐브 안 공기를 밖으로 내보내요. 덥거나 습할 때 켜 주세요. 지금 온도 ${val(m.temp)}${m.temp.value === null ? '' : '°C'}.`}</p>
    </>
  );
};

// ---------- 하단 탭 바 ----------
const TABS: Array<{ key: Tab; label: string; icon: React.ElementType }> = [
  { key: 'status', label: '상태', icon: Activity },
  { key: 'control', label: '조절', icon: SlidersHorizontal },
  { key: 'records', label: '기록', icon: LayoutGrid },
  { key: 'alerts', label: '알림', icon: Bell },
];
const TabBar: React.FC<{ tab: Tab; onTab: (t: Tab) => void; alertCount: number }> = ({ tab, onTab, alertCount }) => (
  <nav className="a2-tabbar" aria-label="주 메뉴">
    {TABS.map((t) => (
      <button key={t.key} type="button" className={tab === t.key ? 'is-on' : ''} aria-current={tab === t.key ? 'page' : undefined} onClick={() => onTab(t.key)}>
        <span className="a2-tab-circle"><t.icon aria-hidden="true" strokeWidth={1.6} />{t.key === 'alerts' && alertCount > 0 && <span className="a2-badge">{alertCount}</span>}</span>
        <span className="a2-tab-label">{t.label}</span>
      </button>
    ))}
  </nav>
);
