import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Bell, ChevronDown, ChevronRight, Droplets, Fan, Leaf, LogOut, LineChart, Lightbulb, Menu, Pause, Play, Search, SlidersHorizontal,
  Sprout, Waves, Wind, ArrowUpRight, X, WifiOff, Sun, Loader2,
} from 'lucide-react';
import type { ActuatorState, ValueRange, ZoneConfig } from '../types/smartfarm';
import { getHistory, getReport, getReports, getSnapshotBlob, getSnapshots, type CropProfile, type ReportJob } from '../lib/backend';
import { pointsFromRows, type HistoryPoint } from '../lib/history';
import { rangeOf } from '../lib/zoneRanges';
import { DEFAULT_ACTUATOR, fmt, relativeTime } from '../lib/deviceMap';
import { CubeScene, VIEW as SCENE } from '../app2/CubeScene';
import { EmptyCubes, type EmptyState } from '../components/EmptyCubes';
import type { AppMenuItem } from '../lib/appMenu';
import { chartPaths, everyMs } from '../lib/chartPath';
import { extraMetricsOf, extraText, hasChart, type ChartMetric } from '../lib/extraMetrics';
import './desk.css';

// 2026-11-24 새 웹 대시보드 시안 (데스크톱 우선 1440×900, Oripio "Smart Plant Monitoring Dashboard" 느낌).
// 대상: 교육생과 도시에서 식물을 키우는 사람. 보기 중심 화면이고, 조작은 오른쪽 서랍에서 한다.
// 수치는 모두 실제 API 값(SSE 텔레메트리·이력·리포트). 값이 없으면 "—". 가짜 숫자·예시 데이터 없음.
// 표시와 애니메이션은 장치가 보고한 상태(reported)를 따른다. 제어 명령은 App 이 바뀐 값만 보낸다.

const IMG = { src: '/smart-cube-front.webp', w: 1295, h: 1214 };
// 히어로 캔버스(1000×700) 안의 큐브 그림 위치
const C = { x: 280, y: 60, s: 0.413 };
const cx = (x: number) => C.x + x * C.s;
const cy = (y: number) => C.y + y * C.s;

type Level = 'low' | 'ok' | 'high' | 'none';
type MetricKey = 'temp' | 'air' | 'soil' | 'light';
const GENERAL: Record<MetricKey, ValueRange> = { temp: { min: 18, max: 28 }, air: { min: 40, max: 70 }, soil: { min: 35, max: 70 }, light: { min: 5, max: 20 } };
const WORDS: Record<MetricKey, Record<Exclude<Level, 'none'>, string>> = {
  temp: { low: '조금 추워요', ok: '좋아요', high: '조금 더워요' },
  air: { low: '건조해요', ok: '좋아요', high: '습해요' },
  soil: { low: '말랐어요', ok: '촉촉해요', high: '너무 젖었어요' },
  light: { low: '부족해요', ok: '충분해요', high: '너무 밝아요' },
};
const levelOf = (v: number | null, r: ValueRange): Level => (v === null ? 'none' : v < r.min ? 'low' : v > r.max ? 'high' : 'ok');
const FAN_WORDS = ['끔', '약', '중', '강'];
const RESPONSE_TIMEOUT_MS = 10_000;

interface Metric {
  key: MetricKey;
  label: string;
  value: number | null;
  unit: string;
  decimals: number;
  range: ValueRange;
  general: boolean;
  field: string;
  scale?: number;
}

function metricsOf(zone: ZoneConfig): Record<MetricKey, Metric> {
  const pick = (key: 'temp' | 'soil' | 'humidity' | 'lux', general: ValueRange, scale = 1): [ValueRange, boolean] => {
    const r = rangeOf(zone, key);
    return r ? [{ min: Math.round(r.min * scale * 10) / 10, max: Math.round(r.max * scale * 10) / 10 }, false] : [general, true];
  };
  const [t, tg] = pick('temp', GENERAL.temp);
  const [h, hg] = pick('humidity', GENERAL.air);
  const [s, sg] = pick('soil', GENERAL.soil);
  const [l, lg] = pick('lux', GENERAL.light, 1 / 1000);
  const v = (x: number | null) => (zone.live ? x : null);
  return {
    temp: { key: 'temp', label: '온도', value: v(zone.sensors.temperature), unit: '°C', decimals: 1, range: t, general: tg, field: 'temperature' },
    air: { key: 'air', label: '습도', value: v(zone.sensors.humidity), unit: '%', decimals: 0, range: h, general: hg, field: 'humidity' },
    soil: { key: 'soil', label: '흙 촉촉함', value: v(zone.sensors.soilMoisture), unit: '%', decimals: 0, range: s, general: sg, field: 'moisture_pct' },
    light: { key: 'light', label: '빛', value: v(zone.sensors.ledIntensityKlx), unit: 'klx', decimals: 1, range: l, general: lg, field: 'lux', scale: 1 / 1000 },
  };
}

const word = (m: Metric) => { const lv = levelOf(m.value, m.range); return lv === 'none' ? '—' : WORDS[m.key][lv]; };
const val = (m: Metric) => (m.value === null ? '—' : fmt(m.value, m.decimals));

// 리포트 결과(JSON)에서 사람이 읽을 요약 한두 줄을 찾는다
function reportSummary(result: unknown): string | null {
  if (typeof result === 'string') return result.trim() || null;
  if (!result || typeof result !== 'object') return null;
  const o = result as Record<string, unknown>;
  for (const k of ['summary', 'headline', 'overview', '요약', 'text', 'content']) {
    const x = o[k];
    if (typeof x === 'string' && x.trim()) return x.trim();
    if (x && typeof x === 'object') { const inner = reportSummary(x); if (inner) return inner; }
  }
  for (const x of Object.values(o)) if (typeof x === 'string' && x.trim().length > 20) return x.trim();
  return null;
}
const kstToday = () => new Date(Date.now() + 9 * 3600_000).toISOString().slice(0, 10);

export interface DeskDashboardProps {
  zones: ZoneConfig[];
  zoneId: string; // 주소에서 고른 큐브(없으면 첫 큐브)
  onSelectZone: (sn: string) => void;
  reported: Record<string, ActuatorState>;
  canControl: boolean;
  crops: CropProfile[];
  now: number;
  devMode: boolean;
  alertsCount: number;
  menu: AppMenuItem[]; // 기능 페이지(장치 연결 등)
  empty: EmptyState; // 큐브가 없을 때 보여 줄 안내
  devicesHref: string | null;
  userEmail: string | null;
  canSignOut: boolean;
  onSignOut: () => void;
  onEmergencyStop: () => void;
  onUpdate: (sn: string, updates: Partial<ActuatorState>) => void;
  onStartIrrigation: (sn: string) => void;
  onSetCrop: (sn: string, cropId: string | null) => void;
}

type DeviceKey = 'led' | 'inner' | 'outer' | 'water';
type PendingMap = Partial<Record<DeviceKey, { target: number | boolean; at: number }>>;
const reportedOf = (a: ActuatorState, k: DeviceKey): number | boolean =>
  k === 'led' ? (a.ledGrowLight ? a.ledIntensity : 0) : k === 'inner' ? (a.innerFan ? a.innerFanSpeed : 0) : k === 'outer' ? (a.outerFan ? a.outerFanSpeed : 0) : a.irrigationPump;

export const DeskDashboard: React.FC<DeskDashboardProps> = (p) => {
  const zone = p.zones.find((z) => z.id === p.zoneId) ?? p.zones[0] ?? null;
  const a = zone ? p.reported[zone.id] ?? DEFAULT_ACTUATOR : DEFAULT_ACTUATOR;
  const [view, setView] = useState<'cube' | 'analysis'>('cube');
  const [drawer, setDrawer] = useState(false);
  const [query, setQuery] = useState('');
  const [pending, setPending] = useState<PendingMap>({});
  const [waterAsk, setWaterAsk] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  // 큐브를 바꾸면 이전 큐브의 응답 대기·열린 창을 비운다
  useEffect(() => {
    setPending({});
    setWaterAsk(false);
    setDrawer(false);
  }, [zone?.id]);

  useEffect(() => {
    setPending((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const k of Object.keys(prev) as DeviceKey[]) if (prev[k] && reportedOf(a, k) === prev[k]!.target) { delete next[k]; changed = true; }
      return changed ? next : prev;
    });
  }, [a]);

  if (!zone) {
    return <div className="dk-root"><div className="dk-panel"><div className="dk-empty"><EmptyCubes state={p.empty} devicesHref={p.devicesHref} buttonClass="dk-empty-btn" /></div></div></div>;
  }
  const m = metricsOf(zone);
  const plant = zone.cropId ? zone.crop : '내 식물';
  const anyGeneral = Object.values(m).some((x) => x.general);
  const off = Object.values(m).filter((x) => { const lv = levelOf(x.value, x.range); return lv === 'low' || lv === 'high'; });
  // 부제는 한 줄만: 큐브 번호 · 연결 상태 · 마지막 수신. 범위 밖 값은 콜아웃이 주황으로 알려 준다.
  const subtitle = `${zone.code} · ${zone.live ? `연결됨 · ${relativeTime(zone.lastSeen, p.now)}` : `연결 끊김 · 마지막 수신 ${zone.lastSeen ? relativeTime(zone.lastSeen, p.now) : '—'}`}`;
  const q = query.trim();
  const hit = (...words: string[]) => q.length > 0 && words.some((w) => w.includes(q) || q.includes(w));

  const send = (k: DeviceKey, target: number | boolean, updates: Partial<ActuatorState>) => {
    setPending((prev) => ({ ...prev, [k]: { target, at: Date.now() } }));
    p.onUpdate(zone.id, updates);
  };
  const pendState = (k: DeviceKey): 'waiting' | 'late' | null => {
    const x = pending[k];
    if (!x) return null;
    return Date.now() - x.at < RESPONSE_TIMEOUT_MS ? 'waiting' : 'late';
  };

  return (
    <div className="dk-root">
      <div className="dk-panel">
        <TopBar {...p} zone={zone} view={view} onView={setView} query={query} onQuery={setQuery}
          onSearch={() => { if (hit('조명', '팬', '바람', '급수', '물', '환기', '조작')) setDrawer(true); }} />
        <div className="dk-body">
          <Rail view={view} onView={setView} onControls={() => setDrawer(true)} onMenu={() => setMenuOpen(true)} canSignOut={p.canSignOut} onSignOut={p.onSignOut} />
          <main className="dk-main">
            {view === 'cube'
              ? <Hero zone={zone} a={a} m={m} plant={plant} subtitle={subtitle} anyGeneral={anyGeneral} hit={hit} off={off} onPickCrop={() => setDrawer(true)}
                quick={<QuickRow zone={zone} a={a} canControl={p.canControl} send={send} pendState={pendState} onWater={() => setWaterAsk(true)} onMore={() => setDrawer(true)} />} />
              : <Analysis zone={zone} m={m} />}
            <aside className="dk-cards">
              <TrendCard zone={zone} m={m} />
              <StatusCard zone={zone} a={a} m={m} onOpen={() => setDrawer(true)} />
              <ReportCard zone={zone} />
            </aside>
          </main>
        </div>
      </div>
      {waterAsk && (
        <div className="dk-drawer-back" onClick={() => setWaterAsk(false)}>
          <div className="dk-confirm dk-confirm-center" role="alertdialog" aria-modal="true" aria-labelledby="dk-qconfirm-title" onClick={(e) => e.stopPropagation()}>
            <h3 id="dk-qconfirm-title">물을 줄까요?</h3>
            <p>아래 물통의 물을 화분 밑 트레이로 올려요. 큐브 안 모든 화분에 함께 물이 가요.{zone.device?.pumpMaxMs ? ` 펌프는 최대 ${Math.round(zone.device.pumpMaxMs / 1000)}초 뒤 자동으로 멈춰요.` : ' 펌프는 정해진 시간이 지나면 자동으로 멈춰요.'}</p>
            <div className="dk-confirm-btns">
              <button type="button" className="dk-btn-ghost" onClick={() => setWaterAsk(false)}>취소</button>
              <button type="button" className="dk-water" onClick={() => { setWaterAsk(false); setPending((prev) => ({ ...prev, water: { target: true, at: Date.now() } })); p.onStartIrrigation(zone.id); }}>물 주기</button>
            </div>
          </div>
        </div>
      )}
      {menuOpen && <MenuDrawer items={p.menu} onClose={() => setMenuOpen(false)} />}
      {drawer && (
        <Drawer zone={zone} a={a} canControl={p.canControl} crops={p.crops} cropAssigned={Boolean(zone.cropId)}
          onClose={() => setDrawer(false)} send={send} pendState={pendState}
          onToggleAuto={() => p.onUpdate(zone.id, { autoMode: !a.autoMode })}
          onWater={() => { setPending((prev) => ({ ...prev, water: { target: true, at: Date.now() } })); p.onStartIrrigation(zone.id); }}
          onSetCrop={(id) => p.onSetCrop(zone.id, id)} />
      )}
    </div>
  );
};

// ---------- 상단 바 ----------
const TopBar: React.FC<DeskDashboardProps & { zone: ZoneConfig; view: 'cube' | 'analysis'; onView: (v: 'cube' | 'analysis') => void; query: string; onQuery: (q: string) => void; onSearch: () => void }> = ({
  view, onView, query, onQuery, onSearch, alertsCount, menu, userEmail, onEmergencyStop, canControl, zones, zone, onSelectZone,
}) => {
  const logsHref = menu.find((x) => x.key === 'logs')?.to;
  return (
  <header className="dk-top">
    <span className="dk-logo" aria-label="스마트큐브"><Sprout aria-hidden="true" /></span>
    <div className="dk-tabs" role="tablist" aria-label="화면">
      <button type="button" role="tab" aria-selected={view === 'analysis'} className={view === 'analysis' ? 'is-on' : ''} onClick={() => onView('analysis')}>
        {view === 'analysis' && <span className="dk-tab-dot" aria-hidden="true" />}분석
      </button>
      <button type="button" role="tab" aria-selected={view === 'cube'} className={view === 'cube' ? 'is-on' : ''} onClick={() => onView('cube')}>
        {view === 'cube' && <span className="dk-tab-dot" aria-hidden="true" />}내 큐브
      </button>
    </div>
    {/* 큐브가 두 대 이상일 때만 큐브를 고른다(2026-11-25) */}
    {zones.length > 1 && (
      <label className="dk-select dk-cube-select">
        <span className="sr-only">큐브 고르기</span>
        <select value={zone.id} onChange={(e) => onSelectZone(e.target.value)}>
          {zones.map((z) => <option key={z.id} value={z.id}>{z.code}{z.cropId ? ` · ${z.crop}` : ''}{z.live ? '' : ' (연결 끊김)'}</option>)}
        </select>
        <ChevronDown aria-hidden="true" />
      </label>
    )}
    <div className="dk-top-right">
      <form className="dk-search" role="search" onSubmit={(e) => { e.preventDefault(); onSearch(); }}>
        <input value={query} onChange={(e) => onQuery(e.target.value)} placeholder="온도, 흙, 조명… 찾기" aria-label="큐브 항목 찾기" />
        <button type="submit" aria-label="찾기"><Search aria-hidden="true" /></button>
      </form>
      {logsHref && (
        <Link className="dk-circle" to={logsHref} aria-label={`알림 이력${alertsCount ? `, 확인할 알림 ${alertsCount}건` : ''}`}>
          <Bell aria-hidden="true" />{alertsCount > 0 && <span className="dk-badge">{alertsCount}</span>}
        </Link>
      )}
      <span className="dk-circle dk-avatar" title={userEmail ?? '로그인 없이 보는 중'} aria-label="내 정보">{(userEmail ?? '나').slice(0, 1).toUpperCase()}</span>
      <button type="button" className="dk-stop" onClick={onEmergencyStop} disabled={!canControl} title={canControl ? undefined : '조작 권한이 없어요'}>긴급 정지</button>
    </div>
  </header>
  );
};

// ---------- 메뉴: 기능 페이지(장치 연결 등)로 ----------
const MenuDrawer: React.FC<{ items: AppMenuItem[]; onClose: () => void }> = ({ items, onClose }) => {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="dk-drawer-back" onClick={onClose}>
      <aside className="dk-drawer" role="dialog" aria-modal="true" aria-labelledby="dk-menu-title" onClick={(e) => e.stopPropagation()}>
        <div className="dk-card-head">
          <h2 id="dk-menu-title">메뉴</h2>
          <button type="button" className="dk-round" onClick={onClose} aria-label="닫기"><X aria-hidden="true" /></button>
        </div>
        {items.map((x) => <Link key={x.key} className="dk-menu-item" to={x.to}><b>{x.label}</b><small>{x.sub}</small><ChevronRight aria-hidden="true" /></Link>)}
      </aside>
    </div>
  );
};

// ---------- 왼쪽 아이콘 레일 (640px 미만에서는 하단 탭 바) ----------
const Rail: React.FC<{ view: 'cube' | 'analysis'; onView: (v: 'cube' | 'analysis') => void; onControls: () => void; onMenu: () => void; canSignOut: boolean; onSignOut: () => void }> = ({
  view, onView, onControls, onMenu, canSignOut, onSignOut,
}) => (
  <nav className="dk-rail" aria-label="주 메뉴">
    <button type="button" className={`dk-rail-btn ${view === 'cube' ? 'is-on' : ''}`} aria-current={view === 'cube' ? 'page' : undefined} onClick={() => onView('cube')}><Leaf aria-hidden="true" /><span>내 큐브</span></button>
    <button type="button" className={`dk-rail-btn ${view === 'analysis' ? 'is-on' : ''}`} aria-current={view === 'analysis' ? 'page' : undefined} onClick={() => onView('analysis')}><LineChart aria-hidden="true" /><span>기록</span></button>
    <button type="button" className="dk-rail-btn" onClick={onControls}><SlidersHorizontal aria-hidden="true" /><span>조작</span></button>
    <button type="button" className="dk-rail-btn" onClick={onMenu} title="메뉴"><Menu aria-hidden="true" /><span>메뉴</span></button>
    {canSignOut && <button type="button" className="dk-rail-btn dk-rail-out" onClick={onSignOut} aria-label="로그아웃"><LogOut aria-hidden="true" /><span>로그아웃</span></button>}
  </nav>
);

// ---------- 히어로: 큐브 그림 + 돋보기 렌즈 + 콜아웃 ----------
// 글씨 규칙(모바일 앱과 같음): 이름 12px 회색 / 값 20px 검정, 상태어는 값 옆 같은 줄. 범위 밖 값만 주황.
// 라벨은 좌우로 나눠 균형 있게: 왼쪽 빛·급수, 오른쪽 온도·습도·흙 촉촉함, 위 가운데 카메라.
interface Callout { key: string; label: string; value: string; warn?: boolean; dot: [number, number]; at: [number, number]; side: 'l' | 'r' | 'c'; words: string[] }

const isOffM = (x: Metric) => { const lv = levelOf(x.value, x.range); return lv === 'low' || lv === 'high'; };
const withWord = (x: Metric, unit: string) => (x.value === null ? '—' : `${val(x)}${unit} ${word(x)}`);

const Hero: React.FC<{ zone: ZoneConfig; a: ActuatorState; m: Record<MetricKey, Metric>; plant: string; subtitle: string; anyGeneral: boolean; hit: (...w: string[]) => boolean; off: Metric[]; onPickCrop: () => void; quick: React.ReactNode }> = ({
  zone, a, m, plant, subtitle, anyGeneral, hit, off, onPickCrop, quick,
}) => {
  const lensUrl = useSnapshot(zone.id);
  const live = zone.live;
  const scene = { live, led: reportedOf(a, 'led') as number, inner: reportedOf(a, 'inner') as number, outer: reportedOf(a, 'outer') as number, pump: a.irrigationPump };
  const sceneLabel = !live ? `${zone.code} 큐브 그림. 연결 끊김` : `${zone.code} 큐브 그림. 조명 ${scene.led > 0 ? `켜짐 ${scene.led}%` : '꺼짐'}, 순환팬 ${FAN_WORDS[scene.inner]}, 배기팬 ${FAN_WORDS[scene.outer]}, ${scene.pump ? '물 올리는 중' : '물 대기'}`;
  const tempAir = m.temp.value === null && m.air.value === null ? '—'
    : isOffM(m.temp) ? withWord(m.temp, '°C') : isOffM(m.air) ? withWord(m.air, '%')
      : `${m.temp.value === null ? '—' : `${val(m.temp)}°C`} · ${m.air.value === null ? '—' : `${val(m.air)}%`}`;
  const callouts: Callout[] = [
    { key: 'light', label: '빛', value: withWord(m.light, ' klx'), warn: isOffM(m.light), dot: [cx(470), cy(214)], at: [250, 70], side: 'l', words: ['빛', '조도', '밝기'] },
    { key: 'water', label: '급수', value: !live ? '—' : a.irrigationPump ? '물 올리는 중' : '저면관수 · 대기', dot: [cx(640), cy(1085)], at: [250, 580], side: 'l', words: ['급수', '물'] },
    { key: 'air', label: '온도 · 습도', value: tempAir, warn: isOffM(m.temp) || isOffM(m.air), dot: [cx(1110), cy(450)], at: [848, 170], side: 'r', words: ['온도', '습도', '덥', '춥', '공기'] },
    { key: 'soil', label: '흙 촉촉함', value: withWord(m.soil, '%'), warn: isOffM(m.soil), dot: [cx(668), cy(748)], at: [848, 420], side: 'r', words: ['흙', '수분', '촉촉'] },
  ];
  // 선 끝: 왼쪽 라벨은 오른쪽 끝, 오른쪽 라벨은 왼쪽 끝, 가운데 라벨은 아래 가운데
  const end = (c: Callout): [number, number] => (c.side === 'c' ? [c.at[0], c.at[1] + 30] : [c.at[0] + (c.side === 'r' ? -10 : 10), c.at[1] + 24]);
  const leafAlert = live && off.length > 0;
  const leafValue = !live || (Object.values(m) as Metric[]).every((x) => x.value === null) ? '—' : leafAlert ? (off.length > 1 ? '주의 · 살펴봐 주세요' : `주의 · ${word(off[0])}`) : '건강해요';
  return (
    <section className="dk-hero" aria-labelledby="dk-hero-title">
      <div className="dk-hero-head">
        <div className="dk-hero-row">
          <h1 id="dk-hero-title">{plant} <span>건강 상태</span></h1>
          {anyGeneral && <button type="button" className="dk-crop-chip" onClick={onPickCrop} title="식물을 고르면 좋아하는 범위가 더 정확해져요. 지금은 일반 식물 기준이에요.">작물 고르기 →</button>}
        </div>
        <p>{subtitle}</p>
      </div>
      {!live && <div className="dk-offline" role="alert"><WifiOff aria-hidden="true" />큐브와 연락이 안 돼요 · 전원과 와이파이를 확인해 주세요</div>}
      <div className={`dk-canvas ${live ? '' : 'is-off'}`}>
        <svg viewBox="0 0 1000 700" className="dk-canvas-svg" aria-hidden="true">
          <ellipse cx={cx(725)} cy={cy(1214) + 6} rx="230" ry="14" fill="#1c211f" opacity=".1" />
          {/* 모바일 앱과 같은 시뮬레이션 그림: 제어함 없음, 아래 칸 물통·펌프·호스, 조명·팬·물이 보고 상태를 따라 움직인다 */}
          <CubeScene s={scene} label={sceneLabel} box={{ x: cx(SCENE.x), y: cy(SCENE.y), w: SCENE.w * C.s, h: SCENE.h * C.s }} />
          {callouts.map((c) => {
            const [ex, ey] = end(c);
            const d = c.side === 'c' ? `M${c.dot[0]} ${c.dot[1]}V${ey + 14}H${ex}V${ey}` : `M${c.dot[0]} ${c.dot[1]}L${(c.dot[0] + ex) / 2} ${ey}H${ex}`;
            return (
              <g key={c.key} className={`dk-co-line ${hit(...c.words) ? 'is-hit' : ''} ${c.warn ? 'is-warn' : ''}`}>
                <path d={d} />
                <circle cx={c.dot[0]} cy={c.dot[1]} r="5" />
              </g>
            );
          })}
          <g className={`dk-co-line ${leafAlert ? 'is-alert' : ''}`}>
            <path d={`M370 334L${cx(560)} ${cy(640)}`} />
            <circle cx={cx(560)} cy={cy(640)} r="5" />
          </g>
        </svg>
        {/* 돋보기 렌즈: 최근 촬영 사진, 없으면 큐브 그림의 화분 부분 확대. 식물에 이상이 있으면 빨간 불이 깜빡인다 */}
        <div className={`dk-lens ${leafAlert ? 'is-alert' : ''}`} role="img" aria-label={lensUrl ? '카메라가 찍은 최근 사진 확대' : '큐브 그림의 화분 부분 확대'}
          style={lensUrl ? { backgroundImage: `url(${lensUrl})`, backgroundSize: 'cover', backgroundPosition: 'center' } : undefined}>
          {!lensUrl && <span className="dk-lens-crop" />}
        </div>
        {leafAlert && <span className="dk-lens-lamp" aria-hidden="true" />}
        <div className={`dk-co dk-co-leaf ${leafAlert ? 'is-alert' : ''}`} style={{ left: '15%', top: '66%' }} role={leafAlert ? 'status' : undefined}>
          <small>잎 상태</small>
          <b>{leafValue}</b>
        </div>
        {callouts.map((c) => (
          <div key={c.key} className={`dk-co dk-co-${c.side} ${hit(...c.words) ? 'is-hit' : ''} ${c.warn ? 'is-warn' : ''}`} style={{ left: `${c.at[0] / 10}%`, top: `${c.at[1] / 7}%` }}>
            {c.label && <small>{c.label}</small>}
            <b>{c.value}</b>
          </div>
        ))}
      </div>
      {quick}
      {/* 1024px 미만: 콜아웃 대신 2열 카드 */}
      <div className="dk-co-cards">
        {callouts.map((c) => (
          <div key={c.key} className={`dk-co-card ${hit(...c.words) ? 'is-hit' : ''} ${c.warn ? 'is-warn' : ''}`}>
            {c.label && <small>{c.label}</small>}<b>{c.value}</b>
          </div>
        ))}
      </div>
    </section>
  );
};

const QBtn: React.FC<{ st: 'waiting' | 'late' | null; lock: boolean; icon: React.ElementType; on: boolean; label: string; small: string; onClick: () => void }> = ({ st, lock, icon: Icon, on, label, small, onClick }) => (
  <button type="button" className={`dk-qbtn ${on ? 'is-on' : ''} ${st ? `is-${st}` : ''}`} disabled={lock} aria-pressed={on} onClick={onClick}>
    <span className="dk-qicon" aria-hidden="true"><Icon />{st === 'late' && <i />}</span>
    <span className="dk-qtext"><b>{label}</b><small>{small}</small></span>
  </button>
);

// ---------- 그림 아래 바로가기: 조명 · 물 주기 · 환기 (모바일 앱과 같은 동작) ----------
// 켜짐 = 진한 초록 원, 응답 대기 = 도는 링, 응답 없음 = 주황 점. 표시는 장치가 보고한 상태를 따른다.
const QuickRow: React.FC<{ zone: ZoneConfig; a: ActuatorState; canControl: boolean; send: (k: DeviceKey, target: number | boolean, updates: Partial<ActuatorState>) => void; pendState: (k: DeviceKey) => 'waiting' | 'late' | null; onWater: () => void; onMore: () => void }> = ({
  zone, a, canControl, send, pendState, onWater, onMore,
}) => {
  const live = zone.live;
  const lock = !live ? '큐브와 연락이 안 돼서 지금은 조작할 수 없어요.' : !canControl ? '보기 전용이에요. 큐브 주인만 조작할 수 있어요.' : a.autoMode ? '자동 돌보기 중이에요. 직접 조절하려면 조작 서랍에서 자동 돌보기를 꺼 주세요.' : null;
  const led = reportedOf(a, 'led') as number;
  const outer = reportedOf(a, 'outer') as number;
  const lit = live && led > 0;
  const venting = live && outer > 0;
  const pumping = live && a.irrigationPump;
  const onLevel = a.lastLedIntensity && a.lastLedIntensity > 0 ? a.lastLedIntensity : 70;
  const sub = (k: DeviceKey, idle: string) => { const st = pendState(k); return st === 'waiting' ? '응답 기다리는 중' : st === 'late' ? '응답 없음' : live ? idle : '상태 알 수 없음'; };
  return (
    <div className="dk-quick-wrap">
      <div className="dk-quick" role="group" aria-label="바로 하기">
        <QBtn st={pendState('led')} lock={lock !== null} icon={Lightbulb} on={lit} label={lit ? '조명 끄기' : '조명 켜기'} small={sub('led', lit ? `지금 ${led}%` : '지금 꺼짐')}
          onClick={() => send('led', lit ? 0 : onLevel, lit ? { ledGrowLight: false, ledIntensity: 0 } : { ledGrowLight: true, ledIntensity: onLevel })} />
        <QBtn st={pendState('water')} lock={lock !== null} icon={Droplets} on={pumping} label={pumping ? '물 멈추기' : '물 주기'} small={sub('water', pumping ? '물 올리는 중' : '물통에서 트레이로')}
          onClick={() => (pumping ? send('water', false, { irrigationPump: false, nutrientValve: false }) : onWater())} />
        <QBtn st={pendState('outer')} lock={lock !== null} icon={Wind} on={venting} label={venting ? '환기 끄기' : '환기 켜기'} small={sub('outer', venting ? `배기팬 ${FAN_WORDS[outer]}` : '지금 꺼짐')}
          onClick={() => (venting ? send('outer', 0, { outerFan: false, outerFanSpeed: 0 }) : send('outer', 2, { outerFan: true, outerFanSpeed: 2 }))} />
        <button type="button" className="dk-qmore" onClick={onMore}><SlidersHorizontal aria-hidden="true" />자세히 조절</button>
      </div>
      {lock && <p className="dk-qlock">{lock}</p>}
    </div>
  );
};

function useSnapshot(sn: string): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    let made: string | null = null;
    getSnapshots(sn, { limit: 1 })
      .then(async (list) => {
        const row = list.rows[0];
        if (!row) return;
        const blob = await getSnapshotBlob(sn, row.camera, row.date, row.file);
        if (!alive) return;
        made = URL.createObjectURL(blob);
        setUrl(made);
      })
      .catch(() => undefined); // 사진이 없으면 그림 확대로 대신한다
    return () => { alive = false; if (made) URL.revokeObjectURL(made); };
  }, [sn]);
  return url;
}

// ---------- 분석: 항목별 7일 추이 (EC·CO₂·pH 는 2026-11-25 추가, pH 는 지금 값만) ----------
const Analysis: React.FC<{ zone: ZoneConfig; m: Record<MetricKey, Metric> }> = ({ zone, m }) => (
  <section className="dk-hero dk-analysis" aria-labelledby="dk-an-title">
    <div className="dk-hero-head">
      <h1 id="dk-an-title">지난 7일 <span>기록</span></h1>
      <p>큐브가 실제로 잰 값만 그래프로 보여 줘요. 초록 띠는 좋아하는 범위예요.</p>
    </div>
    <div className="dk-an-grid">
      {(['temp', 'air', 'soil', 'light'] as MetricKey[]).map((k) => (
        <div key={k} className="dk-an-card">
          <div className="dk-an-head"><b>{m[k].label}</b><span>지금 {val(m[k])}{m[k].value === null ? '' : m[k].unit}</span></div>
          <AreaChart sn={zone.id} metric={m[k]} start="-7d" every="2h" variant="light" />
        </div>
      ))}
      {extraMetricsOf(zone).map((x) => (
        <div key={x.key} className="dk-an-card">
          <div className="dk-an-head"><b>{x.label}</b><span>지금 {extraText(x)}</span></div>
          {hasChart(x) && <AreaChart sn={zone.id} metric={x} start="-7d" every="2h" variant="light" />}
        </div>
      ))}
    </div>
  </section>
);

// ---------- 부드러운 영역 차트 ----------
function useHistory(sn: string, metric: ChartMetric, start: string, every: string) {
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

const AreaChart: React.FC<{ sn: string; metric: ChartMetric; start: string; every: string; variant: 'lime' | 'light' }> = ({ sn, metric, start, every, variant }) => {
  const { points, error } = useHistory(sn, metric, start, every);
  const [pick, setPick] = useState<number | null>(null);
  const W = 300, H = 120, PAD = 6;
  const shape = useMemo(() => {
    if (!points || points.length < 2) return null;
    const t0 = points[0].t, t1 = points[points.length - 1].t;
    const vs = points.map((p) => p.v);
    // 범위가 없는 항목(EC·CO₂)은 값만으로 축을 잡고 띠를 그리지 않는다
    const bounds = metric.range ? [metric.range.min, metric.range.max] : [];
    const lo = Math.min(...vs, ...bounds), hi = Math.max(...vs, ...bounds);
    const span = hi - lo || 1;
    const X = (t: number) => PAD + ((t - t0) / (t1 - t0 || 1)) * (W - PAD * 2);
    const Y = (v: number) => H - 18 - ((v - lo) / span) * (H - 34);
    const xy = points.map((p) => [X(p.t), Y(p.v)] as [number, number]);
    // 부드러운 곡선. 기록이 빈 구간에서는 끊는다(chartPath.ts)
    const { line: d, area } = chartPaths(xy, points.map((p) => p.t), everyMs(every), H - 18);
    const band = metric.range ? { y1: Y(metric.range.max), y2: Y(metric.range.min) } : null;
    return { d, area, xy, band, t0, t1 };
  }, [points, metric.range?.min, metric.range?.max, every]);

  if (error) return <p className="dk-chart-msg">기록을 불러오지 못했어요.</p>;
  if (!points) return <p className="dk-chart-msg">기록을 불러오는 중…</p>;
  if (!shape) return <p className="dk-chart-msg">아직 그래프로 보여 줄 기록이 없어요.</p>;
  const i = pick ?? shape.xy.length - 1;
  const [px, py] = shape.xy[i];
  const diff = points[i].v - points[0].v;
  const diffText = `${diff >= 0 ? '+' : ''}${diff.toFixed(metric.decimals)}${metric.unit === '°C' ? '°C' : metric.unit === '%' ? '%p' : metric.unit}`;
  const tick = (t: number) => (start === '-24h'
    ? new Date(t).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false })
    : new Date(t).toLocaleDateString('ko-KR', { month: 'numeric', day: 'numeric' }));
  return (
    <div className={`dk-chart dk-chart-${variant}`}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img"
        aria-label={`${metric.label} ${start === '-24h' ? '24시간' : '기간'} 추이, 처음보다 ${diffText}`}
        onPointerMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const x = ((e.clientX - r.left) / r.width) * W;
          let best = 0;
          shape.xy.forEach(([qx], j) => { if (Math.abs(qx - x) < Math.abs(shape.xy[best][0] - x)) best = j; });
          setPick(best);
        }}
        onPointerLeave={() => setPick(null)}>
        <defs>
          <linearGradient id={`dk-fill-${variant}-${metric.key}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#3e8a4c" stopOpacity=".18" />
            <stop offset="1" stopColor="#3e8a4c" stopOpacity="0" />
          </linearGradient>
        </defs>
        {shape.band && <rect x="0" y={shape.band.y1} width={W} height={Math.max(0, shape.band.y2 - shape.band.y1)} className="dk-chart-band" />}
        <path d={shape.area} fill={`url(#dk-fill-${variant}-${metric.key})`} />
        <path d={shape.d} className="dk-chart-line" strokeLinecap="round" />
        <line x1={px} x2={px} y1="8" y2={H - 18} className="dk-chart-guide" />
        <circle cx={px} cy={py} r="4.5" className="dk-chart-dot" />
        <text x={PAD} y={H - 4} className="dk-chart-tick">{tick(shape.t0)}</text>
        <text x={W - PAD} y={H - 4} textAnchor="end" className="dk-chart-tick">{tick(shape.t1)}</text>
      </svg>
      <span className="dk-chart-pill" style={{ left: `${Math.min(80, Math.max(20, (px / W) * 100))}%`, top: `${(py / H) * 100}%` }}>
        {diffText} <small>{tick(points[i].t)}</small>
      </span>
    </div>
  );
};

// ---------- 오른쪽 카드 a: 생육 추이 ----------
const PERIODS = [
  { key: '-24h', label: '24시간', every: '15m' },
  { key: '-7d', label: '7일', every: '2h' },
  { key: '-30d', label: '전체(30일)', every: '6h' },
];

const TrendCard: React.FC<{ zone: ZoneConfig; m: Record<MetricKey, Metric> }> = ({ zone, m }) => {
  const [period, setPeriod] = useState(PERIODS[0]);
  const [key, setKey] = useState<'soil' | 'temp' | 'air'>('soil');
  return (
    <section className="dk-card dk-trend" aria-labelledby="dk-trend-title">
      <div className="dk-card-head">
        <h2 id="dk-trend-title">생육 추이</h2>
        <label className="dk-select">
          <span className="sr-only">기간</span>
          <select value={period.key} onChange={(e) => setPeriod(PERIODS.find((x) => x.key === e.target.value) ?? PERIODS[0])}>
            {PERIODS.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
          </select>
          <ChevronDown aria-hidden="true" />
        </label>
      </div>
      <div className="dk-trend-keys" role="radiogroup" aria-label="볼 항목">
        {(['soil', 'temp', 'air'] as const).map((k) => (
          <button key={k} type="button" role="radio" aria-checked={key === k} className={key === k ? 'is-on' : ''} onClick={() => setKey(k)}>{m[k].label}</button>
        ))}
      </div>
      <AreaChart sn={zone.id} metric={m[key]} start={period.key} every={period.every} variant="lime" />
      <p className="dk-trend-read">
        <b className={isOffM(m[key]) ? 'is-warn' : ''}>{withWord(m[key], m[key].unit)}</b>
        <small>지금 · 좋아하는 범위 {m[key].range.min}~{m[key].range.max}{m[key].unit}</small>
      </p>
    </section>
  );
};

// ---------- 오른쪽 카드 b: 큐브 상태 ----------
const StatusCard: React.FC<{ zone: ZoneConfig; a: ActuatorState; m: Record<MetricKey, Metric>; onOpen: () => void }> = ({ zone, a, m, onOpen }) => {
  const live = zone.live;
  const soilLv = levelOf(m.soil.value, m.soil.range);
  const airLv = levelOf(m.air.value, m.air.range);
  const tiles = [
    { icon: Lightbulb, name: '조명', value: !live ? '—' : a.ledGrowLight && a.ledIntensity > 0 ? `켜짐 ${a.ledIntensity}%` : '꺼짐', warn: false },
    { icon: Droplets, name: '흙 상태', value: word(m.soil), warn: soilLv === 'low' || soilLv === 'high' },
    { icon: Waves, name: '습도', value: m.air.value === null ? '—' : `${val(m.air)}%`, warn: airLv === 'low' || airLv === 'high' },
    { icon: Sprout, name: '급수', value: !live ? '—' : a.irrigationPump ? '물 주는 중' : '대기', warn: false },
  ];
  return (
    <section className="dk-card dk-status" aria-labelledby="dk-status-title">
      <div className="dk-card-head">
        <h2 id="dk-status-title">큐브 상태 <span>· {live ? '실시간' : '연결 끊김'}</span></h2>
        <button type="button" className="dk-round" onClick={onOpen} aria-label="조작 서랍 열기"><ArrowUpRight aria-hidden="true" /></button>
      </div>
      <div className="dk-tiles">
        {tiles.map(({ icon: Icon, name, value, warn }) => (
          <div key={name} className={`dk-tile ${warn ? 'is-warn' : ''}`}>
            <Icon aria-hidden="true" />
            <small>{name}</small>
            <b>{value}</b>
          </div>
        ))}
      </div>
    </section>
  );
};

// ---------- 오른쪽 카드 c: 오늘의 생육 리포트 ----------
const ReportCard: React.FC<{ zone: ZoneConfig }> = ({ zone }) => {
  const [state, setState] = useState<'loading' | 'none' | 'ok' | 'error'>('loading');
  const [text, setText] = useState('');
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const startedAt = useRef(0);
  const canSpeak = typeof window !== 'undefined' && 'speechSynthesis' in window;

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const list = await getReports(zone.id, 20);
        const today = kstToday();
        const job = list.reports.find((r: ReportJob) => r.status === 'done' && r.date === today);
        if (!job) { if (alive) setState('none'); return; }
        const full = await getReport(zone.id, job.job_id);
        const s = reportSummary(full.result);
        if (!alive) return;
        if (s) { setText(s); setState('ok'); } else setState('none');
      } catch {
        if (alive) setState('none'); // 리포트 기능이 없거나 실패하면 "아직 없어요"로 보인다
      }
    })();
    return () => { alive = false; window.speechSynthesis?.cancel(); };
  }, [zone.id]);

  const seconds = Math.max(5, Math.round(text.length / 6)); // 한국어 읽기 속도(초당 약 6자)로 어림한 길이
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
  const BARS = 34;
  return (
    <section className="dk-card dk-report" aria-labelledby="dk-report-title">
      <div className="dk-card-head">
        <h2 id="dk-report-title">오늘의 생육 리포트</h2>
        <button type="button" className="dk-play" onClick={toggle} disabled={state !== 'ok' || !canSpeak} aria-label={playing ? '읽기 멈추기' : '리포트 읽어 주기'}>
          {playing ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}
        </button>
      </div>
      <p className={`dk-report-text ${state === 'ok' ? '' : 'is-empty'}`}>
        {state === 'loading' ? '리포트를 확인하는 중…' : state === 'ok' ? text : '아직 오늘 리포트가 없어요'}
      </p>
      {/* 재생 진행 표시(소리 크기가 아니라 읽은 만큼 막대가 밝아진다) */}
      <div className="dk-wave" aria-hidden="true">
        {Array.from({ length: BARS }, (_, j) => (
          <span key={j} className={j / BARS < progress ? 'is-done' : ''} style={{ height: `${30 + ((j * 37) % 70)}%` }} />
        ))}
      </div>
      <p className="dk-report-meta">1x · {state === 'ok' ? `${mmss(Math.round(progress * seconds))} / 약 ${mmss(seconds)}` : '—'}</p>
    </section>
  );
};

// ---------- 조작 서랍 ----------
const Drawer: React.FC<{
  zone: ZoneConfig; a: ActuatorState; canControl: boolean; crops: CropProfile[]; cropAssigned: boolean;
  onClose: () => void; send: (k: DeviceKey, target: number | boolean, updates: Partial<ActuatorState>) => void;
  pendState: (k: DeviceKey) => 'waiting' | 'late' | null; onToggleAuto: () => void; onWater: () => void; onSetCrop: (id: string | null) => void;
}> = ({ zone, a, canControl, crops, cropAssigned, onClose, send, pendState, onToggleAuto, onWater, onSetCrop }) => {
  const live = zone.live;
  const locked = !canControl || a.autoMode || !live;
  const [led, setLed] = useState(a.ledGrowLight ? a.ledIntensity : 0);
  const [dragging, setDragging] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const shownLed = a.ledGrowLight ? a.ledIntensity : 0;
  useEffect(() => { if (!dragging) setLed(shownLed); }, [shownLed, dragging]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  const pumpMax = zone.device?.pumpMaxMs ? Math.round(zone.device.pumpMaxMs / 1000) : null;
  const Pend: React.FC<{ k: DeviceKey }> = ({ k }) => {
    const s = pendState(k);
    if (s === 'waiting') return <p className="dk-pend"><Loader2 className="dk-spin" aria-hidden="true" />장치 응답을 기다리는 중</p>;
    if (s === 'late') return <p className="dk-pend is-late">장치가 아직 응답하지 않았어요 · 표시는 장치가 보고한 상태예요</p>;
    return null;
  };
  return (
    <div className="dk-drawer-back" onClick={onClose}>
      <aside className="dk-drawer" role="dialog" aria-modal="true" aria-labelledby="dk-drawer-title" onClick={(e) => e.stopPropagation()}>
        <div className="dk-card-head">
          <h2 id="dk-drawer-title">큐브 조작 <span>· 큐브 전체에 적용</span></h2>
          <button type="button" className="dk-round" onClick={onClose} aria-label="닫기"><X aria-hidden="true" /></button>
        </div>
        <p className="dk-drawer-note">
          {!live ? '큐브와 연락이 안 돼요. 연결되면 조작할 수 있어요.'
            : !canControl ? '보기 전용이에요. 큐브 주인만 조작할 수 있어요.'
              : a.autoMode ? '자동 돌보기 중이에요. 직접 조작하려면 아래에서 끄세요.'
                : '누르면 큐브로 바로 보내요. 표시는 장치가 보고한 상태를 따라가요.'}
        </p>

        <fieldset className="dk-ctl" disabled={locked}>
          <div className="dk-ctl-row">
            <span className="dk-ctl-name"><Lightbulb aria-hidden="true" />조명 <b>{live ? (shownLed > 0 ? `켜짐 ${shownLed}%` : '꺼짐') : '—'}</b></span>
            <button type="button" role="switch" aria-checked={shownLed > 0} className={`dk-switch ${shownLed > 0 ? 'is-on' : ''}`}
              onClick={() => send('led', shownLed > 0 ? 0 : 70, shownLed > 0 ? { ledGrowLight: false, ledIntensity: 0 } : { ledGrowLight: true, ledIntensity: 70 })}>
              <span aria-hidden="true" /><span className="sr-only">조명 {shownLed > 0 ? '끄기' : '켜기'}</span>
            </button>
          </div>
          <label className="dk-slider">
            <span>밝기 {led}%</span>
            <input type="range" min="0" max="100" step="5" value={led}
              onPointerDown={() => setDragging(true)}
              onPointerUp={() => setDragging(false)}
              onChange={(e) => {
                const v = Number(e.target.value);
                setLed(v);
                send('led', v, v > 0 ? { ledGrowLight: true, ledIntensity: v } : { ledGrowLight: false, ledIntensity: 0 });
              }} />
          </label>
          <Pend k="led" />

          {(['inner', 'outer'] as const).map((k) => {
            const now = reportedOf(a, k) as number;
            return (
              <div key={k} className="dk-ctl-block">
                <span className="dk-ctl-name">{k === 'inner' ? <Wind aria-hidden="true" /> : <Fan aria-hidden="true" />}{k === 'inner' ? '순환팬 · 안에서 돌려요' : '환기팬 · 밖으로 내보내요'} <b>{live ? FAN_WORDS[now] : '—'}</b></span>
                <div className="dk-pills">
                  {FAN_WORDS.map((w, n) => (
                    <button key={w} type="button" aria-pressed={live && now === n} className={live && now === n ? 'is-on' : ''}
                      onClick={() => send(k, n, k === 'inner' ? { innerFan: n > 0, innerFanSpeed: n } : { outerFan: n > 0, outerFanSpeed: n })}>{w}</button>
                  ))}
                </div>
                <Pend k={k} />
              </div>
            );
          })}

          <div className="dk-ctl-block">
            <span className="dk-ctl-name"><Droplets aria-hidden="true" />물 주기 <b>{live ? (a.irrigationPump ? '물 주는 중' : '대기') : '—'}</b></span>
            {a.irrigationPump && live
              ? <button type="button" className="dk-water is-on" onClick={() => send('water', false, { irrigationPump: false, nutrientValve: false })}>물 주기 멈추기</button>
              : <button type="button" className="dk-water" onClick={() => setConfirm(true)}>물 주기</button>}
            <Pend k="water" />
          </div>
        </fieldset>

        <div className="dk-ctl-row dk-auto">
          <span className="dk-ctl-name"><Sun aria-hidden="true" />자동 돌보기 <b>{a.autoMode ? '켜짐' : '꺼짐'}</b></span>
          <button type="button" role="switch" aria-checked={a.autoMode} className={`dk-switch ${a.autoMode ? 'is-on' : ''}`}
            disabled={!canControl || !live || (!cropAssigned && !a.autoMode)} onClick={onToggleAuto}
            title={!cropAssigned && !a.autoMode ? '식물을 먼저 골라 주세요' : undefined}>
            <span aria-hidden="true" /><span className="sr-only">자동 돌보기 {a.autoMode ? '끄기' : '켜기'}</span>
          </button>
        </div>
        {!cropAssigned && (
          <label className="dk-crop">식물 고르기 (자동 돌보기와 좋아하는 범위에 쓰여요)
            <select disabled={!canControl} defaultValue="" onChange={(e) => e.target.value && onSetCrop(e.target.value)}>
              <option value="">골라 주세요</option>
              {crops.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
        )}

        {confirm && (
          <div className="dk-confirm" role="alertdialog" aria-modal="true" aria-labelledby="dk-confirm-title">
            <h3 id="dk-confirm-title">물을 줄까요?</h3>
            <p>아래 물통의 물을 화분 밑 트레이로 올려요. 큐브 안 모든 화분에 함께 물이 가요.{pumpMax ? ` 펌프는 최대 ${pumpMax}초 뒤 자동으로 멈춰요.` : ' 펌프는 정해진 시간이 지나면 자동으로 멈춰요.'}</p>
            <div className="dk-confirm-btns">
              <button type="button" className="dk-btn-ghost" onClick={() => setConfirm(false)}>취소</button>
              <button type="button" className="dk-water" onClick={() => { setConfirm(false); onWater(); }}>물 주기</button>
            </div>
          </div>
        )}
      </aside>
    </div>
  );
};
