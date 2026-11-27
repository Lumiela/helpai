import { test } from 'node:test';
import assert from 'node:assert/strict';
import { INITIAL_ACTUATORS, INITIAL_ZONES } from '../data/mockFarmData';
import { activeSavedPreset, pickSettings, readPresets, STORAGE_KEY, type SavedPreset } from './presets';
import { updateActuator } from './actuatorControl';
import { zoneState } from './zoneState';
import { buildReadings } from '../components/ZoneDetailView';
import { tryCopyCode } from '../components/NotificationConsole';
import { appendHistorySample, buildHistory, pointsFromRows, visibleHistory } from './history';

const zone = INITIAL_ZONES[0];
const actuator = INITIAL_ACTUATORS[zone.id];

test('동명 프리셋은 ID로 구분하고 직접 조작하면 활성 ID와 이름을 해제한다', () => {
  const presets: SavedPreset[] = [25, 75].map((ledIntensity, index) => ({
    id: `preset-${index}`, name: '같은 이름', zoneId: zone.id,
    settings: pickSettings({ ...actuator, autoMode: false, ledIntensity }),
  }));
  const second = presets[1];
  const loaded = updateActuator(actuator, { ...second.settings, activePresetId: second.id, activePresetName: second.name }, zone);
  assert.equal(loaded.ledIntensity, 75);
  assert.equal(activeSavedPreset(presets, zone.id, loaded)?.id, second.id);
  const manual = updateActuator(loaded, { outerFanSpeed: 1 }, zone);
  assert.equal(manual.activePresetId, null);
  assert.equal(manual.activePresetName, null);
  const legacy = { ...loaded, activePresetId: undefined };
  assert.equal(activeSavedPreset(presets, zone.id, legacy), undefined);
  assert.equal(activeSavedPreset([second], zone.id, legacy)?.id, second.id);
  assert.equal(activeSavedPreset(presets, zone.id, { ...loaded, activePresetId: 'deleted' }), undefined);
});

test('기존 저장소 키와 프리셋을 읽고 소등 복원 밝기를 왕복 보존한다', () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  const settings = pickSettings({ ...actuator, ledMode: 'night', ledGrowLight: false, ledIntensity: 0, lastLedIntensity: 85 });
  const preset = { id: 'old', zoneId: zone.id, name: '기존', settings: { ...settings, sideVent: 20 } };
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: (key: string) => {
    assert.equal(key, STORAGE_KEY);
    return JSON.stringify([preset]);
  } } });
  try {
    const restored = readPresets()[0];
    assert.equal(restored.settings.lastLedIntensity, 85);
    assert.equal('sideVent' in pickSettings(restored.settings), false);
    delete preset.settings.lastLedIntensity;
    assert.equal(readPresets()[0].id, 'old');
  } finally {
    if (descriptor) Object.defineProperty(globalThis, 'localStorage', descriptor);
    else delete (globalThis as { localStorage?: Storage }).localStorage;
  }
});

test('EC와 pH 각각의 범위 이탈이 종합 경고에 반영되고 경계값은 정상이다', () => {
  for (const [key, range] of [['ec', zone.targetEc], ['ph', zone.targetPh]] as const) {
    for (const value of [range.min, range.max, range.min - 0.01, range.max + 0.01]) {
      const state = zoneState({ ...zone, sensors: { ...zone.sensors, [key]: value } });
      const expected = value < range.min || value > range.max;
      assert.equal(key === 'ec' ? state.ecWarn : state.phWarn, expected);
      assert.equal(state.warn, expected);
    }
  }
});

test('24시간 그래프는 시각을 이동하면서 이전 표본과 현재값을 분리한다', () => {
  const start = new Date(2026, 8, 14, 12);
  const samples = buildHistory(zone, 'temp', start);
  const now = new Date(start.getTime() + 30_000);
  const visible = visibleHistory(samples, now, 25);
  assert.equal(visible[0].t, now.getTime() - 24 * 3600_000);
  assert.equal(visible.at(-1)?.t, now.getTime());
  assert.equal(visible.at(-1)?.v, 25);
  assert.equal(visible.find((p) => p.t === start.getTime())?.v, zone.sensors.temperature);
  assert.equal(appendHistorySample(samples, zone, 'temp', now), samples);
  const later = new Date(start.getTime() + 15 * 60_000);
  const next = appendHistorySample(samples, { ...zone, sensors: { ...zone.sensors, temperature: 26 } }, 'temp', later);
  assert.equal(next.at(-1)?.v, 26);
  assert.equal(next.at(-1)?.t, later.getTime());
  assert.ok(next.length <= 98);
  assert.ok(visibleHistory(next, later, 26).every((p) => p.t >= later.getTime() - 24 * 3600_000));
});

test('24시간 이상 중단과 시계 역행 후에도 정렬된 유한한 데모 이력을 복구한다', () => {
  const start = new Date(2026, 8, 14, 12);
  const samples = buildHistory(zone, 'light', start);
  for (const offset of [-3600_000, 25 * 3600_000]) {
    const now = new Date(start.getTime() + offset);
    const next = appendHistorySample(samples, zone, 'light', now);
    assert.equal(next.length, 97);
    assert.equal(next.at(-1)?.t, now.getTime());
    assert.equal(next[0].t, now.getTime() - 24 * 3600_000);
    assert.ok(next.every((p, i) => Number.isFinite(p.v) && (i === 0 || p.t > next[i - 1].t)));
  }
});


test('pH만 이탈하면 EC 카드는 경고하지만 정상 EC 그래프는 이탈로 표시하지 않는다', () => {
  const readings = buildReadings({...zone,sensors:{...zone.sensors,ph:9}},actuator);
  const ec = readings.find((item) => item.key === 'ec')!;
  assert.equal(ec.warn,true);
  assert.equal(ec.chartWarn,false);
  assert.ok(ec.rows.some(([label,value]) => label === 'pH' && value.includes('pH 범위 이탈')));
});

test('복사는 쓰기 완료 후에만 성공하며 거부나 동기 오류는 실패로 돌려준다', async () => {
  let resolve!: () => void;
  let settled = false;
  const pending = tryCopyCode('test', () => new Promise<void>((done) => { resolve = done; }));
  void pending.then(() => { settled = true; });
  await Promise.resolve();
  assert.equal(settled,false);
  resolve();
  assert.equal(await pending,true);
  assert.equal(await tryCopyCode('test', async () => { throw new Error('permission denied'); }),false);
  assert.equal(await tryCopyCode('test', () => { throw new Error('clipboard unavailable'); }),false);
});

test('백엔드 이력은 시간순으로 정렬되고 숫자가 아닌 값은 버리며 배율(조도 lx→klx)을 적용한다', () => {
  const rows = [
    { time: '2026-09-30T01:05:00Z', field: 'lux', value: 196 },
    { time: '2026-09-30T01:00:00Z', field: 'lux', value: 638.3 },
    { time: '2026-09-30T01:10:00Z', field: 'lux', value: null as unknown as number },
  ];
  const plain = pointsFromRows(rows);
  assert.deepEqual(plain.map((p) => p.v), [638.3, 196]);
  assert.ok(plain[0].t < plain[1].t);
  const klx = pointsFromRows(rows, 1 / 1000);
  assert.deepEqual(klx.map((p) => p.v), [0.6383, 0.196]);
});
