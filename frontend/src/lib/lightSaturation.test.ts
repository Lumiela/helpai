import { test } from 'node:test';
import assert from 'node:assert/strict';
import { INITIAL_ZONES } from '../data/mockFarmData';
import { daylightFactor, saturationPpfdAt } from './lightSaturation';

test('작물별 광포화점 설정과 광주기를 적용하고 야간에는 곡선이 0으로 내려간다', () => {
  assert.deepEqual(INITIAL_ZONES.map((zone) => saturationPpfdAt(zone.lightSaturation, zone.photoperiod, 12)), [1000, 800, 600]);
  for (const zone of INITIAL_ZONES) {
    for (const hour of [0, zone.photoperiod.on, zone.photoperiod.off, 24]) {
      assert.equal(saturationPpfdAt(zone.lightSaturation, zone.photoperiod, hour), 0);
    }
    const ramp = saturationPpfdAt(zone.lightSaturation, zone.photoperiod, zone.photoperiod.on + 0.5);
    assert.ok(ramp > 0 && ramp < zone.lightSaturation.peakPpfd);
  }
});

test('자정을 넘는 광주기와 종일 점등도 경계에서 일관되게 계산한다', () => {
  assert.equal(daylightFactor(20, 6, 12, 2), 0);
  assert.equal(daylightFactor(20, 6, 0, 2), 1);
  assert.equal(daylightFactor(20, 6, 24, 2), 1);
  assert.equal(daylightFactor(20, 6, 21, 2), 0.5);
  for (const hour of [0, 6, 12, 18, 24]) assert.equal(daylightFactor(6, 6, hour, 2), 1);
});
