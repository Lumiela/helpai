import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Header } from './components/Header';
import { ActuatorControl } from './components/ActuatorControl';
import { NotificationConsole } from './components/NotificationConsole';
import { EdgeDevicesList } from './components/EdgeDevicesList';
import { AlarmRulesAndLogs } from './components/AlarmRulesAndLogs';
import { QuickNoticeFloatingTester } from './components/QuickNoticeFloatingTester';
import { ActuatorState, EdgeDevice, NotificationLogItem, ZoneConfig } from './types/smartfarm';
import { AG, EdgeNotice, AGIcon, formatKoreanTime, onNotificationLog, onNotificationHandled, onDeviceRegistered } from './lib/agToolkit';
import { updateActuator } from './lib/actuatorControl';
import { IrrigationTimers } from './lib/irrigationTimers';
import {
  BackendError, describeResult, getAuto, getCrops, getCubes, getStatus, openStream, postControl, postRefresh, putAuto, putCrop,
  type AutoView, type ControlBody, type CropProfile, type StatusFrame,
} from './lib/backend';
import { NO_RANGES, photoperiodHours, rangesFromCrop } from './lib/zoneRanges';
import { useAuth } from './lib/AuthContext';
import { ACCESS_LABEL } from './lib/auth';
import { deleteOperatorAccess } from './lib/backend';
import { LoginScreen } from './components/LoginScreen';
import { NoAccessScreen } from './components/NoAccessScreen';
import { InstancesScreen } from './components/InstancesScreen';
import { loginUrl } from './lib/instancePath';
import { parseInstanceRoute, pathForTab, type TabId } from './lib/routes';
import { InstancePanel } from './components/InstancePanel';
import { CameraLab } from './components/CameraLab';
import { ReportsPanel } from './components/ReportsPanel';
import { SmartCubeApp } from './app2/SmartCubeApp';
import { DeskDashboard } from './desk/DeskDashboard';
import type { EmptyState } from './components/EmptyCubes';
import { appMenu } from './lib/appMenu';
import { useWideScreen } from './lib/useWideScreen';
import {
  actuatorFromTelemetry, buildDevice, buildZone, controlDiff, controlFromActuator, DEFAULT_ACTUATOR, DEFAULT_PROFILE, isLive, touchesDevice,
  type TelemetrySample, type ZoneProfile,
} from './lib/deviceMap';

// 2026-09-22: 모의 데이터를 걷어내고 젯슨 api 서비스에 붙였다.
//   /cubes 목록 → 큐브마다 SSE(/stream)로 텔레메트리 → 화면 모델(buildZone)
//   화면 조작 → 장치 명령(POST /control) → 펌웨어 응답을 알림으로
// 장치가 보고한 출력 상태가 진실이다. 명령이 거부되면 화면도 장치 상태로 되돌린다.

const DEV_TOOLS_KEY = 'smartcube-dev-tools';
const CUBES_POLL_MS = 15_000; // 큐브 목록·백엔드 상태 확인 주기
const CONTROL_DEBOUNCE_MS = 250; // 슬라이더를 끄는 동안 쌓인 변경을 명령 1건으로 모아 보낸다
const SYNC_GRACE_MS = 4_000; // 명령이 받아들여진 뒤 다음 텔레메트리가 반영될 때까지 화면을 덮어쓰지 않는 시간
const STATUS_RETRY_MS = 5_000;

// 2026-10-06 작물 기반 자동 제어: 큐브별 설정(서버 /cubes 목록에서). 판단은 서버가 한다.
interface CubeSettings {
  crop_id: string | null;
  auto_mode: boolean; // 켜져 있으면 급수 규칙도 함께 동작
}

// 서버 자동 제어 상태 중 사람에게 알릴 것: "센서 점검 필요"와 연속 실패로 인한 일시 정지(사용자 결정: 알림으로).
function autoNotices(view: AutoView | undefined): string[] {
  const st = view?.enabled ? view.state : null;
  if (!st) return [];
  const out = st.alerts.filter((a) => a.startsWith('센서 점검 필요'));
  if (st.paused) out.push(`자동 제어 일시 정지: ${st.paused}`);
  return out;
}

export interface BackendState {
  state: 'connecting' | 'ok' | 'error';
  message: string;
  checkedAt: number | null;
}

function readDevMode(): boolean {
  try {
    return localStorage.getItem(DEV_TOOLS_KEY) === '1';
  } catch {
    return false;
  }
}

function sameActuator(a: ActuatorState | undefined, b: ActuatorState): boolean {
  return a !== undefined && JSON.stringify(a) === JSON.stringify(b);
}

// 알림 문구용: 어떤 조작이었는지
function describeUpdates(updates: Partial<ActuatorState>): string {
  const parts: string[] = [];
  if (updates.outerFan !== undefined || updates.outerFanSpeed !== undefined) parts.push(updates.outerFan === false || updates.outerFanSpeed === 0 ? '배기팬 정지' : `배기팬 ${updates.outerFanSpeed ?? ''}단`);
  if (updates.innerFan !== undefined || updates.innerFanSpeed !== undefined) parts.push(updates.innerFan === false || updates.innerFanSpeed === 0 ? '순환팬 정지' : `순환팬 ${updates.innerFanSpeed ?? ''}단`);
  if (updates.ledIntensity !== undefined || updates.ledGrowLight !== undefined || updates.ledMode !== undefined) {
    parts.push(updates.ledGrowLight === false || updates.ledIntensity === 0 || updates.ledMode === 'night' ? 'LED 소등' : `LED ${updates.ledIntensity ?? ''}%`);
  }
  if (updates.irrigationPump !== undefined) parts.push(updates.irrigationPump ? '급수 시작' : '급수 정지');
  return parts.join(', ') || '제어';
}

export default function App() {
  // 2026-09-29 소유자 모델(Supabase 로그인). 소유자(또는 읽기 접근을 받은 운영자)일 때만 백엔드를 부른다.
  const auth = useAuth();
  // 우회 모드(2026-09-30)에서는 인스턴스 경로(/i/<iid>/)일 때만 부른다 — 루트(/)에는 인스턴스 라우트가 없다.
  // 인증 모드 확인 중(loading)에는 부르지 않는다.
  const authed = auth.loading
    ? false
    : auth.bypassed
      ? auth.instanceId !== null
      : !auth.enabled || auth.access === 'owner' || auth.access === 'operator';
  // 2026-09-29 오후: 어느 탭·어느 큐브인지는 주소가 진실이다(routes.ts). 탭 전환 = 주소 이동.
  const location = useLocation();
  const navigate = useNavigate();
  const route = useMemo(() => parseInstanceRoute(location.pathname), [location.pathname]);
  const activeTab: string = route.tab;
  const wide = useWideScreen();
  const isHome = activeTab === 'overview' || activeTab === 'zone-detail';
  const selectedZoneId = route.sn ?? '';
  const setActiveTab = useCallback(
    (tab: string) => {
      if (!route.iid) return;
      navigate(pathForTab(route.iid, tab as TabId, route.sn));
    },
    [navigate, route.iid, route.sn],
  );
  const setSelectedZoneId = useCallback(
    (sn: string, replace = false) => {
      if (!route.iid) return;
      navigate(pathForTab(route.iid, 'zone-detail', sn), { replace });
    },
    [navigate, route.iid],
  );
  const [cubes, setCubes] = useState<string[]>([]); // 백엔드가 아는 큐브 일련번호(정렬)
  const [samples, setSamples] = useState<Record<string, TelemetrySample>>({});
  const [statuses, setStatuses] = useState<Record<string, StatusFrame>>({});
  const [profiles, setProfiles] = useState<Record<string, ZoneProfile>>({});
  const [actuators, setActuators] = useState<Record<string, ActuatorState>>({});
  const [backend, setBackend] = useState<BackendState>({ state: 'connecting', message: '', checkedAt: null });
  const [now, setNow] = useState<number>(() => Date.now());
  const [discovered, setDiscovered] = useState<EdgeDevice[]>([]); // 알림 API(DiscoverMessage)로 등록된 것. 개발자 도구용
  const [logs, setLogs] = useState<NotificationLogItem[]>([]);
  const [devMode, setDevMode] = useState<boolean>(readDevMode);
  const [irrigationTimers] = useState(() => new IrrigationTimers());
  const [crops, setCrops] = useState<CropProfile[]>([]);
  const [cubeSettings, setCubeSettings] = useState<Record<string, CubeSettings>>({});
  const [autoViews, setAutoViews] = useState<Record<string, AutoView>>({});
  const cubeSettingsRef = useRef(cubeSettings);
  cubeSettingsRef.current = cubeSettings;

  const samplesRef = useRef(samples);
  samplesRef.current = samples;
  const actuatorsRef = useRef(actuators);
  actuatorsRef.current = actuators;
  const statusesRef = useRef(statuses);
  statusesRef.current = statuses;
  const syncHold = useRef<Record<string, number>>({}); // sn → 이 시각까지 텔레메트리로 화면을 덮어쓰지 않음
  const statusAsked = useRef<Record<string, number>>({}); // sn → 마지막 status 조회 시각
  const liveRef = useRef<Record<string, boolean>>({});
  // sn → 아직 보내지 않은 명령. base 는 모으기 시작한 시점의 화면 상태(바뀐 키를 고르는 기준)
  const pendingControl = useRef<Record<string, { base: ActuatorState; timer: number; note: string }>>({});

  useEffect(() => () => irrigationTimers.cancelAll(), [irrigationTimers]);

  // 모든 AG.Notification 이벤트를 이력에 기록한다.
  useEffect(() => {
    const unsubscribeLog = onNotificationLog((newLog) => {
      setLogs((prev) => [newLog, ...prev]);
    });
    // 확인 요청이 닫히면 처리 완료로 바꿔 헤더의 대기 건수를 줄인다.
    const unsubscribeHandled = onNotificationHandled((logId) => {
      setLogs((prev) => prev.map((l) => (l.id === logId ? { ...l, handled: true } : l)));
    });
    const unsubscribeDevice = onDeviceRegistered((device) => {
      setDiscovered((prev) => [device, ...prev.filter((item) => item.id !== device.id)]);
    });
    return () => {
      unsubscribeLog();
      unsubscribeHandled();
      unsubscribeDevice();
    };
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(DEV_TOOLS_KEY, devMode ? '1' : '0');
    } catch {
      // 저장 공간을 못 쓰는 환경에서는 설정을 기억하지 않는다.
    }
    if (!devMode && (activeTab === 'notification-lab' || activeTab === 'reports')) setActiveTab('overview');
  }, [devMode, activeTab, setActiveTab]);

  // 모르는 주소 조각(/i/x/nope)은 Home 으로 고쳐 쓴다
  useEffect(() => {
    if (route.iid && !route.known) navigate(pathForTab(route.iid, 'overview'), { replace: true });
  }, [route, navigate]);

  // PWA 시작 주소(/m)가 이 인스턴스로 오도록 기억한다(index.html)
  useEffect(() => {
    if (!route.iid) return;
    try { localStorage.setItem('smartcube-last-iid', route.iid); } catch { /* 사생활 모드 등 */ }
  }, [route.iid]);

  // 1초 시계: 통신 끊김 판정과 '몇 초 전' 표시에 쓴다.
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  // 상태 프레임(모델·펌웨어·출력 활성 여부)은 장치가 접속할 때만 오므로 따로 가져온다.
  const loadStatus = useCallback(async (sn: string) => {
    const t = Date.now();
    if (t - (statusAsked.current[sn] ?? 0) < STATUS_RETRY_MS) return;
    statusAsked.current[sn] = t;
    try {
      const { status } = await getStatus(sn);
      setStatuses((prev) => ({ ...prev, [sn]: status }));
    } catch {
      // 아직 상태 프레임이 없다(404). 다음 텔레메트리 때 다시 시도한다.
    }
  }, []);

  const loadAuto = useCallback(async (sn: string) => {
    try {
      const view = await getAuto(sn);
      setAutoViews((prev) => ({ ...prev, [sn]: view }));
      setCubeSettings((prev) => ({ ...prev, [sn]: { crop_id: view.crop_id, auto_mode: view.enabled } }));
    } catch {
      // 예전 api(0.6)에는 자동 제어 라우트가 없다(404). 수동 제어만 쓴다.
    }
  }, []);

  const refreshCubes = useCallback(async () => {
    try {
      const list = await getCubes();
      const sns = list.map((cube) => cube.sn).sort();
      setCubes((prev) => (prev.length === sns.length && prev.every((sn, i) => sn === sns[i]) ? prev : sns));
      setCubeSettings(Object.fromEntries(list.map((cube) => [cube.sn, {
        crop_id: cube.crop_id ?? null, auto_mode: Boolean(cube.auto_mode),
      }])));
      setBackend({ state: 'ok', message: '', checkedAt: Date.now() });
      for (const cube of list) {
        const known = statusesRef.current[cube.sn]?.session;
        if (!known || (cube.session && known !== cube.session)) void loadStatus(cube.sn);
        if (cube.crop_id !== undefined) void loadAuto(cube.sn);
      }
    } catch (error) {
      setBackend({ state: 'error', message: error instanceof Error ? error.message : String(error), checkedAt: Date.now() });
    }
  }, [loadStatus, loadAuto]);

  // 작물표는 서버 파일이라 화면을 열 때 한 번 읽는다(값을 고치면 api 재배포).
  useEffect(() => {
    if (!authed) return;
    getCrops().then((listing) => setCrops(listing.crops)).catch(() => setCrops([]));
  }, [authed]);

  useEffect(() => {
    if (!authed) return;
    void refreshCubes();
    const timer = setInterval(() => void refreshCubes(), CUBES_POLL_MS);
    return () => clearInterval(timer);
  }, [refreshCubes, authed]);

  // 큐브마다 실시간 스트림(SSE)을 연다. 목록이 바뀌면 다시 연다.
  useEffect(() => {
    if (!authed) return;
    const closers = cubes.map((sn) =>
      openStream(sn, (event) => {
        setSamples((prev) => ({ ...prev, [sn]: { values: event.values, seen: event.t * 1000 } }));
        const session = event.values.session;
        if (session && statusesRef.current[sn]?.session !== session) void loadStatus(sn);
      }));
    return () => closers.forEach((close) => close());
  }, [cubes, loadStatus, authed]);

  // 장치가 보고한 출력 상태를 화면 구동기 상태에 반영한다 (명령 직후 잠깐은 보류).
  useEffect(() => {
    const t = Date.now();
    setActuators((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const sn of Object.keys(samples)) {
        if ((syncHold.current[sn] ?? 0) > t) continue;
        const synced = actuatorFromTelemetry(prev[sn], samples[sn].values);
        if (!sameActuator(prev[sn], synced)) {
          next[sn] = synced;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [samples]);

  // 연결·끊김 전환을 알린다.
  useEffect(() => {
    for (const sn of cubes) {
      const live = isLive(samples[sn]?.seen, now);
      const before = liveRef.current[sn];
      if (before === live) continue;
      liveRef.current[sn] = live;
      if (live) EdgeNotice.toast(`${sn} 장치가 연결됐습니다.`);
      else if (before === true) EdgeNotice.info(`${sn} 통신 끊김`, '15초 넘게 텔레메트리가 오지 않습니다.');
    }
  }, [cubes, samples, now]);

  // 2026-10-06: 작물이 배정된 큐브는 서버 작물표의 지금 시간대 범위·광주기·이름을 쓴다. 미배정이면 범위 '—'.
  //   목표 온도 슬라이더(수동 제어 전용, 저장 안 됨)는 예전처럼 profiles 에서.
  const zones = useMemo<ZoneConfig[]>(
    () => cubes.map((sn) => {
      const base = profiles[sn] ?? DEFAULT_PROFILE;
      const cropId = cubeSettings[sn]?.crop_id ?? null;
      const crop = crops.find((c) => c.id === cropId) ?? null;
      const profile: ZoneProfile = {
        ...base,
        crop: crop ? crop.name : cropId ? `${cropId} (작물표에 없음)` : DEFAULT_PROFILE.crop,
        photoperiod: crop ? photoperiodHours(crop) : base.photoperiod,
        cropId,
        ranges: crop ? rangesFromCrop(crop, new Date(now)) : NO_RANGES,
      };
      return buildZone(sn, profile, samples[sn], statuses[sn], now);
    }),
    [cubes, profiles, samples, statuses, now, cubeSettings, crops],
  );
  // 자동 제어 켜짐 여부는 서버가 진실이다. 화면 구동기 상태에 덮어써서 넘긴다(켜져 있으면 수동 제어 카드가 회색·비활성).
  const viewActuators = useMemo<Record<string, ActuatorState>>(() => {
    const out: Record<string, ActuatorState> = {};
    for (const sn of cubes) out[sn] = { ...(actuators[sn] ?? DEFAULT_ACTUATOR), autoMode: Boolean(cubeSettings[sn]?.auto_mode) };
    return out;
  }, [cubes, actuators, cubeSettings]);
  // 2026-11-24 모바일 앱: 화면 표시와 애니메이션은 장치가 보고한 상태(텔레메트리)를 따른다. 명령 직후의 화면 상태(actuators)와 따로 둔다.
  const reportedActuators = useMemo<Record<string, ActuatorState>>(() => {
    const out: Record<string, ActuatorState> = {};
    for (const sn of cubes) {
      const base = viewActuators[sn] ?? DEFAULT_ACTUATOR;
      out[sn] = samples[sn] ? { ...actuatorFromTelemetry(base, samples[sn].values), autoMode: base.autoMode } : base;
    }
    return out;
  }, [cubes, viewActuators, samples]);

  // 서버 자동 제어의 "센서 점검 필요"·일시 정지를 알림으로 한 번씩 띄운다(사용자 결정). 사라졌다 다시 생기면 다시 알린다.
  //   자동 제어 상태는 큐브 목록을 확인할 때(15초마다) 함께 가져온다.
  const autoNoticed = useRef<Record<string, Set<string>>>({});
  useEffect(() => {
    for (const sn of Object.keys(autoViews)) {
      const current = autoNotices(autoViews[sn]);
      const before = autoNoticed.current[sn] ?? new Set<string>();
      for (const message of current) {
        if (before.has(message)) continue;
        const cut = message.indexOf(': ');
        EdgeNotice.info(`${sn} ${cut > 0 ? message.slice(0, cut) : message}`, cut > 0 ? message.slice(cut + 2) : '');
      }
      autoNoticed.current[sn] = new Set(current);
    }
  }, [autoViews]);
  const devices = useMemo<EdgeDevice[]>(() => [...zones.map((zone) => buildDevice(zone, now)), ...discovered], [zones, now, discovered]);

  useEffect(() => {
    // 큐브 상세인데 주소의 큐브가 없으면(없어졌거나 비어 있음) 첫 큐브 주소로 고쳐 쓴다
    if (activeTab === 'zone-detail' && zones.length > 0 && !zones.some((zone) => zone.id === selectedZoneId)) setSelectedZoneId(zones[0].id, true);
  }, [zones, selectedZoneId, activeTab, setSelectedZoneId]);

  // 장치 명령. 응답이 오면 알리고, 거부·실패면 장치가 보고한 상태로 화면을 되돌린다.
  const sendControl = useCallback(async (zone: ZoneConfig, body: ControlBody, label: string, note = '') => {
    const sn = zone.id;
    syncHold.current[sn] = Date.now() + 10_000;
    try {
      const response = await postControl(sn, body);
      if (response.result === 'ok') {
        syncHold.current[sn] = Date.now() + SYNC_GRACE_MS;
        EdgeNotice.toast(`${sn} ${label}: ${describeResult('ok')}${note}`);
        return;
      }
      syncHold.current[sn] = 0;
      EdgeNotice.info(`${sn} ${label} 거부`, describeResult(response.result));
    } catch (error) {
      syncHold.current[sn] = 0;
      const message = error instanceof BackendError ? error.message : error instanceof Error ? error.message : String(error);
      EdgeNotice.info(`${sn} ${label} 실패`, message);
    }
    const sample = samplesRef.current[sn];
    if (sample) setActuators((prev) => ({ ...prev, [sn]: actuatorFromTelemetry(prev[sn], sample.values) }));
  }, []);

  // 변경을 잠깐 모았다가, 모으기 시작한 시점과 비교해 바뀐 출력만 보낸다.
  const queueControl = (zone: ZoneConfig, before: ActuatorState, label: string, note: string) => {
    const sn = zone.id;
    syncHold.current[sn] = Date.now() + 10_000;
    const pending = pendingControl.current[sn];
    if (pending) clearTimeout(pending.timer);
    const base = pending?.base ?? before;
    const keptNote = note || pending?.note || '';
    const timer = window.setTimeout(() => {
      delete pendingControl.current[sn];
      const body = controlDiff(base, actuatorsRef.current[sn] ?? base);
      if (Object.keys(body).length === 0) {
        syncHold.current[sn] = 0; // 결국 바뀐 출력이 없다(예: 슬라이더를 원래 값으로 되돌림)
        return;
      }
      void sendControl(zone, body, label, keptNote).then(() => void loadAuto(sn));
    }, CONTROL_DEBOUNCE_MS);
    pendingControl.current[sn] = { base, timer, note: keptNote };
  };

  const handleSelectZone = (zoneId: string) => {
    setSelectedZoneId(zoneId);
  };

  // full: 긴급 정지. 모으지 않고 모든 출력(fan1·fan2·led1·pump)을 바로 보낸다.
  // note: 장치가 받아들였을 때 알림 끝에 붙일 안내
  const handleUpdateActuator = (zoneId: string, updates: Partial<ActuatorState>, options: { full?: boolean; note?: string } = {}) => {
    const zone = zones.find((item) => item.id === zoneId);
    if (!zone) return;
    if (!auth.canControl) {
      EdgeNotice.info(
        `${zone.code} 조작 불가`,
        auth.access === 'operator' ? '운영자 읽기 접근은 조작할 수 없습니다.' : '큐브 소유자만 조작할 수 있습니다.',
      );
      return;
    }
    const { autoMode, ...rest } = updates;
    // 자동 제어 켜기/끄기만 누른 것: 서버에 요청한다(2026-10-06). 작물이 없으면 서버가 409.
    if (autoMode !== undefined && Object.keys(rest).length === 0) {
      void handleSetAuto(zone, autoMode);
      return;
    }
    if (rest.irrigationPump !== undefined || rest.nutrientValve !== undefined) irrigationTimers.cancel(zoneId);
    const current = actuatorsRef.current[zoneId] ?? DEFAULT_ACTUATOR;
    const next = updateActuator(current, { ...rest, autoMode: false }, zone);
    actuatorsRef.current = { ...actuatorsRef.current, [zoneId]: next };
    setActuators((prev) => ({ ...prev, [zoneId]: next }));
    if (touchesDevice(rest)) {
      // 사람 조작이면 서버가 그 큐브의 자동 제어를 끈다(사용자 결정). 화면도 바로 꺼짐으로.
      if (cubeSettingsRef.current[zoneId]?.auto_mode) {
        setCubeSettings((prev) => ({ ...prev, [zoneId]: { ...prev[zoneId], auto_mode: false } }));
        EdgeNotice.toast(`${zone.code} 직접 조작해 자동 제어가 꺼졌습니다.`);
      }
      if (options.full) {
        const pending = pendingControl.current[zoneId];
        if (pending) clearTimeout(pending.timer);
        delete pendingControl.current[zoneId];
        void sendControl(zone, controlFromActuator(next), describeUpdates(rest), options.note).then(() => void loadAuto(zoneId));
      } else {
        queueControl(zone, current, describeUpdates(rest), options.note ?? '');
      }
    }
  };

  const applyAutoView = (view: AutoView) => {
    setAutoViews((prev) => ({ ...prev, [view.sn]: view }));
    setCubeSettings((prev) => ({ ...prev, [view.sn]: { crop_id: view.crop_id, auto_mode: view.enabled } }));
  };

  const autoErrorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

  async function handleSetAuto(zone: ZoneConfig, enabled: boolean) {
    try {
      applyAutoView(await putAuto(zone.id, { enabled }));
      EdgeNotice.toast(enabled ? `${zone.code} 자동 제어를 켰습니다. 서버가 ${zone.crop} 범위로 판단합니다.` : `${zone.code} 자동 제어를 껐습니다. 수동 제어만 됩니다.`);
    } catch (error) {
      EdgeNotice.info(`${zone.code} 자동 제어 ${enabled ? '켜기' : '끄기'} 실패`, autoErrorText(error));
    }
  }

  const handleSetCrop = async (zoneId: string, cropId: string | null) => {
    const zone = zones.find((item) => item.id === zoneId);
    if (!zone || !auth.canControl) return;
    try {
      applyAutoView(await putCrop(zoneId, cropId));
      const name = crops.find((c) => c.id === cropId)?.name ?? cropId;
      EdgeNotice.toast(cropId ? `${zone.code} 작물을 ${name}(으)로 정했습니다.` : `${zone.code} 작물을 해제했습니다. 자동 제어도 꺼졌습니다.`);
    } catch (error) {
      EdgeNotice.info(`${zone.code} 작물 변경 실패`, autoErrorText(error));
    }
  };

  const handleStartIrrigation = (zoneId: string, simulated: boolean) => {
    const zone = zones.find((item) => item.id === zoneId);
    if (!zone || (simulated && !devMode)) return;
    // 결과 알림은 장치 응답을 받은 뒤 sendControl 이 띄운다(거부·오프라인이면 실패 알림).
    const pumpMax = zone.device?.pumpMaxMs;
    handleUpdateActuator(zoneId, { irrigationPump: true, nutrientValve: true }, {
      note: pumpMax ? ` 장치는 최대 ${Math.round(pumpMax / 1000)}초 뒤 스스로 멈춥니다.` : '',
    });
    // 실제 장치는 스스로 멈추므로 화면 타이머는 개발자 도구의 5초 모의 급수에만 쓴다.
    if (simulated) {
      irrigationTimers.start(zoneId, true, () => {
        handleUpdateActuator(zoneId, { irrigationPump: false, nutrientValve: false });
      });
    }
  };

  const handleUpdateTargetTemp = (zoneId: string, target: number) => {
    setProfiles((prev) => {
      const profile = prev[zoneId] ?? DEFAULT_PROFILE;
      return { ...prev, [zoneId]: { ...profile, targetTemp: { ...profile.targetTemp, currentTarget: target } } };
    });
  };

  const handleClearLogs = () => {
    setLogs([]);
    EdgeNotice.toast('알림 이력을 비웠습니다.', 3000, { record: false });
  };

  const handlePingDevice = async (device: EdgeDevice) => {
    if (!cubes.includes(device.id)) {
      EdgeNotice.toast(`${device.name}: 백엔드에 등록되지 않은 장치입니다.`);
      return;
    }
    const before = samplesRef.current[device.id]?.seen ?? 0;
    const t0 = Date.now();
    try {
      await postRefresh(device.id);
    } catch (error) {
      EdgeNotice.info(`${device.id} 응답 확인 실패`, error instanceof Error ? error.message : String(error));
      return;
    }
    const timer = setInterval(() => {
      const seen = samplesRef.current[device.id]?.seen ?? 0;
      if (seen > before) {
        clearInterval(timer);
        EdgeNotice.toast(`${device.id} 응답 ${((Date.now() - t0) / 1000).toFixed(1)}초. 연결 정상.`);
      } else if (Date.now() - t0 > 4000) {
        clearInterval(timer);
        EdgeNotice.info(`${device.id} 응답 없음`, '4초 안에 텔레메트리가 오지 않았습니다.');
      }
    }, 200);
  };

  const handleEmergencyStop = () => {
    EdgeNotice.dangerAlert(
      '전체 긴급 정지',
      '모든 큐브에 배기팬·순환팬·LED·급수 펌프 정지 명령을 보냅니다. 댐퍼·CO₂ 표시도 정지로 바꿉니다.',
      () => {
        irrigationTimers.cancelAll();
        for (const zone of zones) {
          handleUpdateActuator(zone.id, {
            outerFan: false,
            outerFanSpeed: 0,
            innerFan: false,
            innerFanSpeed: 0,
            irrigationPump: false,
            nutrientValve: false,
            co2Generator: false,
            ledGrowLight: false,
            ledIntensity: 0,
            activePresetName: null,
            coolingDamper: false,
            heatingDamper: false,
          }, { full: true });
        }
        EdgeNotice.toast(zones.length ? '모든 큐브에 정지 명령을 보냈습니다. 결과는 큐브별 알림으로 표시됩니다.' : '연결된 큐브가 없습니다.', 5000);
      },
      '전체 정지'
    );
  };

  // 큐브 단위 이상 상황 모의 (개발자 도구에서 호출)
  const handleSimulateAlert = (zoneId: string, alertType: string) => {
    const zone = zones.find((z) => z.id === zoneId) || zones[0];
    if (!zone) {
      EdgeNotice.toast('연결된 큐브가 없어 모의 알림을 만들 수 없습니다.');
      return;
    }

    if (alertType === 'high_temp') {
      EdgeNotice.info(
        `${zone.code} 고온 (모의)`,
        `현재 34.5°C, 상한 ${zone.targetTemp.max.toFixed(1)}°C 초과. 냉각 댐퍼가 가동 중이며 목표 온도를 낮추는 것을 권장합니다.`,
        4000
      );
    } else if (alertType === 'humidity_trap') {
      const markup = `
        <group-row class="background-white">
          <row-start class="background-gray-light padding-4">
            <span class="ag-icon">${AGIcon.water}</span>
            <div class="text-bold">${zone.code} 과습, 결로 위험 (모의)</div>
            <span class="margin-left-auto text-time">${formatKoreanTime()}</span>
          </row-start>
          <div class="padding-6">
            <p class="message-body">상대습도 94.2%로 곰팡이병 위험이 있습니다. 배기팬과 순환팬을 3단으로 올릴까요?</p>
            <row-between class="margin-top-4 justify-end">
              <button type="button" data-action="close" class="button button-small color-gray">무시</button>
              <button type="button" data-action="fan-on" class="button button-small color-indigo">팬 3단 가동</button>
            </row-between>
          </div>
        </group-row>
      `;

      AG.Notification.Message(markup, function () {
        const item = this;
        item.querySelector('[data-action="fan-on"]')?.addEventListener(
          'click',
          () => {
            handleUpdateActuator(zoneId, { outerFan: true, outerFanSpeed: 3, innerFan: true, innerFanSpeed: 3 });
            item.remove();
          },
          { once: true }
        );
        item.querySelector('[data-action="close"]')?.addEventListener('click', () => item.remove(), { once: true });
      });
    } else if (alertType === 'ec_spike') {
      EdgeNotice.dangerAlert(
        `${zone.code} 양액 EC 급상승 (모의)`,
        `현재 배지 EC 3.45 dS/m, 상한 ${zone.targetEc.max} dS/m 초과. 급수를 일시 차단해 뿌리 염류 장해를 막습니다.`,
        () => {
          handleUpdateActuator(zoneId, { irrigationPump: false, nutrientValve: false });
        },
        '급수 차단'
      );
    }
  };

  // 시설 단위 시나리오 (개발자 도구에서 호출)
  const handleSimulateAlarmScenario = (scenario: string) => {
    if (scenario === 'heatwave') {
      EdgeNotice.info('폭염 경보 (모의)', '외부 온도 36°C 초과. 모든 큐브의 냉각 댐퍼를 열고 주간 모드로 전환하는 것을 권장합니다.', 4500);
    } else if (scenario === 'frost') {
      EdgeNotice.dangerAlert(
        '한파 경보 (모의)',
        '큐브 내부 온도가 8.2°C로 급락했습니다. 난방 순환 밸브를 긴급 개방할까요?',
        () => EdgeNotice.toast('(모의) 난방 순환 펌프를 가동하고 보온 커튼을 닫았습니다.'),
        '밸브 개방'
      );
    } else if (scenario === 'ec_imbalance') {
      EdgeNotice.info('양액 혼합비 이상 (모의)', 'A액과 B액 혼합비 편차 18%. 희석수 밸브를 자동으로 보정합니다.');
    } else if (scenario === 'power_fail') {
      EdgeNotice.dangerAlert(
        '상용 전원 정전 (모의)',
        '비상 발전기를 기동하는 중입니다. 비필수 조명을 끄고 엣지 제어기를 UPS 배터리로 전환했습니다.',
        () => EdgeNotice.toast('(모의) 비상 발전기 전력 동기화를 마쳤습니다.'),
        '확인'
      );
    }
  };

  const selectedZone = zones.find((z) => z.id === selectedZoneId) ?? zones[0] ?? null;
  const unhandledAlertsCount = logs.filter((l) => l.handled === false && (l.severity === 'danger' || l.severity === 'warning')).length;
  const canStopAll = auth.canControl && zones.length > 0;
  const grantUntil = auth.me?.grant ? new Date(auth.me.grant.until * 1000).toLocaleTimeString('ko-KR', { hour12: false, hour: '2-digit', minute: '2-digit' }) : null;

  // 로그인·접근 관문 (훅은 전부 위에서 끝났으므로 여기서 갈라져도 된다)
  // auth.loading 은 api 인증 모드 확인(checking) 중에도 true 다(2026-09-30 우회).
  if (auth.loading || (auth.enabled && auth.claims && auth.access === 'loading')) {
    return (
      <div className="app-viewport">
        <div className="app-shell bg-surface text-ink flex items-center justify-center">
          <p className="text-sm text-ink-muted">로그인·접근 상태를 확인하는 중…</p>
        </div>
      </div>
    );
  }
  // 2026-09-29 오후: 로그인은 /login 하나. 인스턴스 경로(/i/<iid>/)에 미로그인으로 오면 /login?next= 로 보낸다.
  if (auth.enabled && !auth.claims) {
    if (auth.instanceId) {
      window.location.replace(loginUrl(window.location.pathname));
      return null;
    }
    return <LoginScreen />;
  }
  // 로그인됐고 인스턴스 경로가 아니면(/login, /): 소유 인스턴스로 자동 이동(AuthContext). 여럿·없음이면 목록·등록 화면.
  // 우회 모드(api AUTH_MODE=off)에서도 인스턴스 경로가 아니면 목록 화면(하나면 AuthContext 가 바로 보낸다).
  if ((auth.enabled || auth.bypassed) && auth.instanceId === null) return <InstancesScreen />;
  if (auth.enabled && auth.access === 'none') return <NoAccessScreen />;

  // 2026-11-25 사용자 결정: 주소를 나누지 않고 화면 폭으로 모바일 앱과 웹 대시보드를 바꾼다.
  //   기존 관제 화면(Home·큐브 상세)과 고르는 화면은 뺐다. 장치 연결·알림 이력·프리셋 등은 메뉴에서 들어가며 아래의 예전 틀을 쓴다.
  if (isHome) {
    const menu = appMenu(route.iid, devMode);
    const devicesHref = route.iid ? pathForTab(route.iid, 'devices') : null;
    const empty: EmptyState = backend.state === 'ok' ? 'none' : backend.state === 'error' ? 'error' : 'loading';
    return wide ? (
      <DeskDashboard
        zones={zones}
        zoneId={selectedZoneId}
        onSelectZone={handleSelectZone}
        reported={reportedActuators}
        canControl={auth.canControl}
        crops={crops}
        now={now}
        devMode={devMode}
        alertsCount={unhandledAlertsCount}
        menu={menu}
        empty={empty}
        devicesHref={devicesHref}
        userEmail={auth.claims?.email ?? null}
        canSignOut={auth.enabled}
        onSignOut={() => void auth.signOut()}
        onEmergencyStop={handleEmergencyStop}
        onUpdate={(sn, updates) => handleUpdateActuator(sn, updates)}
        onStartIrrigation={(sn) => handleStartIrrigation(sn, false)}
        onSetCrop={(sn, cropId) => void handleSetCrop(sn, cropId)}
      />
    ) : (
      <SmartCubeApp
        zones={zones}
        reported={reportedActuators}
        canControl={auth.canControl}
        crops={crops}
        now={now}
        devMode={devMode}
        logs={logs}
        menu={menu}
        empty={empty}
        devicesHref={devicesHref}
        userEmail={auth.claims?.email ?? null}
        canSignOut={auth.enabled}
        onSignOut={() => void auth.signOut()}
        onUpdate={(sn, updates) => handleUpdateActuator(sn, updates)}
        onStartIrrigation={(sn) => handleStartIrrigation(sn, false)}
        onSetCrop={(sn, cropId) => void handleSetCrop(sn, cropId)}
        onEmergencyStop={handleEmergencyStop}
      />
    );
  }

  return (
    <div className="app-viewport">
    <div className="app-shell bg-surface text-ink flex flex-col">
      <Header
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        unhandledAlertsCount={unhandledAlertsCount}
        devMode={devMode}
        setDevMode={setDevMode}
        onEmergencyStop={handleEmergencyStop}
        user={auth.enabled && auth.claims ? { email: auth.claims.email, accessLabel: ACCESS_LABEL[auth.access === 'operator' ? 'operator' : 'owner'] + (grantUntil ? ` (${grantUntil}까지)` : '') } : null}
        authBypassed={auth.bypassed}
        instanceId={auth.instanceId}
        instanceName={auth.me?.instance_name ?? null}
        backend={backend}
        cubeCount={cubes.length}
        onRefresh={() => void refreshCubes()}
        canStopAll={canStopAll}
        onSignOut={() => void auth.signOut()}
      />
      {auth.access === 'operator' && (
        <div className="bg-danger-soft text-ink text-sm px-4 py-2 flex items-center gap-3" role="status">
          <span>운영자 읽기 접근 중{grantUntil ? ` — ${grantUntil}까지` : ''}. 조작은 할 수 없고 이 접근은 소유자 기록에 남습니다.</span>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => void deleteOperatorAccess().then(() => auth.refreshMe())}>
            접근 반납
          </button>
        </div>
      )}

      <main key={activeTab} className="app-main mx-auto w-full max-w-[1840px] px-4 sm:px-6 py-5 pb-24 md:pb-10 lg:py-4 flex-1">
        {activeTab === 'actuators' && <ActuatorControl zones={zones} actuators={viewActuators} onUpdateActuator={handleUpdateActuator} />}

        {activeTab === 'devices' && (
          <EdgeDevicesList devices={devices} canManage={auth.canControl} onAdded={() => void refreshCubes()} onPing={(device) => void handlePingDevice(device)} />
        )}

        {activeTab === 'logs' && (
          // 2026-09-29: 서버 상태·조작 기록(InstancePanel)은 별도 탭 대신 알림 이력 아래에 이어 붙인다(사용자 지시).
          <div className="flex flex-col gap-5">
            <AlarmRulesAndLogs logs={logs} onClearLogs={handleClearLogs} onSimulateAlarmScenario={handleSimulateAlarmScenario} devMode={devMode} />
            {authed && <InstancePanel />}
          </div>
        )}

        {/* 2026-10-06 사용자 지시: AI 리포트는 당분간 개발자 모드 켜짐에서만 */}
        {activeTab === 'reports' && devMode && (authed ? (
          <ReportsPanel cubes={cubes} canControl={auth.canControl} />
        ) : (
          <p className="panel p-6 text-sm text-ink-muted">로그인 뒤에 볼 수 있습니다.</p>
        ))}

        {activeTab === 'notification-lab' && devMode && (
          <div className="flex flex-col gap-5">
            <NotificationConsole />
            {/* 2026-09-29 사용자 지시: 즉시 촬영·최신 사진 조회는 개발자 모드의 알림 API 테스트 안에서만 */}
            {authed && <CameraLab cubes={cubes} />}
          </div>
        )}
      </main>

      {devMode && <QuickNoticeFloatingTester zones={zones} onSimulateAlert={handleSimulateAlert} />}
    </div>
    </div>
  );
}
