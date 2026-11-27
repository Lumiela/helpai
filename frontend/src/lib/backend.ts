// 젯슨 api 서비스(FastAPI) 클라이언트. 라우트는 edgeserver-JS/api/README.md 참조.
//
//   브라우저 ──/api/…──▶ nginx(web) ──▶ api:8000
//
// 기본 주소는 같은 호스트의 /api (nginx 가 접두어를 떼고 넘긴다). 개발 서버는 vite.config.ts 의
// proxy 가 같은 경로를 SSH 터널(127.0.0.1:8000)로 넘긴다. 다른 호스트를 쓰려면 VITE_API_BASE.
//
// 2026-09-28: 모든 요청에 Supabase 접근 토큰을 Authorization: Bearer 로 붙인다(auth.ts).
// 실시간 스트림은 EventSource 대신 fetch 스트림(sse.ts)으로 읽어 같은 헤더를 보낸다.
// 2026-09-29 오후: 다중 인스턴스. 인스턴스 라우트는 /i/<iid>/api/…, 전역 라우트(/health, /me, /claim)는 /api/….
//   현재 인스턴스는 주소 경로에서 읽는다(instancePath.ts).

import { getAccessToken } from './auth';
import { apiBaseFor, currentInstance } from './instancePath';
import { boundaryFromHeaders, MjpegParser } from './mjpeg';
import { openFetchStream } from './sse';

function readBase(): string {
  try {
    const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env;
    const base = env?.VITE_API_BASE;
    if (base) return base.replace(/\/+$/, '');
  } catch {
    // node 테스트 등 import.meta.env 가 없는 환경
  }
  return '/api';
}

export const API_ROOT = readBase(); // 전역 라우트 기준 (/api)
export const INSTANCE_ID: string | null = currentInstance();
export const API_BASE = apiBaseFor(INSTANCE_ID, API_ROOT); // 인스턴스 라우트 기준 (/i/<iid>/api)

// 펌웨어 텔레메트리(public 프레임). 필드 이름은 펌웨어 그대로.
export interface Telemetry {
  session: string;
  uptime_ms: number;
  applied_seq: number;
  ads1115_ok: boolean;
  moisture_calibrated: boolean;
  moisture_raw: number | null;
  moisture_v: number | null;
  moisture_pct: number | null;
  fan1_rpm: number;
  fan2_rpm: number;
  led1: number;
  led2: number;
  pump: boolean;
  fan1: number;
  fan2: number;
  lease_active: boolean;
  pump_locked: boolean;
  rssi_dbm: number;
  // esp32_devkit_node 1.1.0(2026-09-29)부터. 센서가 없으면 null 또는 필드 자체가 없다(옛 펌웨어).
  temperature?: number | null; // °C, SHT45 평균
  humidity?: number | null; // %RH, SHT45 평균
  lux?: number | null; // lx, BH1750
  temp_1?: number | null;
  hum_1?: number | null;
  temp_2?: number | null;
  hum_2?: number | null;
  temp_3?: number | null;
  hum_3?: number | null;
  sht45_count?: number;
  // esp32_devkit_node 1.2.2(2026-09-30)부터. ESP32 칩 내부 온도(°C, 보정 없음). 장치 연결 페이지 표시용.
  chip_temp?: number | null;
  // cube-devkit-1.3.0(2026-10-12)부터. RS485 토양 센서. 센서가 없거나 읽기 실패면 null.
  soil_ec?: number | null; // µS/cm
  soil_ph?: number | null; // pH
  // cube-devkit-1.5.0(2026-10-13)부터. CO2 HX-2000U. 첫 응답 뒤 3분 예열 중이거나 없으면 null.
  co2_ok?: boolean;
  co2_ppm?: number | null; // ppm
}

// 펌웨어 상태 프레임(status). 접속 직후와 요청 시에만 온다.
export interface StatusFrame {
  name: string;
  vendor: string;
  model: string;
  sn: string;
  fw: string;
  ip: string;
  transport: string;
  session: string;
  last_received_seq: number;
  outputs_enabled: boolean;
  lease_ms: number;
  pump_max_ms: number;
  protocol: string;
  // esp32_cam_node 펌웨어(2026-09-28)부터
  ads1115_ok?: boolean;
  cal_pending?: boolean;
  diag_mode?: boolean;
  air?: number;
  water?: number;
}

// GET /me (전역) — 소유자 모델(2026-09-29). 접근 판정은 서버가 한다.
export interface OperatorGrant {
  until: number; // epoch 초
  issued_at: number;
  reason: string;
}

export interface MeGlobal {
  sub: string;
  email: string;
  instances: string[]; // 내가 소유한(그리고 등록부에 있는) 인스턴스
  operator: boolean;
  grants: Record<string, OperatorGrant>; // 살아 있는 운영자 읽기 접근 (인스턴스별)
  auth_mode: string;
}

// GET /i/{iid}/me — 이 인스턴스에 대한 나
export interface Me extends MeGlobal {
  instance_id: string;
  instance_name: string | null;
  is_owner: boolean;
  access: 'owner' | 'operator' | 'none';
  grant: OperatorGrant | null;
  cubes: string[];
}

// POST /claim — 큐브 SN 으로 인스턴스 등록(회원가입). 게이트웨이가 인스턴스·큐브·브로커 계정을 자동으로 만든다.
export interface ClaimResponse {
  instance_id: string;
  sn: string;
  already: boolean;
}

// 카메라 (2026-09-29 오후). 카메라 SN = <큐브 SN>-C<번호>. 사진(스냅샷) 조회는 고객용이 아니라 화면에 없다(서버 라우트만).
export interface CameraInfo {
  sn: string;
  cube: string | null;
  number: number | null;
  name?: string | null;
  seen: number | null; // epoch 초
  status: Record<string, unknown> | null;
  last_file: string | null;
  last_taken: string | null;
  stream: { on: boolean; since: number | null; until: number | null; frames: number; last_frame: number | null; viewers: number };
}

// POST /i/{iid}/cubes — 장치 추가 (소유자)
export interface AddCubeResponse {
  instance_id: string;
  sn: string;
  already: boolean;
  cubes: string[];
}

// GET /i/{iid}/broker — 이 인스턴스의 장치용 브로커 계정 (펌웨어 secrets.h 에 넣을 값). 소유자만.
export interface BrokerInfo {
  instance_id: string;
  username: string;
  password: string | null;
  host: string;
  port: number;
  tls: boolean;
  app: string;
  ready: boolean; // 브로커에 계정·권한이 반영됐는지
  cubes: string[];
}

// 설정 보드 장치 등록 (2026-11-16, 계획서 edgeserver-JS/2026-11-16_설정보드장치등록_계획서).
// POST /i/{iid}/provision/codes — 큐브 시리얼로 일회용 등록 코드 발급(24시간, 지금의 "장치 추가"와 합침). 소유자만.
//   waiting 발급됨 · registered 설정 보드가 등록, 장치 연결 중 · done 기본 구성 모두 연결(서버가 AP 정지 명령)
//   failed 등록 뒤 제한 시간(10분) 안에 다 붙지 않음 · expired 24시간 지남 · replaced 같은 큐브에 새 코드 발급
export type ProvisionStatus = 'waiting' | 'registered' | 'done' | 'failed' | 'expired' | 'replaced';
export interface ProvisionCode {
  code: string; // XXXX-XXXX
  sn: string; // 큐브 시리얼
  link: string; // QR 에 넣는 설정 보드 링크 (http://<설정 보드 AP 주소>/register?code=…)
  issued_at: number; // epoch 초
  expires_at: number;
  status: ProvisionStatus;
  registered_at: number | null;
  done_at: number | null;
  failed_at: number | null;
  setup_sn: string | null; // 설정 보드 시리얼 (<큐브>-AP)
  expected: string[]; // 기본 구성: 큐브 제어기 + 카메라 2
  connected: string[];
  timeout_s: number;
}

// GET /instance/status (소유자 또는 운영자 읽기)
export interface InstanceStatus {
  version: string;
  instance_id: string;
  uptime_s: number;
  mqtt_connected: boolean;
  rx: Record<string, number>;
  cubes: Record<string, { session: string | null; last_seq: number; seen: number | null; has_values: boolean; has_status: boolean; fw: string | null }>;
  writer: { enabled: boolean; started: boolean; queued: number; written_lines: number; failed_batches: number; retried_batches: number; last_ok: number | null; last_error: string | null };
  audit: { enabled: boolean; sent: number; failed: number; last_error: string | null };
  auth: string;
  sse_subscribers: Record<string, number>;
}

// POST /cubes/{sn}/config — 토양 보정 (esp32_cam_node)
export interface SoilConfigBody {
  air?: number;
  water?: number;
  cal?: 'air' | 'water' | 'reset';
}

export interface SoilConfigResponse {
  sn: string;
  sent: Record<string, number>;
  air: number | null;
  water: number | null;
  cal_pending: boolean;
  ads1115_ok: boolean | null;
}

export interface CubeSummary {
  sn: string;
  session: string | null;
  seen: number | null; // epoch 초 (api 기준)
  has_values: boolean;
  // 2026-10-06 자동 제어(api 0.7). 예전 api 는 이 칸이 없다.
  crop_id?: string | null;
  auto_mode?: boolean;
}

// ── 자동 제어 (2026-10-06, api 0.7: app/crops.json · app/autoctl.py) ──
export interface CropRange {
  min: number;
  max: number;
}

export interface CropProfile {
  id: string;
  name: string;
  source: string | null;
  temp_day: CropRange;
  temp_night: CropRange;
  humidity_day: CropRange;
  humidity_night: CropRange;
  soil_moisture: CropRange;
  lux_day: CropRange; // lx
  photoperiod: { on: string; off: string; on_min: number; off_min: number }; // 켜는 시각~끄는 시각 = 주간
}

export interface CropListing {
  crops: CropProfile[];
  invalid: Record<string, string>;
  file_error: string | null;
}

export interface AutoAction {
  t: number; // epoch 초
  sent: { fan1?: number; led1?: number; pump?: boolean } | null;
  result: string;
  rules: string[];
}

export interface AutoState {
  phase: 'day' | 'night' | null;
  rules: string[];
  alerts: string[];
  excursions: Record<string, { side: 'low' | 'high'; since: number; value: number | null; min: number; max: number }>;
  events: Array<{ t: number; item: string; event: 'start' | 'end'; side?: string; value: number | null }>;
  paused: string | null;
  fails: number;
  last_eval: number | null;
  last_action: AutoAction | null;
  pump_count: number;
  pump_last_at: number;
}

export interface AutoView {
  sn: string;
  crop_id: string | null;
  crop: CropProfile | null;
  crop_invalid: string | null;
  enabled: boolean; // 켜져 있으면 급수 규칙도 함께 동작(별도 스위치 없음)
  available: boolean; // Supabase 에 자동 제어 칸이 있는지
  interval_s: number;
  state: AutoState | null;
}

export interface ValuesResponse {
  sn: string;
  seen: number; // epoch 초
  session: string;
  values: Telemetry;
}

export interface HistoryRow {
  time: string; // ISO 8601
  field: string;
  value: number;
}

export interface HistoryResponse {
  sn: string;
  start: string;
  field: string | null;
  count: number;
  rows: HistoryRow[];
}

// 제어 본문. 빠진 키는 api 가 마지막 텔레메트리 값으로 채운다.
export interface ControlBody {
  pump?: boolean;
  led1?: number;
  led2?: number;
  fan1?: number;
  fan2?: number;
}

export interface ControlResponse {
  sn: string;
  seq: number;
  sent: Record<string, number | boolean>;
  result: string; // ok / denied / outputs_disabled / stale_session / duplicate_or_old_seq / pump_locked / invalid_fields
}

export interface StreamEvent {
  sn: string;
  t: number; // epoch 초
  values: Telemetry;
}

export class BackendError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit, base: string = API_BASE): Promise<T> {
  let response: Response;
  try {
    const headers: Record<string, string> = { Accept: 'application/json', ...((init?.headers as Record<string, string>) ?? {}) };
    const token = await getAccessToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    response = await fetch(`${base}${path}`, { ...init, headers });
  } catch (error) {
    throw new BackendError(0, `백엔드에 연결할 수 없습니다 (${error instanceof Error ? error.message : String(error)})`);
  }
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!response.ok) {
    const detail = body && typeof body === 'object' && 'detail' in body ? (body as { detail: unknown }).detail : body;
    throw new BackendError(response.status, typeof detail === 'string' ? detail : `HTTP ${response.status}`);
  }
  return body as T;
}

// ── 전역 라우트 (/api/…) ──

export function getHealth() {
  return request<{ ok: boolean; mqtt_connected: boolean; instances: number; cubes: number; uptime_s: number; auth_mode?: string }>('/health', undefined, API_ROOT);
}

export function getMeGlobal() {
  return request<MeGlobal>('/me', undefined, API_ROOT);
}

export function postClaim(sn: string) {
  return request<ClaimResponse>('/claim', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sn }),
  }, API_ROOT);
}

// 로그인만 하고 큐브 등록은 나중에(2026-11-18): 소유 인스턴스가 없으면 큐브 없는 인스턴스를 만든다(있으면 그것).
export function postInstance() {
  return request<{ instance_id: string; created: boolean }>('/instances', { method: 'POST' }, API_ROOT);
}

// ── 인스턴스 라우트 (/i/<iid>/api/…) ──

export function getMe() {
  return request<Me>('/me');
}

export function getInstanceStatus() {
  return request<InstanceStatus>('/instance/status');
}

export function getCameras() {
  return request<CameraInfo[]>('/cameras');
}

export function postAddCamera(cube: string, number: number, name?: string) {
  return request<{ camera: string; cube: string; already: boolean; cameras: string[] }>('/cameras', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cube, number, name: name || undefined }),
  });
}

// 사진(스냅샷) 조회·즉시 촬영 — 고객 화면에는 없고 개발자 도구(알림 API 테스트)에서만 쓴다(2026-09-29 사용자 지시)
export interface SnapshotRow {
  camera: string;
  cube: string;
  date: string;
  file: string;
  path: string;
  bytes: number;
  taken_at: string | null;
  trigger: string | null;
  zoom_pct?: number | null; // 촬영 배율 %(2026-10-08). 1배거나 줌을 모르는 펌웨어면 없음
  cap_at?: number | null; // 카메라가 보낸 촬영 시각(NTP epoch ms, 펌웨어 0.7.1-captime 부터, 2026-11-11). 없으면 모름
  cmd_ms?: number | null; // 명령 수신 → 촬영까지 ms (같은 펌웨어부터)
  cap_ms?: number | null; // 촬영 소요 ms
}

export interface SnapshotList {
  cube: string;
  camera: string | null;
  count: number;
  rows: SnapshotRow[];
  cameras: string[];
}

export function getSnapshots(cube: string, opts: { cam?: string; date?: string; limit?: number } = {}) {
  const q = new URLSearchParams();
  if (opts.cam) q.set('cam', opts.cam);
  if (opts.date) q.set('date', opts.date);
  if (opts.limit) q.set('limit', String(opts.limit));
  const qs = q.toString();
  return request<SnapshotList>(`/cubes/${encodeURIComponent(cube)}/snapshots${qs ? `?${qs}` : ''}`);
}

export function snapshotUrl(cube: string, cam: string, date: string, file: string): string {
  return `${API_BASE}/cubes/${encodeURIComponent(cube)}/snapshots/${encodeURIComponent(cam)}/${date}/${encodeURIComponent(file)}`;
}

// 사진은 헤더 인증이 필요해 fetch 로 받아 Blob 으로 (img 태그는 헤더를 못 붙인다)
export async function getSnapshotBlob(cube: string, cam: string, date: string, file: string): Promise<Blob> {
  const token = await getAccessToken();
  const r = await fetch(snapshotUrl(cube, cam, date, file), { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!r.ok) throw new BackendError(r.status, `사진을 받지 못함 (HTTP ${r.status})`);
  return r.blob();
}

// zoom: 1·1.5·2·3(배). 1 이면 보내지 않는다. 펌웨어 cube-cam-0.6-zoom 만 적용하고 그 촬영 한 번에만 (2026-10-08)
export function postSnapshotRequest(cube: string, cam?: string, zoom?: number) {
  const body: { cam?: string; zoom?: number } = {};
  if (cam) body.cam = cam;
  if (zoom && zoom !== 1) body.zoom = zoom;
  return request<SnapshotRow & { camera: string; taken_at: string }>(`/cubes/${encodeURIComponent(cube)}/snapshot`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

// 동시 촬영(2026-11-11, 스테레오 거리 측정 시험용): 두 카메라에 촬영 명령을 연달아 보내고 두 업로드를 함께 기다린다.
export interface SnapshotPairResult {
  pair_id: string;
  capture_skew_ms: number | null; // 두 카메라가 보낸 촬영 시각의 차이. 펌웨어가 촬영 시각을 안 보내면 null
  arrival_skew_ms: number; // 두 업로드가 서버에 도착한 시각의 차이(촬영+전송이 섞인 값)
  a: SnapshotRow & { camera: string };
  b: SnapshotRow & { camera: string };
}

export function postSnapshotPair(cube: string, camA: string, camB: string, zoom?: number) {
  const body: { cam_a: string; cam_b: string; zoom?: number } = { cam_a: camA, cam_b: camB };
  if (zoom && zoom !== 1) body.zoom = zoom;
  return request<SnapshotPairResult>(`/cubes/${encodeURIComponent(cube)}/snapshot-pair`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function postStreamStart(cam: string) {
  return request<CameraInfo>(`/cameras/${encodeURIComponent(cam)}/stream/start`, { method: 'POST' });
}

export function postStreamStop(cam: string) {
  return request<CameraInfo>(`/cameras/${encodeURIComponent(cam)}/stream/stop`, { method: 'POST' });
}

// MJPEG 시청: fetch 스트림을 읽어 프레임(JPEG Blob)마다 onFrame. 돌려주는 함수로 닫는다.
export function openMjpegStream(cam: string, onFrame: (blob: Blob) => void, onError?: (message: string) => void): () => void {
  const ctrl = new AbortController();
  void (async () => {
    try {
      const token = await getAccessToken();
      const r = await fetch(`${API_BASE}/cameras/${encodeURIComponent(cam)}/stream`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        signal: ctrl.signal,
      });
      if (!r.ok || !r.body) throw new BackendError(r.status, `스트림 연결 실패 (HTTP ${r.status})`);
      const boundary = boundaryFromHeaders((name) => r.headers.get(name));
      if (!boundary) throw new BackendError(0, '스트림 형식이 MJPEG 가 아님');
      const parser = new MjpegParser(boundary);
      const reader = r.body.getReader();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        if (!value) continue;
        for (const part of parser.push(value)) {
          if (part.body.length > 0 && (part.headers['content-type'] ?? '').startsWith('image/jpeg')) {
            onFrame(new Blob([part.body], { type: 'image/jpeg' }));
          }
        }
      }
    } catch (e) {
      if (ctrl.signal.aborted) return;
      onError?.(e instanceof Error ? e.message : String(e));
    }
  })();
  return () => ctrl.abort();
}

export function postAddCube(sn: string) {
  return request<AddCubeResponse>('/cubes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sn }),
  });
}

export function postProvisionCode(sn: string) {
  return request<ProvisionCode>('/provision/codes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sn }),
  });
}

export function getProvisionCodes() {
  return request<ProvisionCode[]>('/provision/codes');
}

// 장치 연결 페이지 햄버거 메뉴 (2026-11-18). 소유자만.
//   연결 초기화 = 큐브 전체(메인 보드·카메라·설정 보드)에 설정값 지우기 명령(notify/erase) — 장치는 목록에 남는다.
//                 큐브 줄에만 있다(카메라 하나만 초기화하는 경로는 없앴다, 사용자 결정)
//   장치 제거 = 목록에서 빼기 — 큐브 제어기를 빼면 그 큐브의 카메라도 함께 빠진다
//              (11-18 사용자 결정) 빼기 전에 연결 초기화를 먼저 보낸다(큐브 = 큐브 전체, 카메라 = 그 카메라).
//              브로커가 끊겨 먼저 보낼 수 없으면 서버가 제거하지 않는다(503).
export interface EraseResponse {
  sn: string;
  sent: boolean; // 모든 대상에 명령을 내보냈는지
  targets: string[]; // 명령을 보낸 보드: [큐브, 카메라…, <큐브>-AP]
}
export interface RemoveResponse {
  sn: string;
  removed: string[];
  erase_targets?: string[]; // 제거 전에 연결 초기화를 보낸 보드
  erase_sent?: boolean;
}

export function postEraseCube(sn: string) {
  return request<EraseResponse>(`/cubes/${encodeURIComponent(sn)}/erase`, { method: 'POST' });
}

export function deleteCube(sn: string) {
  return request<RemoveResponse>(`/cubes/${encodeURIComponent(sn)}`, { method: 'DELETE' });
}

export function deleteCamera(cam: string) {
  return request<RemoveResponse>(`/cameras/${encodeURIComponent(cam)}`, { method: 'DELETE' });
}

export function getBroker() {
  return request<BrokerInfo>('/broker');
}

export function postBrokerRotate() {
  return request<BrokerInfo>('/broker/rotate', { method: 'POST' });
}

// 운영자: 마스터 키 + 사유 → 읽기 접근 발급 (응답은 /me 와 같은 모양)
export function postOperatorAccess(masterKey: string, reason: string) {
  return request<Me>('/operator/access', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ master_key: masterKey, reason }),
  });
}

export function deleteOperatorAccess() {
  return request<Me>('/operator/access', { method: 'DELETE' });
}

export function postConfig(sn: string, body: SoilConfigBody) {
  return request<SoilConfigResponse>(`/cubes/${encodeURIComponent(sn)}/config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

// 여러 필드를 한 번에. api 가 field=a,b,c 를 받는다(최대 12개).
export function getHistoryMulti(sn: string, fields: string[], start = '-24h', every?: string) {
  const query = new URLSearchParams({ start, field: fields.join(',') });
  if (every) query.set('every', every);
  return request<HistoryResponse>(`/cubes/${encodeURIComponent(sn)}/history?${query.toString()}`);
}

export function getCubes() {
  return request<CubeSummary[]>('/cubes');
}

export function getValues(sn: string) {
  return request<ValuesResponse>(`/cubes/${encodeURIComponent(sn)}/values`);
}

export function getStatus(sn: string) {
  return request<{ sn: string; status: StatusFrame }>(`/cubes/${encodeURIComponent(sn)}/status`);
}

// start 는 Flux 상대 시각(-24h), every 는 집계 창(5m = 5분 평균). 둘 다 api 가 모양을 검사한다.
export function getHistory(sn: string, field: string, start = '-24h', every?: string) {
  const query = new URLSearchParams({ start, field });
  if (every) query.set('every', every);
  return request<HistoryResponse>(`/cubes/${encodeURIComponent(sn)}/history?${query.toString()}`);
}

export function postControl(sn: string, body: ControlBody) {
  return request<ControlResponse>(`/cubes/${encodeURIComponent(sn)}/control`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function postRefresh(sn: string) {
  return request<{ sn: string; requested: boolean }>(`/cubes/${encodeURIComponent(sn)}/refresh`, { method: 'POST' });
}

export function getCrops() {
  return request<CropListing>('/crops');
}

export function getAuto(sn: string) {
  return request<AutoView>(`/cubes/${encodeURIComponent(sn)}/auto`);
}

// 작물 배정. null 이면 해제(자동 제어도 꺼진다).
export function putCrop(sn: string, cropId: string | null) {
  return request<AutoView>(`/cubes/${encodeURIComponent(sn)}/crop`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ crop_id: cropId }),
  });
}

// 자동 제어 켜기/끄기(급수 포함). 작물이 없으면 409("작물을 먼저 선택하세요").
export function putAuto(sn: string, body: { enabled: boolean }) {
  return request<AutoView>(`/cubes/${encodeURIComponent(sn)}/auto`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

// ── AI 리포트 (2026-10-06 → 10-07 젯슨 작성 방식, api app/report.py) ──
// 리포트는 매일 02:00(한국 시간)에 젯슨이 작성한다(GPU 서버 LLM 은 추론만). 소유자는 간격(1~30일)만 정하고,
// 간격이 1일보다 길면 하루치씩 나눠 회차를 따로 만든다.

export interface ReportSettings {
  sn: string;
  interval_days: number;
  min: number;
  max: number;
  anchor_time: string; // "02:00"
  windows: string[]; // ["02:00-02:10", "02:30-02:40"]
  last_anchor: string | null;
  next_anchor: string | null;
  next_period_start: string | null;
  now_max_hours: number; // "받기" 범위 상한(24)
  llm_ready: boolean; // LLM 서버 주소·키가 모두 있는지
  graph: string;
}

export type ReportStatus = 'pending' | 'writing' | 'done' | 'failed';

export interface ReportAttempt {
  at: string;
  window: string; // main | retry | now | manual
  ok: boolean;
  error: string | null;
  kind: string | null; // window_end | context | error | cancelled
}

export interface ReportProgress {
  step: number;
  total: number;
  name: string;
  label: string;
}

export interface ReportJob {
  job_id: string;
  instance: string;
  cube: string;
  date: string;
  anchor: string;
  period_start: string;
  period_end: string;
  interval_days: number;
  part: [number, number] | null; // 간격이 N일이면 [몇 번째 하루치, N]
  kind?: 'scheduled' | 'now'; // now = 화면의 "받기"로 즉시 요청한 회차
  status: ReportStatus;
  progress: ReportProgress | null; // 작성 중일 때 그래프의 진행 단계
  manual: boolean;
  reason: string | null;
  created_at: string;
  started_at: string | null;
  done_at: string | null;
  result_bytes: number | null;
  retries: number;
  attempt_count: number;
  last_attempt: ReportAttempt | null;
}

export function getReportSettings(sn: string) {
  return request<ReportSettings>(`/cubes/${encodeURIComponent(sn)}/report-settings`);
}

export function putReportSettings(sn: string, intervalDays: number) {
  return request<ReportSettings>(`/cubes/${encodeURIComponent(sn)}/report-settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ interval_days: intervalDays }),
  });
}

export function getReports(sn: string, limit = 50) {
  return request<{ sn: string; reports: ReportJob[]; total: number }>(`/cubes/${encodeURIComponent(sn)}/reports?limit=${limit}`);
}

// 회차 하나와 받은 결과 JSON(완료된 회차만 result 가 채워진다)
export function getReport(sn: string, jobId: string) {
  return request<ReportJob & { result: unknown; attempts: ReportAttempt[] }>(
    `/cubes/${encodeURIComponent(sn)}/reports/${encodeURIComponent(jobId)}`,
  );
}

// 즉시 요청(개발자 도구 "받기"): 입력한 범위 [start, end)(최대 24시간)로 회차를 만들고 차례가 되면 곧바로 작성. 정기 일정에는 영향 없음.
export function postReportNow(sn: string, start: string, end: string) {
  return request<ReportJob>(`/cubes/${encodeURIComponent(sn)}/reports/now`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ start, end }),
  });
}

// 실패 회차를 다시 작성하도록 넣는다(차례가 되면 곧바로). 응답은 넣은 뒤의 회차 상태(대기).
export function postReportRetry(sn: string, jobId: string) {
  return request<ReportJob>(`/cubes/${encodeURIComponent(sn)}/reports/${encodeURIComponent(jobId)}/retry`, { method: 'POST' });
}

// SSE 구독. fetch 스트림이라 Authorization 헤더가 붙고, 끊기면 스스로 다시 붙는다. 돌려주는 함수로 닫는다.
export function openStream(sn: string, onEvent: (event: StreamEvent) => void, onState?: (open: boolean) => void): () => void {
  return openFetchStream(
    `${API_BASE}/cubes/${encodeURIComponent(sn)}/stream`,
    (data) => {
      try {
        onEvent(JSON.parse(data) as StreamEvent);
      } catch {
        // 깨진 이벤트는 버린다
      }
    },
    { getToken: getAccessToken, onState },
  );
}

// 펌웨어 응답 결과 문자열을 사람이 읽는 말로
const RESULT_TEXT: Record<string, string> = {
  ok: '장치가 적용했습니다.',
  denied: '장치가 거부했습니다. 해당 출력이 펌웨어에서 꺼져 있습니다.',
  outputs_disabled: '장치 출력이 비활성 상태라 명령을 거부했습니다. 펌웨어 설정으로 출력을 켜야 합니다.',
  stale_session: '장치가 재접속해 세션이 바뀌었습니다. 잠시 뒤 다시 시도하세요.',
  duplicate_or_old_seq: '명령 순번이 겹쳐 거부됐습니다. 잠시 뒤 다시 시도하세요.',
  pump_locked: '펌프가 최대 가동 시간 뒤 잠금 상태입니다.',
  invalid_fields: '장치가 명령 형식을 이해하지 못했습니다.',
};

export function describeResult(result: string | undefined): string {
  // 응답에 결과가 없으면(예: 개발용 가짜 api) 숫자·undefined 대신 쉬운 말로
  if (!result) return '장치가 결과를 알려 주지 않았어요.';
  return RESULT_TEXT[result] ?? `장치 응답: ${result}`;
}
