import { ZoneConfig } from '../types/smartfarm';
import { daylightFactor } from './lightSaturation';
import type { HistoryRow } from './backend';
import { rangeOf } from './zoneRanges';

export type ReadingKey = 'temp' | 'humid' | 'co2' | 'light' | 'ec' | 'soil';

export interface HistoryPoint {
  t: number; // epoch ms
  v: number;
}

// 백엔드 이력(InfluxDB 조회 결과)을 그래프 점으로. 시간순 정렬, 숫자가 아닌 값은 버린다.
// scale 은 저장 단위 → 화면 단위 배율(조도: lx 저장, klx 표시 → 1/1000). 기본 1.
export function pointsFromRows(rows: HistoryRow[], scale = 1): HistoryPoint[] {
  return rows
    .map((row) => ({ t: Date.parse(row.time), v: typeof row.value === 'number' ? row.value * scale : Number.NaN }))
    .filter((point) => Number.isFinite(point.t) && Number.isFinite(point.v))
    .sort((a, b) => a.t - b.t);
}

// 문자열 → 32비트 시드
function hashSeed(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

// 결정적 난수 (같은 시드 → 같은 수열). 화면이 다시 그려져도 이력이 흔들리지 않게 한다.
function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const STEP_MIN = 15;
const POINTS = (24 * 60) / STEP_MIN + 1; // 97개, 15분 간격
const DAY_MS = 24 * 3600 * 1000;

// 열린 화면에서는 15분마다 표본을 보존한다. 장시간 중단/시계 역행 시 데모 이력을 새로 만든다.
// (2026-09-22) 화면은 더 이상 이 함수를 쓰지 않는다. 배지 수분은 백엔드 이력(pointsFromRows)으로, 나머지는 실측이 없다.
export function appendHistorySample(samples: HistoryPoint[], zone: ZoneConfig, key: ReadingKey, now: Date): HistoryPoint[] {
  const end = now.getTime();
  const last = samples.at(-1);
  if (!last || end < last.t || end - last.t >= DAY_MS) return buildHistory(zone, key, now);
  if (end - last.t < STEP_MIN * 60 * 1000) return samples;
  const value = currentValue(zone, key);
  if (value === null) return samples;
  const next = [...samples, { t: end, v: value }];
  const first = next.findIndex((point) => point.t >= end - DAY_MS);
  return next.slice(Math.max(0, first - 1));
}

export function visibleHistory(samples: HistoryPoint[], now: Date, value: number): HistoryPoint[] {
  if (samples.length === 0) return [];
  const end = now.getTime();
  const start = end - DAY_MS;
  const points = [...samples.filter((point) => point.t < end), { t: end, v: value }];
  const first = points.findIndex((point) => point.t >= start);
  if (first <= 0) return points;
  const left = points[first - 1];
  const right = points[first];
  if (right.t === start) return points.slice(first);
  const boundary = { t: start, v: left.v + (right.v - left.v) * (start - left.t) / (right.t - left.t) };
  return [boundary, ...points.slice(first)];
}

// 최근 24시간 모형 이력. 마지막 점은 현재 센서값과 일치하도록 평행 이동한다.
// (2026-09-22) 테스트 픽스처와 호환을 위해 남겨 둔다. 화면은 쓰지 않는다.
export function buildHistory(zone: ZoneConfig, key: ReadingKey, end: Date): HistoryPoint[] {
  const rand = mulberry32(hashSeed(`${zone.id}:${key}`));
  const { on, off } = zone.photoperiod;
  const endMs = end.getTime();
  const startMs = endMs - 24 * 3600 * 1000;

  // 완만한 잡음: 이전 값을 70% 유지하는 임의 보행
  let noise = 0;
  const nextNoise = (amp: number) => {
    noise = noise * 0.7 + (rand() * 2 - 1) * amp * 0.3;
    return noise;
  };

  const raw: HistoryPoint[] = [];
  for (let i = 0; i < POINTS; i++) {
    const t = startMs + i * STEP_MIN * 60 * 1000;
    const d = new Date(t);
    const hour = d.getHours() + d.getMinutes() / 60;
    const day = daylightFactor(on, off, hour, 2);
    let v = 0;
    switch (key) {
      case 'temp':
        v = zone.targetTemp.nightTarget + (zone.targetTemp.currentTarget - zone.targetTemp.nightTarget) * day + nextNoise(0.9);
        break;
      case 'humid': {
        const mid = (zone.targetHumidity.min + zone.targetHumidity.max) / 2;
        v = mid - 5 * (day - 0.5) + nextNoise(3.5);
        break;
      }
      case 'co2': {
        const mid = (zone.targetCo2.min + zone.targetCo2.max) / 2;
        v = mid + 140 * (day - 0.5) + nextNoise(60);
        break;
      }
      case 'light':
        v = (zone.sensors.ledIntensityKlx ?? 0) * daylightFactor(on, off, hour, 1) + (day > 0 ? nextNoise(0.5) * day : 0);
        break;
      case 'ec': {
        const phase = ((t / 3600000) % 24) / 24;
        v = (zone.sensors.ec ?? 0) + 0.12 * Math.sin(phase * Math.PI * 2) + nextNoise(0.08);
        break;
      }
      case 'soil': {
        // 6시간마다 급수 후 서서히 마르는 톱니 모양
        const cycle = ((t / 3600000) % 6) / 6;
        v = (zone.sensors.soilMoisture ?? 0) + 6 - 12 * cycle + nextNoise(1.2);
        break;
      }
    }
    raw.push({ t, v });
  }

  // 마지막 점을 현재값에 맞춘다 (광량은 현재값 자체가 일정에 따르므로 그대로 둔다)
  const current = currentValue(zone, key) ?? 0;
  const shift = key === 'light' ? 0 : current - raw[raw.length - 1].v;
  return raw.map((p, i) => ({ t: p.t, v: i === raw.length - 1 ? current : roundTo(p.v + shift, key) }));
}

export function currentValue(zone: ZoneConfig, key: ReadingKey): number | null {
  switch (key) {
    case 'temp':
      return zone.sensors.temperature;
    case 'humid':
      return zone.sensors.humidity;
    case 'co2':
      return zone.sensors.co2;
    case 'light':
      return zone.sensors.ledIntensityKlx;
    case 'ec':
      return zone.sensors.ec;
    case 'soil':
      return zone.sensors.soilMoisture;
  }
}

// 그래프의 허용 범위 띠. 2026-10-06: 작물 범위(지금 시간대). 범위가 없으면 띠 없음.
export function allowedRange(zone: ZoneConfig, key: ReadingKey): { min: number; max: number } | undefined {
  const pick = (r: { min: number; max: number } | null, scale = 1) => (r ? { min: r.min * scale, max: r.max * scale } : undefined);
  switch (key) {
    case 'temp':
      return pick(rangeOf(zone, 'temp'));
    case 'humid':
      return pick(rangeOf(zone, 'humidity'));
    case 'co2':
      return pick(rangeOf(zone, 'co2'));
    case 'ec':
      return pick(rangeOf(zone, 'ec'));
    case 'soil':
      return pick(rangeOf(zone, 'soil'));
    case 'light':
      return pick(rangeOf(zone, 'lux'), 1 / 1000); // 화면 단위 klx
    default:
      return undefined;
  }
}

export function decimalsOf(key: ReadingKey): number {
  return key === 'co2' ? 0 : key === 'ec' ? 2 : 1;
}

function roundTo(v: number, key: ReadingKey): number {
  const d = decimalsOf(key);
  const f = Math.pow(10, d);
  return Math.round(v * f) / f;
}
