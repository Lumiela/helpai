// SMART CUBE 모바일 앱(2차, 2026-11-24) 공용 데이터 도우미.
// 수치는 모두 실제 API 값(SSE 텔레메트리·작물 프로필·장치 보고 상태)에서 나온다. 값이 없으면 null → 화면은 "—".
import type { ActuatorState, ValueRange, ZoneConfig } from '../types/smartfarm';
import type { CropProfile } from '../lib/backend';
import { rangeOf } from '../lib/zoneRanges';
import { fmt } from '../lib/deviceMap';

export type Level = 'low' | 'ok' | 'high' | 'none';
export type MetricKey = 'temp' | 'air' | 'soil' | 'light';

// 작물을 고르지 않았을 때 쓰는 일반 기준(대부분의 실내 식물이 괜찮은 범위)
export const GENERAL: Record<MetricKey, ValueRange> = { temp: { min: 18, max: 28 }, air: { min: 40, max: 70 }, soil: { min: 35, max: 70 }, light: { min: 5, max: 20 } };

export const WORDS: Record<MetricKey, Record<Exclude<Level, 'none'>, string>> = {
  temp: { low: '조금 추워요', ok: '좋아요', high: '조금 더워요' },
  air: { low: '건조해요', ok: '좋아요', high: '습해요' },
  soil: { low: '말랐어요', ok: '촉촉해요', high: '너무 젖었어요' },
  light: { low: '부족해요', ok: '충분해요', high: '너무 밝아요' },
};

export interface Metric {
  key: MetricKey;
  label: string;
  value: number | null; // 연결이 끊기면 null
  unit: string;
  decimals: number;
  range: ValueRange;
  general: boolean; // 작물 범위가 없어 일반 기준을 쓴 경우
  field: string; // 이력 필드
  scale?: number;
}

export const levelOf = (v: number | null, r: ValueRange): Level => (v === null ? 'none' : v < r.min ? 'low' : v > r.max ? 'high' : 'ok');

export function metricsOf(zone: ZoneConfig): Record<MetricKey, Metric> {
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

export const word = (m: Metric) => { const lv = levelOf(m.value, m.range); return lv === 'none' ? '—' : WORDS[m.key][lv]; };
export const val = (m: Metric) => (m.value === null ? '—' : fmt(m.value, m.decimals));
export const isOff = (m: Metric) => { const lv = levelOf(m.value, m.range); return lv === 'low' || lv === 'high'; };

// 전체 상태: 측정한 항목 중 좋아하는 범위 안에 있는 비율(점수를 꾸며 내지 않는다)
export function overall(zone: ZoneConfig, m: Record<MetricKey, Metric>): { word: string; pct: number | null; inRange: number; measured: number } {
  const list = Object.values(m).filter((x) => x.value !== null);
  if (!zone.live) return { word: '연결 끊김', pct: null, inRange: 0, measured: 0 };
  if (list.length === 0) return { word: '—', pct: null, inRange: 0, measured: 0 };
  const ok = list.filter((x) => levelOf(x.value, x.range) === 'ok').length;
  const pct = Math.round((ok / list.length) * 100);
  return { word: pct === 100 ? '건강해요' : pct >= 50 ? '대체로 괜찮아요' : '돌봐 주세요', pct, inRange: ok, measured: list.length };
}

// ---------- 장치 ----------
export type DeviceKey = 'led' | 'inner' | 'outer' | 'water';
export const FAN_WORDS = ['끔', '약', '중', '강'];
export const reportedOf = (a: ActuatorState, k: DeviceKey): number | boolean =>
  k === 'led' ? (a.ledGrowLight ? a.ledIntensity : 0) : k === 'inner' ? (a.innerFan ? a.innerFanSpeed : 0) : k === 'outer' ? (a.outerFan ? a.outerFanSpeed : 0) : a.irrigationPump;

// 작물 프로필의 조명 시간(켜는 시각~끄는 시각)
export function lightHours(crop: CropProfile | undefined): number | null {
  if (!crop) return null;
  const { on_min: on, off_min: off } = crop.photoperiod;
  return Math.round((((off - on) + 1440) % 1440) / 60 * 10) / 10;
}

// 작물 이름으로 고른 분류(필터 칩용). 작물 프로필에 분류가 없어 이름으로만 나눈다.
export type Category = 'leafy' | 'flower' | 'herb' | 'other';
export function categoryOf(name: string | null): Category {
  if (!name) return 'other';
  if (/장미|꽃|제라늄|베고니아|국화|튤립|팬지/.test(name)) return 'flower';
  if (/바질|민트|로즈마리|라벤더|타임|파슬리|허브/.test(name)) return 'herb';
  if (/상추|케일|시금치|청경채|배추|쑥갓|잎/.test(name)) return 'leafy';
  return 'other';
}

// ---------- 이 기기에만 저장하는 것(서버 기능이 아직 없음) ----------
const KEY = 'smartcube-app2';
export interface LocalData {
  startDate?: string; // 재배 시작일 YYYY-MM-DD
  notes?: Array<{ id: string; at: number; text: string }>;
  germination?: { sown: number | null; sprouted: number | null; at: number | null };
  observed?: string; // 관찰하기를 체크한 날짜(YYYY-MM-DD)
}
export function readLocal(sn: string): LocalData {
  try { return (JSON.parse(localStorage.getItem(KEY) ?? '{}') as Record<string, LocalData>)[sn] ?? {}; } catch { return {}; }
}
export function writeLocal(sn: string, patch: Partial<LocalData>): LocalData {
  let all: Record<string, LocalData> = {};
  try { all = JSON.parse(localStorage.getItem(KEY) ?? '{}'); } catch { /* 저장소를 못 쓰면 새로 시작 */ }
  const next = { ...(all[sn] ?? {}), ...patch };
  all[sn] = next;
  try { localStorage.setItem(KEY, JSON.stringify(all)); } catch { /* 사생활 모드 등 */ }
  return next;
}
export const today = () => new Date().toLocaleDateString('sv-SE'); // YYYY-MM-DD (브라우저 시간대)

// 리포트 결과(JSON)에서 사람이 읽을 요약을 찾는다
export function reportSummary(result: unknown): string | null {
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
