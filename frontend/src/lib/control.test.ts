import { test } from 'node:test';
import assert from 'node:assert/strict';
import { INITIAL_ACTUATORS, INITIAL_ZONES } from '../data/mockFarmData';
import { automaticDampers, automaticControl, scheduledMode, updateActuator as updateAtTime } from './actuatorControl';
import { IrrigationTimers } from './irrigationTimers';

const zone = INITIAL_ZONES[0];
const initial = INITIAL_ACTUATORS[zone.id];
const noon = new Date(2026, 8, 14, 12);
const updateActuator = (current: Parameters<typeof updateAtTime>[0], updates: Parameters<typeof updateAtTime>[1], target: Parameters<typeof updateAtTime>[2]) => updateAtTime(current, updates, target, noon);

test('수동 댐퍼는 센서 갱신에도 유지되고 반대편 댐퍼는 정지한다', () => {
  const heating = updateActuator(initial, { heatingDamper: true }, zone);
  assert.equal(heating.autoMode, false);
  assert.equal(heating.coolingDamper, false);
  assert.equal(heating.heatingDamper, true);
  assert.deepEqual(automaticDampers(heating, zone), heating);
  const cooling = updateActuator(heating, { coolingDamper: true }, zone);
  assert.equal(cooling.coolingDamper, true);
  assert.equal(cooling.heatingDamper, false);
  const invalid = updateActuator(cooling, { coolingDamper: true, heatingDamper: true }, zone);
  assert.equal(invalid.coolingDamper || invalid.heatingDamper, false);
});

test('자동 제어 재개와 목표 온도 경계에서 댐퍼가 상호 배타적으로 동작한다', () => {
  const manual = { ...initial, autoMode: false, heatingDamper: true, coolingDamper: false };
  assert.equal(updateActuator(manual, { autoMode: true }, zone).coolingDamper, true);
  for (const delta of [-2, -0.5, 0, 0.5, 2]) {
    const sample = { ...zone, sensors: { ...zone.sensors, temperature: zone.targetTemp.currentTarget + delta } };
    const result = automaticDampers(initial, sample);
    assert.equal(result.coolingDamper, delta > 0.5);
    assert.equal(result.heatingDamper, delta < -0.5);
    assert.equal(result.coolingDamper && result.heatingDamper, false);
  }
});

test('적용한 프리셋 이름은 센서 갱신에는 유지되고 직접 제어하면 해제된다', () => {
  const loaded = updateActuator(initial, { autoMode: true, activePresetName: '토마토 기본 설정' }, zone);
  assert.equal(automaticDampers(loaded, zone).activePresetName, '토마토 기본 설정');
  assert.equal(updateActuator(loaded, { heatingDamper: true }, zone).activePresetName, null);
  assert.equal(updateActuator(loaded, { ledIntensity: 50 }, zone).activePresetName, null);
  assert.equal(updateActuator(loaded, { autoMode: false }, zone).activePresetName, null);
  const second = updateActuator(loaded, { autoMode: false, activePresetName: '야간 설정' }, zone);
  assert.equal(second.activePresetName, '야간 설정');
});

test('일반 급수는 180초, 모의 급수는 5초이며 이전 타이머를 취소한다', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const timers = new IrrigationTimers();
  const complete: string[] = [];
  timers.start('normal', false, () => complete.push('normal'));
  timers.start('demo', true, () => complete.push('demo'));
  t.mock.timers.tick(4_999);
  assert.deepEqual(complete, []);
  t.mock.timers.tick(1);
  assert.deepEqual(complete, ['demo']);
  t.mock.timers.tick(174_999);
  assert.deepEqual(complete, ['demo']);
  t.mock.timers.tick(1);
  assert.deepEqual(complete, ['demo', 'normal']);

  timers.start('restart', true, () => complete.push('stale'));
  t.mock.timers.tick(2_000);
  timers.start('restart', false, () => complete.push('replacement'));
  t.mock.timers.tick(5_000);
  assert.equal(complete.includes('stale'), false);
  timers.cancel('restart');
  timers.start('other', false, () => complete.push('cancelled'));
  timers.cancelAll();
  t.mock.timers.tick(180_000);
  assert.deepEqual(complete, ['demo', 'normal']);
});

test('팬·밝기·점등·급수 등 직접 조작은 자동 제어를 끈다', () => {
  for (const changes of [
    { outerFan: false, outerFanSpeed: 0 }, { innerFanSpeed: 3 },
    { ledIntensity: 25 }, { ledGrowLight: false }, { ledMode: 'night' as const },
    { irrigationPump: true }, { co2Generator: false },
  ]) {
    const manual = updateActuator(initial, changes, zone);
    assert.equal(manual.autoMode, false);
    assert.deepEqual(automaticDampers(manual, zone), manual);
  }
});

test('여러 수동 변경 후 자동 재개는 원래 설정을 복원하고 현재 온도로 댐퍼를 다시 계산한다', () => {
  let manual = updateActuator(initial, { outerFan: false, outerFanSpeed: 0 }, zone);
  manual = updateActuator(manual, { innerFan: true, innerFanSpeed: 3, ledGrowLight: false, ledIntensity: 0 }, zone);
  manual = updateActuator(manual, { heatingDamper: true, irrigationPump: true, nutrientValve: true }, zone);
  const cold = { ...zone, sensors: { ...zone.sensors, temperature: zone.targetTemp.currentTarget - 2 } };
  const resumed = updateActuator(manual, { autoMode: true }, cold);
  assert.equal(resumed.autoMode, true);
  for (const key of ['outerFan', 'outerFanSpeed', 'innerFan', 'innerFanSpeed', 'ledIntensity', 'ledGrowLight', 'ledMode'] as const) {
    assert.equal(resumed[key], initial[key], key);
  }
  assert.equal(resumed.coolingDamper, false);
  assert.equal(resumed.heatingDamper, true);
  assert.equal(resumed.irrigationPump, false);
  assert.equal(resumed.nutrientValve, false);
});

test('자동 프리셋과 각 큐브의 복원 설정은 서로 덮어쓰지 않는다', () => {
  const otherZone = INITIAL_ZONES[1];
  const other = INITIAL_ACTUATORS[otherZone.id];
  const loaded = updateActuator(initial, { autoMode: true, ledIntensity: 45, activePresetName: '시험 설정' }, zone);
  const manual = updateActuator(loaded, { ledIntensity: 5 }, zone);
  assert.equal(updateActuator(manual, { autoMode: true }, zone).ledIntensity, 45);
  const otherManual = updateActuator(other, { ledIntensity: 10 }, otherZone);
  assert.equal(updateActuator(otherManual, { autoMode: true }, otherZone).ledIntensity, other.ledIntensity);
});


test('자동 광주기는 경계 시각에 LED와 주·야간 목표 온도를 함께 전환한다', () => {
  const sample = { ...zone, sensors: { ...zone.sensors, temperature: 22 } };
  const day = automaticControl(initial, sample, new Date(2026, 8, 14, 21, 59, 59));
  const night = automaticControl(day, sample, new Date(2026, 8, 14, 22));
  assert.equal(day.ledIntensity, 70);
  assert.equal(day.heatingDamper, true);
  assert.equal(night.ledMode, 'night');
  assert.equal(night.ledIntensity, 0);
  assert.equal(night.ledGrowLight, false);
  assert.equal(night.coolingDamper, true);
  const sunrise = automaticControl(night, sample, new Date(2026, 8, 15, 6));
  assert.equal(sunrise.ledIntensity, 70);
  assert.equal(sunrise.heatingDamper, true);
});

test('자정 통과 및 종일 점등 일정이 주야간 경계에 일관된다', () => {
  const wrap = {...zone, photoperiod:{on:22,off:6}};
  for (const [hour, mode] of [[21,'night'],[22,'day'],[0,'day'],[6,'night']] as const) {
    assert.equal(scheduledMode(wrap, new Date(2026,8,14,hour)), mode);
  }
  const all = {...zone,photoperiod:{on:6,off:6}};
  assert.equal(scheduledMode(all,new Date(2026,8,14,6)), 'day');
  assert.equal(scheduledMode(all,new Date(2026,8,14,0)), 'day');
});

test('수동 주야간 선택과 자동 야간 복원은 주간 밝기를 잃지 않는다', () => {
  const bright = {...initial,ledIntensity:85};
  const manualNight = updateActuator(bright,{ledMode:'night'},zone);
  assert.equal(manualNight.ledIntensity,0);
  assert.equal(manualNight.autoMode,false);
  const manualDay = updateActuator(manualNight,{ledMode:'day'},zone);
  assert.equal(manualDay.ledIntensity,85);
  const autoNight = automaticControl(bright,zone,new Date(2026,8,14,23));
  const changed = updateActuator(autoNight,{ledIntensity:15},zone);
  const resumed = updateAtTime(changed,{autoMode:true},zone,new Date(2026,8,15,1));
  assert.equal(resumed.ledIntensity,0);
  assert.equal(automaticControl(resumed,zone,noon).ledIntensity,85);
});
