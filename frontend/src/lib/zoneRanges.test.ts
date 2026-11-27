import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildZone, DEFAULT_PROFILE } from './deviceMap';
import { allowedRange } from './history';
import { zoneState } from './zoneState';
import { cropIsDay, NO_RANGES, photoperiodHours, rangeOf, rangesFromCrop, rangeText } from './zoneRanges';
import type { CropProfile } from './backend';

// 시험용 가짜 작물(실제 작물표 값과 무관)
const CROP: CropProfile = {
  id: 'fake', name: '시험 작물', source: null,
  temp_day: { min: 20, max: 26 }, temp_night: { min: 16, max: 22 },
  humidity_day: { min: 60, max: 80 }, humidity_night: { min: 65, max: 85 },
  soil_moisture: { min: 40, max: 70 }, lux_day: { min: 1000, max: 2000 },
  photoperiod: { on: '06:00', off: '22:00', on_min: 360, off_min: 1320 },
};
const NOON = new Date(2026, 9, 6, 12, 0);
const NIGHT = new Date(2026, 9, 6, 23, 0);

test('작물 범위: 주간·야간 시간대에 따라 온도·습도가 바뀌고 조도는 주간만', () => {
  const day = rangesFromCrop(CROP, NOON);
  assert.deepEqual(day.temp, { min: 20, max: 26 });
  assert.deepEqual(day.humidity, { min: 60, max: 80 });
  assert.deepEqual(day.lux, { min: 1000, max: 2000 });
  const night = rangesFromCrop(CROP, NIGHT);
  assert.deepEqual(night.temp, { min: 16, max: 22 });
  assert.deepEqual(night.humidity, { min: 65, max: 85 });
  assert.equal(night.lux, null);
  assert.deepEqual(night.soil, { min: 40, max: 70 });          // 토양 수분은 하루 종일 한 범위
  assert.equal(day.co2, null);                                  // 작물표에 없는 항목은 '—'
  assert.deepEqual(rangesFromCrop(null, NOON), NO_RANGES);
});

test('광주기: 자정을 넘기는 경우와 화면 시각 표기', () => {
  const wrap = { ...CROP, photoperiod: { on: '22:00', off: '06:00', on_min: 1320, off_min: 360 } };
  assert.equal(cropIsDay(wrap, NIGHT), true);
  assert.equal(cropIsDay(wrap, NOON), false);
  assert.deepEqual(photoperiodHours({ photoperiod: { on: '01:00', off: '23:00', on_min: 60, off_min: 1380 } }), { on: 1, off: 23 });
});

test('작물 미배정 큐브: 범위 —, 경고 없음, 그래프 띠 없음', () => {
  const zone = buildZone('CUBE001', { ...DEFAULT_PROFILE, cropId: null, ranges: NO_RANGES }, {
    values: { session: 's', temperature: 40, humidity: 99 } as never, seen: Date.now(),
  }, null, Date.now());
  assert.equal(rangeText(rangeOf(zone, 'temp'), '°C'), '—');
  assert.equal(zoneState(zone).warn, false);
  assert.equal(allowedRange(zone, 'temp'), undefined);
});

test('작물 배정 큐브: 범위 표기·이탈 경고·조도 띠(klx)', () => {
  const zone = buildZone('CUBE001', { ...DEFAULT_PROFILE, cropId: 'fake', ranges: rangesFromCrop(CROP, NOON) }, {
    values: { session: 's', temperature: 27, humidity: 70 } as never, seen: Date.now(),
  }, null, Date.now());
  assert.equal(rangeText(rangeOf(zone, 'temp'), '°C'), '20~26°C');
  assert.equal(rangeText(rangeOf(zone, 'lux'), ' klx', 1 / 1000), '1~2 klx');
  const s = zoneState(zone);
  assert.equal(s.tempWarn, true);
  assert.equal(s.humidWarn, false);
  assert.deepEqual(allowedRange(zone, 'light'), { min: 1, max: 2 });
  assert.deepEqual(allowedRange(zone, 'soil'), { min: 40, max: 70 });
});

test('시험 픽스처(ranges 없음)는 예전 target* 범위를 그대로 쓴다', () => {
  const zone = buildZone('SC-01', DEFAULT_PROFILE, null, null, Date.now());
  assert.deepEqual(rangeOf(zone, 'temp'), { min: 20, max: 26 });
  assert.deepEqual(rangeOf(zone, 'humidity'), DEFAULT_PROFILE.targetHumidity);
  assert.equal(rangeOf(zone, 'soil'), null);
});
