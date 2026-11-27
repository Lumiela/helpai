// 기록 화면에 더하는 항목 (2026-11-25 사용자 결정): EC·CO₂ 는 그래프와 지금 값, pH 는 지금 값만.
// 작물표에 EC·CO₂·pH 범위가 없으므로 백엔드 큐브에서는 range 가 항상 null 이다(범위 띠·상태어를 만들지 않는다).
import type { ValueRange, ZoneConfig } from '../types/smartfarm';
import { rangeOf } from './zoneRanges';
import { fmt } from './deviceMap';

export interface ChartMetric {
  key: string;
  label: string;
  unit: string;
  decimals: number;
  range: ValueRange | null;
  field: string; // 이력 필드
  scale?: number; // 저장 단위 → 화면 단위
}

export interface ExtraMetric extends Omit<ChartMetric, 'field'> {
  key: 'ec' | 'co2' | 'ph';
  value: number | null; // 센서가 없거나 연결이 끊기면 null
  field: string | null; // 그래프가 없는 항목(pH)은 null
}

export function extraMetricsOf(zone: ZoneConfig): ExtraMetric[] {
  const v = (x: number | null) => (zone.live ? x : null);
  return [
    { key: 'ec', label: 'EC', unit: 'dS/m', decimals: 2, range: rangeOf(zone, 'ec'), value: v(zone.sensors.ec), field: 'soil_ec', scale: 1 / 1000 },
    { key: 'co2', label: 'CO₂', unit: 'ppm', decimals: 0, range: rangeOf(zone, 'co2'), value: v(zone.sensors.co2), field: 'co2_ppm' },
    { key: 'ph', label: 'pH', unit: '', decimals: 1, range: rangeOf(zone, 'ph'), value: v(zone.sensors.ph), field: null },
  ];
}

export const hasChart = (m: ExtraMetric): m is ExtraMetric & { field: string } => m.field !== null;

// "1.28 dS/m" 꼴. 값이 없으면 '—'
export function extraText(m: Pick<ExtraMetric, 'value' | 'unit' | 'decimals'>): string {
  if (m.value === null) return '—';
  return m.unit ? `${fmt(m.value, m.decimals)} ${m.unit}` : fmt(m.value, m.decimals);
}
