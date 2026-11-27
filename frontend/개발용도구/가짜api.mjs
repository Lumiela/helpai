// 가짜 젯슨 api (127.0.0.1:8001) — 최신 관제 화면(fr, src/lib/backend.ts 2026-10-08 기준)용.
// 실제 장치·젯슨 없이 그래프·제어·카메라·AI 리포트 화면을 그려 보기 위한 것이고, 배포본에는 들어가지 않는다.
//
//   실행   node 개발용도구/가짜api.mjs          api 만 (8001)
//          node 개발용도구/개발서버실행.mjs      api + vite 개발 서버(3000) 한 번에
//   vite 개발 서버는 SMARTCUBE_API=http://127.0.0.1:8001 로 /api, /i/<iid>/api 를 여기로 넘긴다(vite.config.ts 의 proxy).
//
//   시연 모드  http://127.0.0.1:8001/mock/mode/<ok|warn|critical|offline>
//              ok=전부 범위 안 · warn=조도만 부족 · critical=온도·습도·조도·수분 동시 이탈 · offline=통신 끊김
//   환경변수   MOCK_PORT(8001) MOCK_IID(jetson-01) MOCK_CUBES(CUBE001[,CUBE002…]) MOCK_FW(펌웨어 표시 문자열)
//
//   설정 보드 장치 등록(2026-11-16, 계획서 edgeserver-JS/2026-11-16_설정보드장치등록_계획서) — 아래 "설정 보드 장치 등록" 절
//              MOCK_HOST(127.0.0.1, 설정 보드 실기 시험 때 0.0.0.0) MOCK_AP_ADDR(192.168.4.1)
//              MOCK_PROVISION_TIMEOUT_S(600 = 10분) MOCK_PROVISION_SIM(auto|fail|manual)
//              실행 중 변경: http://127.0.0.1:8001/mock/provision/sim/<auto|fail|manual>?timeout=<초>
//              설정 보드 명령: MOCK_DEVICE_NAME(helpai.cube) MOCK_MQTT_HOST(127.0.0.1) MOCK_MQTT_PORT(1883) MOCK_MQTT_PUB(mosquitto_pub 경로)
//              지우기 명령 흉내: http://127.0.0.1:8001/mock/provision/erase/<설정 보드 SN>
//
// 측정값은 시각의 함수(광주기 → 온도·습도·조도, 6시간 급수 주기 → 토양 수분)라서 실시간 값과 24시간 이력이 이어진다.
// 제어 명령은 바로 텔레메트리에 반영되고(팬·LED·펌프), 펌프는 pump_max_ms 뒤 스스로 멈춘다.

import { createServer } from 'node:http';
import { execFile } from 'node:child_process';
import { randomInt } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const PORT = Number(process.env.MOCK_PORT ?? 8001);
const HOST = process.env.MOCK_HOST ?? '127.0.0.1';
const IID = process.env.MOCK_IID ?? 'jetson-01';
const INSTANCE_NAME = '헬피아이 스마트큐브';
const CUBE_SNS = (process.env.MOCK_CUBES ?? 'CUBE001').split(',').map((s) => s.trim().toUpperCase()).filter(Boolean);
const FW = process.env.MOCK_FW ?? 'esp32_devkit_node 1.7.1';
const CAM_FW = 'cube-cam-0.7.1';
const API_VERSION = '0.7.2';
const PUMP_MAX_MS = 30_000;
const BOOT = Date.now();
const HERE = path.dirname(fileURLToPath(import.meta.url));
// 카메라 그림: camera-mock.jpg = 스트림 프레임(펌웨어 cam-api STREAM_FRAME_SIZE = HD 1280×720),
//              camera-mock-snapshot.jpg = 사진 조회용(스냅샷 비율 4:3). 사진용이 없으면 스트림 프레임을 같이 쓴다.
const JPEG_PATH = path.join(HERE, 'camera-mock.jpg');
const JPEG = existsSync(JPEG_PATH) ? readFileSync(JPEG_PATH) : null;
const SNAP_PATH = path.join(HERE, 'camera-mock-snapshot.jpg');
const SNAP = existsSync(SNAP_PATH) ? readFileSync(SNAP_PATH) : JPEG;

const nowS = () => Math.floor(Date.now() / 1000);
const iso = (ms) => new Date(ms).toISOString();
const r1 = (v) => Math.round(v * 10) / 10;
const round = (v, d) => Math.round(v * 10 ** d) / 10 ** d;
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const KST = 9 * 3600e3;
const kstDate = (ms) => new Date(ms + KST).toISOString().slice(0, 10);
const kstHms = (ms) => new Date(ms + KST).toISOString().slice(11, 19).replace(/:/g, '');
const kstAt = (dateStr, h, m = 0) => Date.parse(`${dateStr}T${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:00+09:00`);
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

// ── 시연 모드 ──
let MODE = 'ok';
const SHIFT = {
  ok: { temperature: 0, humidity: 0, lux: 0, moisture_pct: 0 },
  warn: { temperature: 0, humidity: 0, lux: -4200, moisture_pct: 0 },
  critical: { temperature: 7.5, humidity: -24, lux: -6500, moisture_pct: -22 },
  offline: { temperature: 0, humidity: 0, lux: 0, moisture_pct: 0 },
};
const offline = () => MODE === 'offline';
const seenOf = () => (offline() ? nowS() - 600 : nowS());

// ── 작물표 (api app/crops.json 역할. 예시값) ──
const pp = (on, off) => {
  const m = (s) => { const [h, mm] = s.split(':').map(Number); return h * 60 + mm; };
  return { on, off, on_min: m(on), off_min: m(off) };
};
const CROPS = [
  { id: 'mini_rose', name: '미니장미', source: '가짜 api 예시값', temp_day: { min: 20, max: 26 }, temp_night: { min: 16, max: 21 }, humidity_day: { min: 55, max: 75 }, humidity_night: { min: 60, max: 80 }, soil_moisture: { min: 40, max: 60 }, lux_day: { min: 6000, max: 12000 }, photoperiod: pp('06:00', '22:00') },
  { id: 'lettuce', name: '상추', source: '가짜 api 예시값', temp_day: { min: 18, max: 24 }, temp_night: { min: 14, max: 19 }, humidity_day: { min: 60, max: 80 }, humidity_night: { min: 65, max: 85 }, soil_moisture: { min: 45, max: 65 }, lux_day: { min: 5000, max: 10000 }, photoperiod: pp('05:00', '21:00') },
  { id: 'basil', name: '바질', source: '가짜 api 예시값', temp_day: { min: 22, max: 28 }, temp_night: { min: 18, max: 22 }, humidity_day: { min: 50, max: 70 }, humidity_night: { min: 55, max: 75 }, soil_moisture: { min: 35, max: 55 }, lux_day: { min: 7000, max: 14000 }, photoperiod: pp('06:00', '20:00') },
];
const cropOf = (id) => CROPS.find((c) => c.id === id) ?? null;

// ── 큐브·카메라 상태 ──
const cubes = {};
const cameras = {};
const snapshots = [];

function makeCube(sn, index) {
  return {
    sn,
    session: `sess-${sn.toLowerCase()}-1`,
    seq: 42,
    ip: `192.168.0.${42 + index * 2}`,
    outputs: { fan1: 100, fan2: 100, led1: 70, led2: 70, pump: false },
    pumpTimer: null,
    pumpBoostAt: 0,
    pumpBoost: 0,
    cal: { air: 20500, water: 9800 },
    crop_id: index === 0 ? 'mini_rose' : null,
    auto: { enabled: false, paused: null, pump_count: 3, pump_last_at: nowS() - 5400, last_action: null },
    sse: 0,
    intervalDays: 1,
    reports: [],
    pending: false, // 등록 코드만 발급되고 장치가 아직 붙지 않은 큐브(설정 보드 장치 등록)
  };
}

// 등록 코드 발급 때 목록에 들어가는 큐브 — 장치가 붙기 전이라 텔레메트리·카메라가 없다.
function addPendingCube(sn) {
  if (cubes[sn]) return { cube: cubes[sn], already: true };
  cubes[sn] = makeCube(sn, Object.keys(cubes).length);
  cubes[sn].pending = true;
  return { cube: cubes[sn], already: false };
}

function addCube(sn) {
  if (cubes[sn]) return { cube: cubes[sn], already: true };
  const index = Object.keys(cubes).length;
  cubes[sn] = makeCube(sn, index);
  addCamera(sn, 1, '생육 카메라');
  seedReports(cubes[sn]);
  return { cube: cubes[sn], already: false };
}

function addCamera(cube, number, name) {
  const sn = `${cube}-C${number}`;
  if (cameras[sn]) return { cam: cameras[sn], already: true };
  const index = Object.keys(cameras).length;
  cameras[sn] = {
    sn, cube, number, name: name ?? `카메라 ${number}`,
    status: { fw: CAM_FW, ip: `192.168.0.${43 + index * 2}`, mac: `A4:CF:12:3B:9E:${(index + 1).toString(16).padStart(2, '0').toUpperCase()}`, rssi_dbm: -58, chip_temp: 51.3 },
    stream: { on: false, since: null, until: null, frames: 0, last_frame: null, viewers: 0 },
    last_file: null, last_taken: null,
  };
  seedSnapshots(cameras[sn]);
  return { cam: cameras[sn], already: false };
}

function cameraInfo(cam) {
  const t = Date.now();
  return {
    sn: cam.sn, cube: cam.cube, number: cam.number, name: cam.name,
    seen: cam.erased ? null : offline() ? nowS() - 900 : nowS() - 20, // erased: 초기화(설정값 지우기) 뒤 서버에서 끊긴 흉내
    status: { ...cam.status, rssi_dbm: cam.status.rssi_dbm + Math.round(2 * Math.sin(t / 8e3)), chip_temp: r1(cam.status.chip_temp + 0.6 * Math.sin(t / 6e4)) },
    last_file: cam.last_file, last_taken: cam.last_taken,
    stream: { ...cam.stream },
  };
}

// ── 환경 모형: 시각 → 측정값. 실시간 값과 이력이 같은 식에서 나온다 ──
function lightOn(minute, crop) {
  const on = crop.photoperiod.on_min, off = crop.photoperiod.off_min;
  return on < off ? minute >= on && minute < off : minute >= on || minute < off;
}

// 조명 on/off 를 45분 시정수로 따라가는 0~1 (온도·습도용)
function thermal(minute, crop) {
  const since = (a, b) => (((a - b) % 1440) + 1440) % 1440;
  return lightOn(minute, crop)
    ? 1 - Math.exp(-since(minute, crop.photoperiod.on_min) / 45)
    : Math.exp(-since(minute, crop.photoperiod.off_min) / 45);
}

function minuteOf(tMs) {
  const d = new Date(tMs);
  return d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60;
}

function model(tMs, c, live = false) {
  const minute = minuteOf(tMs);
  const crop = cropOf(c.crop_id) ?? CROPS[0];
  const th = thermal(minute, crop);
  const s = SHIFT[MODE] ?? SHIFT.ok;
  const w1 = Math.sin(tMs / 1.7e6), w2 = Math.sin(tMs / 4.1e5 + 1), w3 = Math.sin(tMs / 6.6e5 + 2);
  const temperature = 19.8 + 4.6 * th + 0.35 * w1 + 0.2 * w2 + s.temperature;
  const humidity = clamp(74 - 11 * th - 1.8 * w1 + 0.6 * w3 + s.humidity, 25, 97);
  // 이력은 광주기대로 LED 70% 가 켜져 있었다고 보고, 실시간은 지금 LED 출력을 따른다
  const ledLevel = live ? c.outputs.led1 : lightOn(minute, crop) ? 70 : 0;
  const lux = ledLevel > 0 ? clamp((8000 + 300 * w3) * (ledLevel / 70) + s.lux, 0, 60000) : 0;
  // 00:15·06:15·12:15·18:15 급수 뒤 서서히 마르는 톱니
  const age = (((minute - 15) % 360) + 360) % 360;
  let moisture = 42 + 12 * Math.exp(-age / 200) + 0.4 * w2 + s.moisture_pct;
  if (live && c.pumpBoost > 0) {
    const dt = tMs - c.pumpBoostAt;
    moisture += c.pumpBoost * (1 - Math.exp(-dt / 60e3)) * Math.exp(-dt / 1.44e7);
  }
  return { temperature: r1(temperature), humidity: r1(humidity), lux: Math.round(lux), moisture_pct: r1(clamp(moisture, 3, 95)) };
}

const rpm = (pct, t) => (pct > 0 ? Math.round(600 + pct * 18 + 25 * Math.sin(t / 7e3)) : 0);

function telemetry(c) {
  const t = Date.now();
  const m = model(t, c, true);
  const o = c.outputs;
  const raw = Math.round(c.cal.air - (m.moisture_pct / 100) * (c.cal.air - c.cal.water));
  return {
    session: c.session, uptime_ms: t - BOOT + 4_820_000, applied_seq: c.seq,
    ads1115_ok: true, moisture_calibrated: true,
    moisture_raw: raw, moisture_v: Math.round(raw * 0.125) / 1000, moisture_pct: m.moisture_pct,
    fan1_rpm: rpm(o.fan1, t), fan2_rpm: rpm(o.fan2, t + 1234), led1: o.led1, led2: o.led2,
    pump: o.pump, fan1: o.fan1, fan2: o.fan2,
    lease_active: true, pump_locked: false, rssi_dbm: -54 + Math.round(2 * Math.sin(t / 9e3)),
    temperature: m.temperature, humidity: m.humidity, lux: m.lux,
    temp_1: r1(m.temperature - 0.2), hum_1: r1(m.humidity + 0.5), temp_2: r1(m.temperature + 0.2), hum_2: r1(m.humidity - 0.5), sht45_count: 2,
    chip_temp: r1(47.5 + 0.8 * Math.sin(t / 5e4) + (o.fan1 + o.fan2) / 200),
  };
}

const statusFrame = (c) => ({
  name: 'smartcube-node', vendor: 'helpai', model: 'CUBE-V1', sn: c.sn, fw: FW, ip: c.ip, transport: 'mqtts',
  session: c.session, last_received_seq: c.seq, outputs_enabled: true, lease_ms: 15000, pump_max_ms: PUMP_MAX_MS, protocol: 'v1',
  ads1115_ok: true, cal_pending: false, diag_mode: false, air: c.cal.air, water: c.cal.water,
});

const cubeSummary = (c) => ({ sn: c.sn, session: c.pending ? null : c.session, seen: c.pending ? null : seenOf(), has_values: !c.pending, crop_id: c.crop_id, auto_mode: c.auto.enabled });

// ── 제어 ──
function startPump(c) {
  clearTimeout(c.pumpTimer);
  c.outputs.pump = true;
  c.pumpBoostAt = Date.now();
  c.pumpBoost = 8;
  c.auto.pump_count += 1;
  c.auto.pump_last_at = nowS();
  c.pumpTimer = setTimeout(() => { c.outputs.pump = false; log(`${c.sn} 펌프 ${PUMP_MAX_MS / 1000}초 지나 자동 정지`); }, PUMP_MAX_MS);
}

function stopPump(c) {
  clearTimeout(c.pumpTimer);
  c.pumpTimer = null;
  c.outputs.pump = false;
}

function applyControl(c, body) {
  const sent = {};
  for (const k of ['fan1', 'fan2', 'led1', 'led2']) {
    if (body[k] === undefined) continue;
    const v = Number(body[k]);
    if (!Number.isFinite(v) || v < 0 || v > 100) return { sn: c.sn, seq: c.seq, sent: {}, result: 'invalid_fields' };
    sent[k] = Math.round(v);
  }
  if (body.pump !== undefined) {
    if (typeof body.pump !== 'boolean') return { sn: c.sn, seq: c.seq, sent: {}, result: 'invalid_fields' };
    sent.pump = body.pump;
  }
  Object.assign(c.outputs, sent);
  c.seq += 1;
  if (sent.pump === true && !c.pumpTimer) startPump(c);
  else if (sent.pump === false) stopPump(c);
  // 사람이 조작하면 그 큐브의 자동 제어는 꺼진다(api 규칙)
  if (c.auto.enabled) { c.auto.enabled = false; c.auto.paused = null; }
  log(`${c.sn} 제어 ${JSON.stringify(sent)} → seq ${c.seq}`);
  return { sn: c.sn, seq: c.seq, sent: { ...c.outputs, ...sent }, result: 'ok' };
}

// ── 자동 제어 ──
function autoRules(crop, phase) {
  const t = phase === 'night' ? crop.temp_night : crop.temp_day;
  const h = phase === 'night' ? crop.humidity_night : crop.humidity_day;
  return [
    `온도 ${t.min}~${t.max}°C 밖이면 배기팬 단계 조정`,
    `습도 ${h.min}~${h.max}% 밖이면 순환팬·배기팬 조정`,
    `토양 수분 ${crop.soil_moisture.min}% 아래면 급수 ${PUMP_MAX_MS / 1000}초(재급수 간격 30분)`,
    `LED ${crop.photoperiod.on}~${crop.photoperiod.off} 점등, 주간 조도 ${crop.lux_day.min}~${crop.lux_day.max} lx`,
  ];
}

function autoView(c) {
  const crop = cropOf(c.crop_id);
  const enabled = c.auto.enabled;
  let state = null;
  if (crop) {
    const t = Date.now();
    const day = lightOn(minuteOf(t), crop);
    const phase = day ? 'day' : 'night';
    const m = model(t, c, true);
    const ranges = {
      temperature: ['온도', day ? crop.temp_day : crop.temp_night, m.temperature],
      humidity: ['습도', day ? crop.humidity_day : crop.humidity_night, m.humidity],
      lux: ['조도', day ? crop.lux_day : null, m.lux],
      moisture_pct: ['토양 수분', crop.soil_moisture, m.moisture_pct],
    };
    const excursions = {};
    const alerts = [];
    if (enabled) {
      for (const [item, [label, rg, v]] of Object.entries(ranges)) {
        if (!rg || (v >= rg.min && v <= rg.max)) continue;
        const side = v < rg.min ? 'low' : 'high';
        excursions[item] = { side, since: nowS() - 300, value: v, min: rg.min, max: rg.max };
        alerts.push(`${label} ${side === 'low' ? '하한 미달' : '상한 초과'} (${v}, 범위 ${rg.min}~${rg.max})`);
      }
    }
    state = {
      phase, rules: autoRules(crop, phase), alerts, excursions, events: [],
      paused: c.auto.paused, fails: 0, last_eval: enabled ? nowS() - 12 : null,
      last_action: enabled ? (c.auto.last_action ?? { t: nowS() - 90, sent: { fan1: 66 }, result: 'ok', rules: ['온도 상한 접근: 배기팬 2단'] }) : null,
      pump_count: c.auto.pump_count, pump_last_at: c.auto.pump_last_at,
    };
  }
  return { sn: c.sn, crop_id: c.crop_id, crop, crop_invalid: null, enabled, available: true, interval_s: 60, state };
}

// ── 사진(스냅샷) ──
function pushSnapshot(cam, tMs, trigger, zoom) {
  const date = kstDate(tMs);
  const file = `${kstHms(tMs)}.jpg`;
  const row = {
    camera: cam.sn, cube: cam.cube, date, file, path: `${cam.cube}/${cam.sn}/${date}/${file}`,
    bytes: SNAP?.length ?? 0, taken_at: iso(tMs), trigger,
    ...(zoom && zoom !== 1 ? { zoom_pct: Math.round(zoom * 100) } : {}),
  };
  snapshots.push(row);
  cam.last_file = row.path;
  cam.last_taken = row.taken_at;
  return row;
}

function seedSnapshots(cam) {
  const now = Date.now();
  for (const dayBack of [1, 0]) {
    const date = kstDate(now - dayBack * 86400e3);
    for (const h of [6, 9, 12, 15, 18, 21]) {
      const t = kstAt(date, h);
      if (t <= now) pushSnapshot(cam, t, 'schedule');
    }
  }
}

// ── AI 리포트 ──
function anchors() {
  const now = Date.now();
  const today = kstAt(kstDate(now), 2);
  return now < today ? { last: today - 86400e3, next: today } : { last: today, next: today + 86400e3 };
}

function reportResult(c, startMs, endMs, atMs) {
  const crop = cropOf(c.crop_id);
  const keys = ['temperature', 'humidity', 'lux', 'moisture_pct'];
  const acc = Object.fromEntries(keys.map((k) => [k, { min: Infinity, max: -Infinity, sum: 0, n: 0, inRange: 0 }]));
  for (let t = startMs; t <= endMs; t += 300e3) {
    const m = model(t, c);
    const day = crop ? lightOn(minuteOf(t), crop) : true;
    const range = crop
      ? { temperature: day ? crop.temp_day : crop.temp_night, humidity: day ? crop.humidity_day : crop.humidity_night, lux: day ? crop.lux_day : null, moisture_pct: crop.soil_moisture }
      : {};
    for (const k of keys) {
      if (k === 'lux' && !day) continue;
      const v = m[k], a = acc[k], rg = range[k];
      a.min = Math.min(a.min, v); a.max = Math.max(a.max, v); a.sum += v; a.n += 1;
      if (!rg || (v >= rg.min && v <= rg.max)) a.inRange += 1;
    }
  }
  const stat = (k, unit, d) => {
    const a = acc[k];
    return a.n ? { unit, min: round(a.min, d), max: round(a.max, d), avg: round(a.sum / a.n, d), in_range_pct: Math.round((a.inRange / a.n) * 100) } : null;
  };
  const env = { temperature: stat('temperature', '°C', 1), humidity: stat('humidity', '%', 1), lux_day: stat('lux', 'lx', 0), soil_moisture: stat('moisture_pct', '%', 1) };
  const labels = { temperature: '온도', humidity: '습도', lux_day: '주간 조도', soil_moisture: '토양 수분' };
  const findings = Object.entries(env)
    .filter(([, s]) => s && s.in_range_pct < 100)
    .map(([k, s]) => `${labels[k]}가 전체의 ${100 - s.in_range_pct}% 구간에서 허용 범위를 벗어남 (최저 ${s.min}${s.unit}, 최고 ${s.max}${s.unit})`);
  if (findings.length === 0) findings.push('모든 항목이 작물 허용 범위 안에 있었음');
  const hours = (endMs - startMs) / 3600e3;
  const pumpRuns = Math.max(1, Math.round(hours / 6));
  return {
    schema: 'smartcube-report/v1',
    cube: c.sn, instance: IID,
    crop: crop ? { id: crop.id, name: crop.name } : null,
    period: { start: iso(startMs), end: iso(endMs), hours: r1(hours) },
    environment: env,
    control: { led_on_hours: r1(hours * 16 / 24), fan1_level_changes: 4, fan2_level_changes: 2, pump_runs: pumpRuns, pump_seconds_total: pumpRuns * 30, auto_mode: c.auto.enabled },
    findings,
    recommendations: [
      '주간 습도가 상한에 가까워지는 오후에는 순환팬을 2단 이상으로 유지',
      '급수 직후 토양 수분이 상한을 넘지 않도록 1회 급수 시간은 30초 유지',
      '다음 회차부터 생육 사진의 잎 면적 변화를 함께 비교',
    ],
    generated_at: iso(atMs),
    writer: 'jetson app/report.py',
    llm: { model: 'gpu-server/llm', latency_s: 41.2 },
  };
}

function makeJob(c, anchorMs, opts = {}) {
  const date = kstDate(anchorMs);
  const kind = opts.kind ?? 'scheduled';
  return {
    job_id: `${c.sn.toLowerCase()}-${date}-${kind === 'now' ? `now-${Date.now().toString(36)}` : '01'}`,
    instance: IID, cube: c.sn, date, anchor: iso(anchorMs),
    period_start: iso(opts.start ?? anchorMs - 86400e3), period_end: iso(opts.end ?? anchorMs),
    interval_days: 1, part: null, kind, status: 'pending', progress: null, manual: Boolean(opts.manual), reason: null,
    created_at: iso(opts.createdAt ?? anchorMs), started_at: null, done_at: null, result_bytes: null,
    retries: 0, attempt_count: 0, last_attempt: null,
    result: null, attempts: [],
  };
}

function finishJob(c, job, atMs) {
  job.result = reportResult(c, Date.parse(job.period_start), Date.parse(job.period_end), atMs);
  job.status = 'done';
  job.progress = null;
  job.started_at = job.started_at ?? iso(atMs - 95e3);
  job.done_at = iso(atMs);
  job.result_bytes = Buffer.byteLength(JSON.stringify(job.result));
  job.attempt_count += 1;
  job.last_attempt = { at: iso(atMs), window: job.kind === 'now' ? 'now' : job.retries > 0 ? 'manual' : 'main', ok: true, error: null, kind: null };
  job.attempts.push(job.last_attempt);
}

function failJob(job, atMs, error) {
  job.status = 'failed';
  job.progress = null;
  job.attempt_count += 1;
  job.last_attempt = { at: iso(atMs), window: 'main', ok: false, error, kind: 'error' };
  job.attempts.push(job.last_attempt);
}

// 받기·재요청: 1.5초 뒤 작성 중 → 3단계 진행 → 8초 뒤 완료
function runJobLater(c, job) {
  job.status = 'pending';
  job.progress = null;
  setTimeout(() => { job.status = 'writing'; job.started_at = iso(Date.now()); }, 1500);
  const steps = [[2500, 1, 'fetch', '측정 이력 조회'], [4500, 2, 'graph', '그래프 작성'], [6500, 3, 'llm', 'LLM 요약 작성']];
  for (const [ms, step, name, label] of steps) setTimeout(() => { if (job.status === 'writing') job.progress = { step, total: 3, name, label }; }, ms);
  setTimeout(() => { finishJob(c, job, Date.now()); log(`${c.sn} 리포트 ${job.job_id} 완료`); }, 8500);
}

function seedReports(c) {
  const { last } = anchors();
  for (let k = 0; k < 3; k++) {
    const anchorMs = last - k * 86400e3;
    const job = makeJob(c, anchorMs);
    if (k === 2) { failJob(job, anchorMs + 125e3, 'LLM 서버 응답 시간 초과(120초)'); failJob(job, anchorMs + 1800e3 + 125e3, 'LLM 서버 응답 시간 초과(120초)'); job.retries = 1; }
    else { job.started_at = iso(anchorMs + 4e3); finishJob(c, job, anchorMs + 98e3); }
    c.reports.push(job);
  }
}

function reportSettings(c) {
  const { last, next } = anchors();
  return {
    sn: c.sn, interval_days: c.intervalDays, min: 1, max: 30, anchor_time: '02:00', windows: ['02:00-02:10', '02:30-02:40'],
    last_anchor: iso(last), next_anchor: iso(next), next_period_start: iso(next - c.intervalDays * 86400e3),
    now_max_hours: 24, llm_ready: true, graph: 'v1',
  };
}

const publicJob = ({ result, attempts, ...job }) => job;

// ── 이력 ──
function parseSpan(s) {
  const m = /^-(\d+)(m|h|d)$/.exec(s ?? '');
  if (!m) return 24 * 3600e3;
  return Number(m[1]) * (m[2] === 'm' ? 60e3 : m[2] === 'h' ? 3600e3 : 86400e3);
}

function parseEvery(s) {
  const m = /^(\d+)(s|m|h)$/.exec(s ?? '');
  if (!m) return 60e3;
  return Number(m[1]) * (m[2] === 's' ? 1e3 : m[2] === 'm' ? 60e3 : 3600e3);
}

function history(c, url) {
  const fieldParam = url.searchParams.get('field');
  const fields = (fieldParam ? fieldParam.split(',') : ['temperature', 'humidity', 'lux', 'moisture_pct']).map((f) => f.trim()).filter(Boolean);
  const span = parseSpan(url.searchParams.get('start'));
  const every = parseEvery(url.searchParams.get('every'));
  const now = Date.now();
  const startMs = Math.floor((now - span) / every) * every;
  const rows = [];
  for (let t = startMs; t <= now; t += every) {
    const m = model(t, c);
    for (const f of fields) if (m[f] !== undefined) rows.push({ time: iso(t), field: f, value: m[f] });
  }
  return { sn: c.sn, start: iso(now - span), field: fieldParam, count: rows.length, rows };
}

// ── 응답 도우미 ──
const CORS = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*', 'Access-Control-Allow-Methods': '*' };

function send(res, body, code = 200) {
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', ...CORS });
  res.end(JSON.stringify(body));
  return code;
}

function readBody(req) {
  return new Promise((resolve) => {
    const chunks = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => {
      const text = Buffer.concat(chunks).toString('utf8');
      try { resolve(text ? JSON.parse(text) : {}); } catch { resolve({}); }
    });
  });
}

const me = () => ({
  sub: 'demo-user', email: 'demo@smartcube.local', instances: [IID], operator: false, grants: {}, auth_mode: 'off',
  instance_id: IID, instance_name: INSTANCE_NAME, is_owner: true, access: 'owner', grant: null, cubes: Object.keys(cubes),
});

const broker = { instance_id: IID, username: `inst-${IID}`, password: null, host: 'jetson.local', port: 8883, tls: true, app: 'smartcube', ready: true };
const brokerInfo = () => ({ ...broker, cubes: Object.keys(cubes) });

// ── 설정 보드 장치 등록 (2026-11-16, 계획서 edgeserver-JS/2026-11-16_설정보드장치등록_계획서 §2·§6·§7) ──
// 1) 대시보드가 큐브 시리얼로 일회용 등록 코드를 발급한다(24시간 유효). 큐브는 이때 목록에 들어가고 장치가 붙기 전까지 '미등록'.
// 2) 설정 보드(<큐브>-AP)가 사용자가 넣은 코드로 /dev/provision/register 를 부르면 인스턴스 ID·브로커 비밀번호를 돌려준다.
// 3) 같은 큐브의 기본 구성(메인 보드 1 + 카메라 2)이 모두 붙으면 'done' — 실제 서버는 이때 설정 보드에 AP 정지를 명령한다.
// 4) 등록 뒤 PROVISION_TIMEOUT_S 안에 다 붙지 않으면 'failed'(대시보드에 재시도 안내).
// 가짜 api 에는 브로커가 없어서 장치 접속은 흉내 낸다: MOCK_PROVISION_SIM=auto(몇 초 간격으로 셋 다 붙음) ·
// fail(카메라 2 가 안 붙어 시간 초과) · manual(/mock/provision/connect/<장치 SN> 으로 하나씩 붙임).
const AP_ADDR = process.env.MOCK_AP_ADDR ?? '192.168.4.1';
const CODE_TTL_S = 24 * 3600;
let PROVISION_TIMEOUT_S = Number(process.env.MOCK_PROVISION_TIMEOUT_S ?? 600); // 실행 중 /mock/provision/sim 으로 바꿀 수 있다
let PROVISION_SIM = process.env.MOCK_PROVISION_SIM ?? 'auto';
const CODE_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // 헷갈리는 0·O·1·I 제외(32자)
const CUBE_SN_RE = /^CUBE\d{3}$/; // 실제 api SN_REGISTER_RE 와 같은 형식
const SETUP_SN_RE = /^(CUBE\d{3})-AP$/;
const provisionCodes = [];

// 설정 보드로 가는 명령(AP 정지·설정값 지우기)은 랩탑 mosquitto 의 mosquitto_pub 로 발행한다(로컬 시험, 2026-11-16 사용자 결정).
// 토픽 device/<앱>/<설정 보드 SN>/notify/<명령>. mosquitto_pub 가 없으면 기록만 남긴다.
const DEVICE_NAME = process.env.MOCK_DEVICE_NAME ?? 'helpai.cube';
const MQTT_HOST = process.env.MOCK_MQTT_HOST ?? '127.0.0.1';
const MQTT_PORT = process.env.MOCK_MQTT_PORT ?? '1883';
const MOSQ_PUB = process.env.MOCK_MQTT_PUB
  ?? ['C:\\Program Files\\mosquitto\\mosquitto_pub.exe', '/usr/bin/mosquitto_pub', '/usr/local/bin/mosquitto_pub', '/opt/homebrew/bin/mosquitto_pub'].find((p) => existsSync(p))
  ?? null;
function publishCommand(boardSn, cmd) {
  const topic = `device/${DEVICE_NAME}/${boardSn}/notify/${cmd}`;
  if (!MOSQ_PUB) { log(`(mosquitto_pub 없음) ${topic} 발행 생략`); return false; }
  execFile(MOSQ_PUB, ['-h', MQTT_HOST, '-p', String(MQTT_PORT), '-t', topic, '-m', JSON.stringify({ cmd })], (err) => {
    log(err ? `${topic} 발행 실패: ${err.message}` : `${topic} 발행`);
  });
  return true;
}

const normCode =(s) => String(s ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '');
function newCode() {
  for (;;) {
    const raw = Array.from({ length: 8 }, () => CODE_CHARS[randomInt(CODE_CHARS.length)]).join('');
    const code = `${raw.slice(0, 4)}-${raw.slice(4)}`;
    if (!provisionCodes.some((p) => p.code === code)) return code;
  }
}
const expectedBoards = (cube) => [cube, `${cube}-C1`, `${cube}-C2`]; // 기본 구성(§7 5-5)
function provisionStatus(p) {
  if (p.status === 'waiting' && nowS() > p.expires_at) return 'expired';
  return p.status;
}
const provisionView = (p) => ({
  code: p.code, sn: p.sn, link: `http://${AP_ADDR}/register?code=${p.code}`,
  issued_at: p.issued_at, expires_at: p.expires_at, status: provisionStatus(p),
  registered_at: p.registered_at, done_at: p.done_at, failed_at: p.failed_at,
  setup_sn: p.setup_sn, expected: p.expected, connected: p.connected, timeout_s: PROVISION_TIMEOUT_S,
});

function issueProvisionCode(cube) {
  for (const old of provisionCodes) {
    if (old.sn === cube && (old.status === 'waiting' || old.status === 'registered')) old.status = 'replaced';
  }
  const { already } = addPendingCube(cube);
  const p = {
    code: newCode(), sn: cube, issued_at: nowS(), expires_at: nowS() + CODE_TTL_S, status: 'waiting',
    registered_at: null, done_at: null, failed_at: null, setup_sn: null, expected: expectedBoards(cube), connected: [], timers: [],
  };
  provisionCodes.push(p);
  log(`등록 코드 발급 ${p.code} → ${cube}${already ? '' : ' (목록에 새로 추가, 미등록)'}`);
  return p;
}

function provisionConnect(p, board) {
  if (p.status !== 'registered' || p.connected.includes(board) || !p.expected.includes(board)) return;
  p.connected.push(board);
  const cube = cubes[p.sn];
  if (board === p.sn && cube) cube.pending = false;
  const m = /^CUBE\d{3}-C(\d+)$/.exec(board);
  if (m) addCamera(p.sn, Number(m[1]), `카메라 ${m[1]}`);
  log(`${board} 서버 연결 (${p.connected.length}/${p.expected.length})`);
  if (p.expected.every((b) => p.connected.includes(b))) {
    p.status = 'done';
    p.done_at = nowS();
    p.timers.forEach(clearTimeout);
    log(`${p.sn} 기본 구성 모두 연결 → ${p.setup_sn} 에 AP 정지 명령`);
    publishCommand(p.setup_sn, 'ap_stop');
  }
}

function registerSetupBoard(body) {
  const p = provisionCodes.find((x) => normCode(x.code) === normCode(body.code));
  if (!p) return status({ detail: '없는 등록 코드' }, 404);
  const st = provisionStatus(p);
  const why = { expired: '유효 시간(24시간)이 지난 코드', replaced: '새 코드가 발급되어 무효가 된 코드', done: '이미 등록이 끝난 코드', failed: '등록에 실패한 코드 — 새로 발급하세요', registered: '이미 등록 중인 코드' };
  if (st !== 'waiting') return status({ detail: why[st] ?? st }, 409);
  const setup = String(body.sn ?? '').trim().toUpperCase();
  const m = SETUP_SN_RE.exec(setup);
  if (!m) return status({ detail: '설정 보드 시리얼 형식이 아님(<큐브>-AP)' }, 422);
  if (m[1] !== p.sn) return status({ detail: `이 코드는 ${p.sn} 큐브용입니다(설정 보드 ${setup})` }, 403);
  p.status = 'registered';
  p.registered_at = nowS();
  p.setup_sn = setup;
  p.timers.push(setTimeout(() => {
    if (p.status !== 'registered') return;
    p.status = 'failed';
    p.failed_at = nowS();
    log(`${p.sn} 등록 시간 초과(${PROVISION_TIMEOUT_S}초) — 붙은 장치 ${p.connected.join(', ') || '없음'}`);
  }, PROVISION_TIMEOUT_S * 1000));
  const plan = PROVISION_SIM === 'manual' ? [] : PROVISION_SIM === 'fail' ? p.expected.slice(0, 2) : p.expected;
  plan.forEach((board, i) => p.timers.push(setTimeout(() => provisionConnect(p, board), 3000 + i * 2500)));
  log(`설정 보드 ${setup} 등록 (코드 ${p.code}, 장치 흉내 ${PROVISION_SIM})`);
  return { ok: true, instance_id: IID, broker_password: 'mock-broker-password', cube: p.sn, expected: p.expected, timeout_s: PROVISION_TIMEOUT_S };
}

function serveSse(req, res, c) {
  res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive', ...CORS });
  c.sse += 1;
  const push = () => { if (!offline() && !c.pending) res.write(`data: ${JSON.stringify({ sn: c.sn, t: nowS(), values: telemetry(c) })}\n\n`); };
  push();
  const timer = setInterval(push, 2000);
  const keep = setInterval(() => res.write(': keepalive\n\n'), 15000);
  req.on('close', () => { clearInterval(timer); clearInterval(keep); c.sse = Math.max(0, c.sse - 1); });
}

function serveMjpeg(req, res, cam) {
  if (!JPEG) return send(res, { detail: '개발용도구/camera-mock.jpg 가 없어 스트림을 만들 수 없음' }, 503);
  res.writeHead(200, { 'Content-Type': 'application/octet-stream', 'X-Mjpeg-Boundary': 'frame', 'Cache-Control': 'no-cache', Connection: 'keep-alive', ...CORS });
  cam.stream.viewers += 1;
  const head = Buffer.from(`--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${JPEG.length}\r\n\r\n`);
  const frame = Buffer.concat([head, JPEG, Buffer.from('\r\n')]);
  const timer = setInterval(() => {
    if (offline()) return;
    res.write(frame);
    cam.stream.frames += 1;
    cam.stream.last_frame = nowS();
  }, 200);
  req.on('close', () => { clearInterval(timer); cam.stream.viewers = Math.max(0, cam.stream.viewers - 1); });
  return 200;
}

// ── 라우트 ──
const sn = (ctx, i = 1) => decodeURIComponent(ctx.m[i]);
const cubeOr404 = (ctx, i = 1) => cubes[sn(ctx, i)] ?? null;
// 오류 응답은 이 꼴로 돌려준다(배열 응답과 섞이지 않게)
const status = (body, code) => ({ __code: code, __body: body });
const notFound = (what) => status({ detail: what }, 404);

const GLOBAL = [
  ['GET', /^\/health$/, () => ({ ok: true, mqtt_connected: !offline(), instances: 1, cubes: Object.keys(cubes).length, uptime_s: Math.floor((Date.now() - BOOT) / 1000) + 4820, auth_mode: 'off' })],
  ['GET', /^\/me$/, () => me()],
  ['POST', /^\/claim$/, (ctx) => ({ instance_id: IID, sn: String(ctx.body.sn ?? '').toUpperCase(), already: true })],
  ['GET', /^\/mock\/state$/, () => ({ mode: MODE, cubes: Object.values(cubes).map((c) => ({ sn: c.sn, outputs: c.outputs, crop_id: c.crop_id, auto: c.auto.enabled, seq: c.seq })), cameras: Object.values(cameras).map(cameraInfo) })],
  [/GET|POST/, /^\/mock\/mode\/([a-z]+)$/, (ctx) => {
    if (!SHIFT[ctx.m[1]]) return status({ detail: `모르는 모드: ${ctx.m[1]} (ok|warn|critical|offline)` }, 400);
    MODE = ctx.m[1];
    log(`시연 모드 → ${MODE}`);
    return { ok: true, mode: MODE };
  }],
  // 설정 보드 장치 등록 — 장치 쪽(설정 보드가 부름). 실제 api 는 /dev/… 장치 라우트에 둔다.
  ['POST', /^\/dev\/provision\/register$/, (ctx) => registerSetupBoard(ctx.body)],
  // 시험용: 장치 흉내 방식·시간 초과를 실행 중에 바꾼다(예: /mock/provision/sim/fail?timeout=20). 다음 등록부터 적용.
  [/GET|POST/, /^\/mock\/provision\/sim\/([a-z]+)$/, (ctx) => {
    if (!['auto', 'fail', 'manual'].includes(ctx.m[1])) return status({ detail: `모르는 방식: ${ctx.m[1]} (auto|fail|manual)` }, 400);
    PROVISION_SIM = ctx.m[1];
    const t = Number(ctx.url.searchParams.get('timeout'));
    if (Number.isFinite(t) && t > 0) PROVISION_TIMEOUT_S = t;
    log(`장치 흉내 → ${PROVISION_SIM}, 시간 초과 ${PROVISION_TIMEOUT_S}초`);
    return { ok: true, sim: PROVISION_SIM, timeout_s: PROVISION_TIMEOUT_S };
  }],
  // 시험용: 대시보드 지우기 명령 흉내 — 장치에 notify/erase 발행(예: /mock/provision/erase/CUBE002-AP)
  [/GET|POST/, /^\/mock\/provision\/erase\/([^/]+)$/, (ctx) => {
    const board = sn(ctx).toUpperCase();
    return { ok: publishCommand(board, 'erase'), board, topic: `device/${DEVICE_NAME}/${board}/notify/erase`, mosquitto_pub: MOSQ_PUB };
  }],
  // 시험용: MOCK_PROVISION_SIM=manual 일 때 장치를 하나씩 붙인다(예: /mock/provision/connect/CUBE002-C1)
  [/GET|POST/, /^\/mock\/provision\/connect\/([^/]+)$/, (ctx) => {
    const board = sn(ctx).toUpperCase();
    const p = [...provisionCodes].reverse().find((x) => x.status === 'registered' && x.expected.includes(board));
    if (!p) return notFound(`등록 중인 코드에 ${board} 가 없음`);
    provisionConnect(p, board);
    return provisionView(p);
  }],
];

const INSTANCE = [
  ['GET', /^\/me$/, () => me()],
  ['GET', /^\/instance\/status$/, () => ({
    version: API_VERSION, instance_id: IID, uptime_s: Math.floor((Date.now() - BOOT) / 1000) + 4820, mqtt_connected: !offline(),
    rx: { values: 1820 + Math.floor((Date.now() - BOOT) / 2000), status: 12 + Object.keys(cubes).length },
    cubes: Object.fromEntries(Object.values(cubes).map((c) => [c.sn, { session: c.session, last_seq: c.seq, seen: seenOf(), has_values: true, has_status: true, fw: FW }])),
    writer: { enabled: true, started: true, queued: 0, written_lines: 18204 + Math.floor((Date.now() - BOOT) / 2000) * 4, failed_batches: 0, retried_batches: 0, last_ok: nowS() - 1, last_error: null },
    audit: { enabled: false, sent: 0, failed: 0, last_error: null },
    auth: 'off', sse_subscribers: Object.fromEntries(Object.values(cubes).map((c) => [c.sn, c.sse])),
  })],
  ['GET', /^\/crops$/, () => ({ crops: CROPS, invalid: {}, file_error: null })],
  ['GET', /^\/cubes$/, () => Object.values(cubes).map(cubeSummary)],
  ['POST', /^\/cubes$/, (ctx) => {
    const id = String(ctx.body.sn ?? '').trim().toUpperCase();
    if (!/^[A-Z0-9_-]{3,32}$/.test(id)) return status({ detail: '큐브 일련번호 형식이 아님' }, 422);
    const { already } = addCube(id);
    return { instance_id: IID, sn: id, already, cubes: Object.keys(cubes) };
  }],
  ['GET', /^\/cubes\/([^/]+)\/values$/, (ctx) => { const c = cubeOr404(ctx); return c ? { sn: c.sn, seen: seenOf(), session: c.session, values: telemetry(c) } : notFound(`모르는 큐브: ${sn(ctx)}`); }],
  ['GET', /^\/cubes\/([^/]+)\/status$/, (ctx) => { const c = cubeOr404(ctx); if (c?.pending) return notFound('아직 상태 프레임 없음(장치 미등록)'); return c ? { sn: c.sn, status: statusFrame(c) } : notFound(`모르는 큐브: ${sn(ctx)}`); }],
  ['GET', /^\/cubes\/([^/]+)\/history$/, (ctx) => { const c = cubeOr404(ctx); return c ? history(c, ctx.url) : notFound(`모르는 큐브: ${sn(ctx)}`); }],
  ['GET', /^\/cubes\/([^/]+)\/stream$/, (ctx) => { const c = cubeOr404(ctx); if (!c) return notFound(`모르는 큐브: ${sn(ctx)}`); serveSse(ctx.req, ctx.res, c); }],
  ['POST', /^\/cubes\/([^/]+)\/control$/, (ctx) => { const c = cubeOr404(ctx); return c ? applyControl(c, ctx.body) : notFound(`모르는 큐브: ${sn(ctx)}`); }],
  ['POST', /^\/cubes\/([^/]+)\/refresh$/, (ctx) => { const c = cubeOr404(ctx); return c ? { sn: c.sn, requested: true } : notFound(`모르는 큐브: ${sn(ctx)}`); }],
  ['POST', /^\/cubes\/([^/]+)\/config$/, (ctx) => {
    const c = cubeOr404(ctx);
    if (!c) return notFound(`모르는 큐브: ${sn(ctx)}`);
    const sent = {};
    if (typeof ctx.body.air === 'number') { c.cal.air = ctx.body.air; sent.air = ctx.body.air; }
    if (typeof ctx.body.water === 'number') { c.cal.water = ctx.body.water; sent.water = ctx.body.water; }
    if (ctx.body.cal === 'reset') { c.cal = { air: 20500, water: 9800 }; sent.cal = 0; }
    return { sn: c.sn, sent, air: c.cal.air, water: c.cal.water, cal_pending: false, ads1115_ok: true };
  }],
  ['GET', /^\/cubes\/([^/]+)\/auto$/, (ctx) => { const c = cubeOr404(ctx); return c ? autoView(c) : notFound(`모르는 큐브: ${sn(ctx)}`); }],
  ['PUT', /^\/cubes\/([^/]+)\/auto$/, (ctx) => {
    const c = cubeOr404(ctx);
    if (!c) return notFound(`모르는 큐브: ${sn(ctx)}`);
    const enabled = Boolean(ctx.body.enabled);
    if (enabled && !c.crop_id) return status({ detail: '작물을 먼저 선택하세요' }, 409);
    c.auto.enabled = enabled;
    c.auto.paused = null;
    log(`${c.sn} 자동 제어 ${enabled ? '켬' : '끔'}`);
    return autoView(c);
  }],
  ['PUT', /^\/cubes\/([^/]+)\/crop$/, (ctx) => {
    const c = cubeOr404(ctx);
    if (!c) return notFound(`모르는 큐브: ${sn(ctx)}`);
    const id = ctx.body.crop_id ?? null;
    if (id !== null && !cropOf(id)) return status({ detail: `작물표에 없는 작물: ${id}` }, 404);
    c.crop_id = id;
    if (id === null) c.auto.enabled = false;
    log(`${c.sn} 작물 → ${id ?? '해제'}`);
    return autoView(c);
  }],
  // 사진
  ['GET', /^\/cubes\/([^/]+)\/snapshots$/, (ctx) => {
    const c = cubeOr404(ctx);
    if (!c) return notFound(`모르는 큐브: ${sn(ctx)}`);
    const cam = ctx.url.searchParams.get('cam'), date = ctx.url.searchParams.get('date');
    const limit = Number(ctx.url.searchParams.get('limit') ?? 50) || 50;
    const rows = snapshots.filter((r) => r.cube === c.sn && (!cam || r.camera === cam) && (!date || r.date === date)).sort((a, b) => b.taken_at.localeCompare(a.taken_at)).slice(0, limit);
    return { cube: c.sn, camera: cam, count: rows.length, rows, cameras: Object.values(cameras).filter((x) => x.cube === c.sn).map((x) => x.sn) };
  }],
  ['POST', /^\/cubes\/([^/]+)\/snapshot$/, (ctx) => {
    const c = cubeOr404(ctx);
    if (!c) return notFound(`모르는 큐브: ${sn(ctx)}`);
    const cam = ctx.body.cam ? cameras[ctx.body.cam] : Object.values(cameras).find((x) => x.cube === c.sn);
    if (!cam || cam.cube !== c.sn) return notFound('이 큐브에 카메라가 없음');
    const row = pushSnapshot(cam, Date.now(), 'manual', ctx.body.zoom);
    log(`${cam.sn} 촬영 → ${row.path}`);
    return { ...row, camera: cam.sn, taken_at: row.taken_at };
  }],
  ['GET', /^\/cubes\/([^/]+)\/snapshots\/([^/]+)\/([^/]+)\/([^/]+)$/, (ctx) => {
    const row = snapshots.find((r) => r.cube === sn(ctx, 1) && r.camera === sn(ctx, 2) && r.date === ctx.m[3] && r.file === sn(ctx, 4));
    if (!row || !SNAP) return notFound('사진 없음');
    ctx.res.writeHead(200, { 'Content-Type': 'image/jpeg', 'Content-Length': SNAP.length, ...CORS });
    ctx.res.end(SNAP);
  }],
  // AI 리포트
  ['GET', /^\/cubes\/([^/]+)\/report-settings$/, (ctx) => { const c = cubeOr404(ctx); return c ? reportSettings(c) : notFound(`모르는 큐브: ${sn(ctx)}`); }],
  ['PUT', /^\/cubes\/([^/]+)\/report-settings$/, (ctx) => {
    const c = cubeOr404(ctx);
    if (!c) return notFound(`모르는 큐브: ${sn(ctx)}`);
    const days = Number(ctx.body.interval_days);
    if (!Number.isInteger(days) || days < 1 || days > 30) return status({ detail: '간격은 1~30일' }, 422);
    c.intervalDays = days;
    return reportSettings(c);
  }],
  ['GET', /^\/cubes\/([^/]+)\/reports$/, (ctx) => {
    const c = cubeOr404(ctx);
    if (!c) return notFound(`모르는 큐브: ${sn(ctx)}`);
    const limit = Number(ctx.url.searchParams.get('limit') ?? 50) || 50;
    const list = [...c.reports].sort((a, b) => b.created_at.localeCompare(a.created_at));
    return { sn: c.sn, reports: list.slice(0, limit).map(publicJob), total: list.length };
  }],
  ['POST', /^\/cubes\/([^/]+)\/reports\/now$/, (ctx) => {
    const c = cubeOr404(ctx);
    if (!c) return notFound(`모르는 큐브: ${sn(ctx)}`);
    const start = Date.parse(ctx.body.start), end = Date.parse(ctx.body.end);
    if (!(start < end)) return status({ detail: '시작 시각이 끝 시각보다 앞이어야 함' }, 422);
    if (end - start > 24 * 3600e3) return status({ detail: '범위는 최대 24시간' }, 422);
    const job = makeJob(c, Date.now(), { kind: 'now', manual: true, start, end, createdAt: Date.now() });
    c.reports.push(job);
    runJobLater(c, job);
    log(`${c.sn} 리포트 즉시 요청 ${job.job_id}`);
    return publicJob(job);
  }],
  ['GET', /^\/cubes\/([^/]+)\/reports\/([^/]+)$/, (ctx) => {
    const c = cubeOr404(ctx);
    const job = c?.reports.find((j) => j.job_id === sn(ctx, 2));
    return job ? job : notFound('없는 회차');
  }],
  ['POST', /^\/cubes\/([^/]+)\/reports\/([^/]+)\/retry$/, (ctx) => {
    const c = cubeOr404(ctx);
    const job = c?.reports.find((j) => j.job_id === sn(ctx, 2));
    if (!job) return notFound('없는 회차');
    if (job.status !== 'failed') return status({ detail: '실패한 회차만 다시 작성할 수 있음' }, 409);
    job.retries += 1;
    job.manual = true;
    runJobLater(c, job);
    return publicJob(job);
  }],
  // 카메라
  ['GET', /^\/cameras$/, () => Object.values(cameras).map(cameraInfo)],
  ['POST', /^\/cameras$/, (ctx) => {
    const cube = String(ctx.body.cube ?? '').toUpperCase(), number = Number(ctx.body.number);
    if (!cubes[cube]) return notFound(`모르는 큐브: ${cube}`);
    if (!Number.isInteger(number) || number < 1 || number > 9) return status({ detail: '카메라 번호는 1~9' }, 422);
    const { cam, already } = addCamera(cube, number, ctx.body.name);
    return { camera: cam.sn, cube, already, cameras: Object.keys(cameras) };
  }],
  ['POST', /^\/cameras\/([^/]+)\/stream\/start$/, (ctx) => {
    const cam = cameras[sn(ctx)];
    if (!cam) return notFound(`모르는 카메라: ${sn(ctx)}`);
    cam.stream.on = true;
    cam.stream.since = nowS();
    cam.stream.until = nowS() + 300;
    return cameraInfo(cam);
  }],
  ['POST', /^\/cameras\/([^/]+)\/stream\/stop$/, (ctx) => {
    const cam = cameras[sn(ctx)];
    if (!cam) return notFound(`모르는 카메라: ${sn(ctx)}`);
    cam.stream.on = false;
    cam.stream.until = null;
    return cameraInfo(cam);
  }],
  ['GET', /^\/cameras\/([^/]+)\/stream$/, (ctx) => {
    const cam = cameras[sn(ctx)];
    if (!cam) return notFound(`모르는 카메라: ${sn(ctx)}`);
    serveMjpeg(ctx.req, ctx.res, cam);
  }],
  // 설정 보드 장치 등록 — 대시보드 쪽(소유자). 발급 = 지금의 "장치 추가"와 합침(§7 5-8)
  ['POST', /^\/provision\/codes$/, (ctx) => {
    const cube = String(ctx.body.sn ?? '').trim().toUpperCase();
    if (!CUBE_SN_RE.test(cube)) return status({ detail: '큐브 시리얼 형식이 아님 (예: CUBE002)' }, 422);
    return provisionView(issueProvisionCode(cube));
  }],
  ['GET', /^\/provision\/codes$/, () => [...provisionCodes].reverse().slice(0, 20).map(provisionView)],
  // 장치 연결 페이지 햄버거 메뉴(2026-11-18 사용자 결정): 초기화 = 설정값 지우기 명령(장치는 목록에 남음),
  // 장치 제거 = 목록에서 빼기(큐브 제어기를 빼면 그 큐브의 카메라도 함께). 실제 api 0.9.7 과 같게.
  // 초기화 = 큐브 전체(메인 보드·카메라·설정 보드, 2026-11-18 사용자 결정). 카메라 하나만 초기화하는 경로는 없앴다.
  ['POST', /^\/cubes\/([^/]+)\/erase$/, (ctx) => {
    const c = cubeOr404(ctx);
    if (!c) return notFound(`모르는 큐브: ${sn(ctx)}`);
    const cams = [...new Set([...Object.keys(cameras).filter((k) => cameras[k].cube === c.sn), `${c.sn}-C1`, `${c.sn}-C2`])].sort();
    const targets = [c.sn, ...cams, `${c.sn}-AP`];
    const sent = targets.map((t) => publishCommand(t, 'erase')).every(Boolean);
    c.pending = true; // 장치가 설정값을 지우고 다시 시작해 서버에서 끊긴 흉내(텔레메트리 멈춤)
    for (const k of cams) if (cameras[k]) { cameras[k].erased = true; cameras[k].stream.on = false; }
    log(`${c.sn} 큐브 전체 초기화 명령 → ${targets.join(', ')}`);
    return { sn: c.sn, sent, targets };
  }],
  // 장치 제거 = 연결 초기화를 먼저 보낸 뒤 목록에서 빼기(2026-11-18 사용자 결정, 실제 api 와 같게)
  ['DELETE', /^\/cubes\/([^/]+)$/, (ctx) => {
    const c = cubeOr404(ctx);
    if (!c) return notFound(`모르는 큐브: ${sn(ctx)}`);
    const cams = Object.keys(cameras).filter((k) => cameras[k].cube === c.sn);
    const targets = [c.sn, ...[...new Set([...cams, `${c.sn}-C1`, `${c.sn}-C2`])].sort(), `${c.sn}-AP`];
    const eraseSent = targets.map((t) => publishCommand(t, 'erase')).every(Boolean);
    for (const k of cams) delete cameras[k];
    for (let i = snapshots.length - 1; i >= 0; i--) if (snapshots[i].cube === c.sn) snapshots.splice(i, 1);
    clearTimeout(c.pumpTimer);
    delete cubes[c.sn];
    log(`${c.sn} 장치 제거 (연결 초기화 먼저 ${targets.join(', ')} · 카메라 ${cams.join(', ') || '없음'} 함께)`);
    return { sn: c.sn, removed: [c.sn, ...cams], cubes: Object.keys(cubes), erase_targets: targets, erase_sent: eraseSent };
  }],
  ['DELETE', /^\/cameras\/([^/]+)$/, (ctx) => {
    const cam = cameras[sn(ctx)];
    if (!cam) return notFound(`모르는 카메라: ${sn(ctx)}`);
    const eraseSent = publishCommand(cam.sn, 'erase');
    delete cameras[cam.sn];
    for (let i = snapshots.length - 1; i >= 0; i--) if (snapshots[i].camera === cam.sn) snapshots.splice(i, 1);
    log(`${cam.sn} 장치 제거 (연결 초기화 먼저)`);
    return { sn: cam.sn, removed: [cam.sn], cameras: Object.keys(cameras), erase_targets: [cam.sn], erase_sent: eraseSent };
  }],
  // 브로커·운영자
  ['GET', /^\/broker$/, () => brokerInfo()],
  ['POST', /^\/broker\/rotate$/, () => { broker.password = `pw-${Math.random().toString(36).slice(2, 10)}`; const out = brokerInfo(); broker.password = null; return out; }],
  ['POST', /^\/operator\/access$/, () => ({ ...me(), is_owner: false, access: 'operator', grant: { until: nowS() + 3600, issued_at: nowS(), reason: '점검' } })],
  ['DELETE', /^\/operator\/access$/, () => ({ ...me(), is_owner: false, access: 'none' })],
];

function match(table, method, pathname) {
  for (const [m, re, handler] of table) {
    if (typeof m === 'string' ? m !== method : !m.test(method)) continue;
    const found = re.exec(pathname);
    if (found) return { handler, m: found };
  }
  return null;
}

for (const id of CUBE_SNS) addCube(id);

createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
  if (req.method === 'OPTIONS') { res.writeHead(204, CORS); return res.end(); }
  let pathname = url.pathname.replace(/\/+$/, '') || '/';
  let table = GLOBAL;
  const inst = /^\/i\/([a-z0-9][a-z0-9_-]*)(\/.*)?$/.exec(pathname);
  if (inst) {
    if (inst[1] !== IID) return send(res, { detail: `없는 인스턴스: ${inst[1]}` }, 404);
    pathname = inst[2] || '/';
    table = INSTANCE;
  }
  const hit = match(table, req.method, pathname);
  if (!hit) { log(req.method, url.pathname, '→ 404'); return send(res, { detail: `mock: 모르는 경로 ${req.method} ${url.pathname}` }, 404); }
  const body = req.method === 'POST' || req.method === 'PUT' ? await readBody(req) : {};
  const out = hit.handler({ req, res, url, m: hit.m, body });
  if (out === undefined) { log(req.method, url.pathname, '→ 스트림'); return; }
  const code = out && out.__code ? out.__code : 200;
  const payload = out && out.__code ? out.__body : out;
  send(res, payload, code);
  if (!/\/(values|history|stream)$/.test(pathname) || code !== 200) log(req.method, url.pathname, '→', code);
}).listen(PORT, HOST, () => {
  log(`가짜 api 실행 중 → http://${HOST}:${PORT}  (인스턴스 ${IID}, 큐브 ${Object.keys(cubes).join(', ')}, 모드 ${MODE})`);
  if (!JPEG) log('camera-mock.jpg 가 없어 카메라 스트림·사진은 503/404 로 응답한다');
});
