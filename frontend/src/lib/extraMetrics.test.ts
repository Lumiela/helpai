import { test } from 'node:test';
import assert from 'node:assert/strict';
import { extraMetricsOf, extraText, hasChart } from './extraMetrics';
import { NO_RANGES } from './zoneRanges';
import type { ZoneConfig } from '../types/smartfarm';

const zone = (live: boolean, sensors: Partial<ZoneConfig['sensors']>) =>
  ({ live, ranges: NO_RANGES, sensors: { ec: null, co2: null, ph: null, ...sensors } }) as unknown as ZoneConfig;

test('기록 화면 추가 항목: EC·CO2 는 이력 필드와 배율, pH 는 지금 값만', () => {
  const list = extraMetricsOf(zone(true, { ec: 1.28, co2: 420, ph: 6.4 }));
  assert.deepEqual(list.map((m) => [m.key, m.field, m.scale]), [['ec', 'soil_ec', 1 / 1000], ['co2', 'co2_ppm', undefined], ['ph', null, undefined]]);
  assert.deepEqual(list.filter(hasChart).map((m) => m.key), ['ec', 'co2']);
  assert.deepEqual(list.map(extraText), ['1.28 dS/m', '420 ppm', '6.4']);
  assert.ok(list.every((m) => m.range === null));
});

test('센서가 없거나 연결이 끊기면 추가 항목은 —', () => {
  assert.deepEqual(extraMetricsOf(zone(true, {})).map(extraText), ['—', '—', '—']);
  assert.deepEqual(extraMetricsOf(zone(false, { ec: 1.28, co2: 420, ph: 6.4 })).map(extraText), ['—', '—', '—']);
});
