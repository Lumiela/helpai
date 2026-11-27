import type { ValueRange, ZoneConfig } from '../types/smartfarm';
import { rangeOf } from './zoneRanges';

// 실측이 없는 항목(null)과 범위가 없는 항목(작물 미배정 등)은 경고하지 않는다.
export function zoneState(zone: ZoneConfig) {
  const outside = (value: number | null, range: ValueRange | null) =>
    typeof value === 'number' && range !== null && (value < range.min || value > range.max);
  const tempWarn = outside(zone.sensors.temperature, rangeOf(zone, 'temp'));
  const humidWarn = outside(zone.sensors.humidity, rangeOf(zone, 'humidity'));
  const co2Warn = outside(zone.sensors.co2, rangeOf(zone, 'co2'));
  const ecWarn = outside(zone.sensors.ec, rangeOf(zone, 'ec'));
  const phWarn = outside(zone.sensors.ph, rangeOf(zone, 'ph'));
  return { tempWarn, humidWarn, co2Warn, ecWarn, phWarn, warn: tempWarn || humidWarn || co2Warn || ecWarn || phWarn };
}
