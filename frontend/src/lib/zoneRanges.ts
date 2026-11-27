// 화면의 허용 범위 (2026-10-06, 작물 기반 자동 제어).
//
// 사용자 결정(2026-10-06):
//   · 범위의 출처는 서버 작물표(api app/crops.json). 큐브에 배정한 작물 ID 로 찾는다.
//   · 주간/야간 = 그 작물의 광주기(켜는 시각~끄는 시각 = 주간). 온도·습도는 주야 범위가 따로, 토양 수분은 하루 종일 한 범위,
//     조도는 주간 목표 범위만(야간은 없음).
//   · 작물이 배정되지 않은 큐브는 범위 '—'(경고·그래프 띠 없음). CO₂·EC·pH 는 작물표에 없으므로 항상 '—'.
// 백엔드 큐브는 zone.ranges 를 반드시 채운다(없으면 NO_RANGES). zone.ranges 가 없는 것은 시험 픽스처(mockFarmData)뿐이고
// 그때는 예전 targetTemp·targetHumidity 등을 그대로 쓴다.
import type { ValueRange, ZoneConfig, ZoneRanges } from '../types/smartfarm';
import type { CropProfile } from './backend';

export type RangeKey = keyof ZoneRanges;

export const NO_RANGES: ZoneRanges = { temp: null, humidity: null, co2: null, ec: null, ph: null, soil: null, lux: null };

export function rangeOf(zone: ZoneConfig, key: RangeKey): ValueRange | null {
  if (zone.ranges) return zone.ranges[key] ?? null;
  switch (key) {
    case 'temp':
      return { min: zone.targetTemp.min, max: zone.targetTemp.max };
    case 'humidity':
      return zone.targetHumidity;
    case 'co2':
      return zone.targetCo2;
    case 'ec':
      return zone.targetEc;
    case 'ph':
      return zone.targetPh;
    default:
      return null;
  }
}

// "20~26°C" 꼴. 범위가 없으면 '—'. scale 은 표시 단위 환산(조도 lx → klx 는 1/1000).
export function rangeText(range: ValueRange | null, unit: string, scale = 1, decimals?: number): string {
  if (!range) return '—';
  const f = (v: number) => (decimals === undefined ? String(Math.round(v * scale * 1000) / 1000) : (v * scale).toFixed(decimals));
  return `${f(range.min)}~${f(range.max)}${unit}`;
}

// 서버 판정과 같은 규칙: 분 단위, 끄는 시각이 켜는 시각보다 이르면 자정을 넘긴다.
export function cropIsDay(crop: Pick<CropProfile, 'photoperiod'>, at: Date): boolean {
  const { on_min: on, off_min: off } = crop.photoperiod;
  const minute = at.getHours() * 60 + at.getMinutes();
  return on < off ? minute >= on && minute < off : minute >= on || minute < off;
}

export function rangesFromCrop(crop: CropProfile | null | undefined, at: Date): ZoneRanges {
  if (!crop) return NO_RANGES;
  const day = cropIsDay(crop, at);
  return {
    temp: day ? crop.temp_day : crop.temp_night,
    humidity: day ? crop.humidity_day : crop.humidity_night,
    co2: null,
    ec: null,
    ph: null,
    soil: crop.soil_moisture,
    lux: day ? crop.lux_day : null,
  };
}

// 화면의 광주기 표기(0~24, 소수점은 분)
export function photoperiodHours(crop: Pick<CropProfile, 'photoperiod'>): { on: number; off: number } {
  return { on: crop.photoperiod.on_min / 60, off: crop.photoperiod.off_min / 60 };
}
