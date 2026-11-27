import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseInstanceRoute, pathForTab } from './routes';

test('인스턴스 안 주소 → 탭', () => {
  assert.deepEqual(parseInstanceRoute('/i/jetson-01/'), { iid: 'jetson-01', tab: 'overview', sn: null, known: true });
  assert.deepEqual(parseInstanceRoute('/i/jetson-01'), { iid: 'jetson-01', tab: 'overview', sn: null, known: true });
  assert.deepEqual(parseInstanceRoute('/i/jetson-01/cubes/CUBE001'), { iid: 'jetson-01', tab: 'zone-detail', sn: 'CUBE001', known: true });
  assert.deepEqual(parseInstanceRoute('/i/jetson-01/cubes/CUBE001/'), { iid: 'jetson-01', tab: 'zone-detail', sn: 'CUBE001', known: true });
  assert.deepEqual(parseInstanceRoute('/i/jetson-01/presets'), { iid: 'jetson-01', tab: 'actuators', sn: null, known: true });
  assert.deepEqual(parseInstanceRoute('/i/jetson-01/devices'), { iid: 'jetson-01', tab: 'devices', sn: null, known: true });
  assert.deepEqual(parseInstanceRoute('/i/jetson-01/logs'), { iid: 'jetson-01', tab: 'logs', sn: null, known: true });
  assert.deepEqual(parseInstanceRoute('/i/jetson-01/lab'), { iid: 'jetson-01', tab: 'notification-lab', sn: null, known: true });
  assert.deepEqual(parseInstanceRoute('/i/jetson-01/reports'), { iid: 'jetson-01', tab: 'reports', sn: null, known: true });
});

test('모르는 조각·인스턴스 밖은 Home 취급(known=false)', () => {
  assert.equal(parseInstanceRoute('/i/jetson-01/nope').known, false);
  assert.equal(parseInstanceRoute('/i/jetson-01/cubes').known, false);
  assert.equal(parseInstanceRoute('/i/jetson-01/logs/extra').known, false);
  assert.equal(parseInstanceRoute('/login').iid, null);
});

test('탭 → 주소', () => {
  assert.equal(pathForTab('jetson-01', 'overview'), '/i/jetson-01/');
  assert.equal(pathForTab('jetson-01', 'zone-detail', 'CUBE001'), '/i/jetson-01/cubes/CUBE001');
  assert.equal(pathForTab('jetson-01', 'zone-detail', null), '/i/jetson-01/');
  assert.equal(pathForTab('jetson-01', 'actuators'), '/i/jetson-01/presets');
  assert.equal(pathForTab('jetson-01', 'notification-lab'), '/i/jetson-01/lab');
  assert.equal(pathForTab('jetson-01', 'reports'), '/i/jetson-01/reports');
  // 왕복
  for (const p of ['/i/jetson-01/', '/i/jetson-01/cubes/CUBE001', '/i/jetson-01/presets', '/i/jetson-01/logs']) {
    const r = parseInstanceRoute(p);
    assert.equal(pathForTab(r.iid!, r.tab, r.sn), p);
  }
});
