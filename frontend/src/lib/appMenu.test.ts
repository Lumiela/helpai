import { test } from 'node:test';
import assert from 'node:assert/strict';
import { appMenu } from './appMenu';

test('새 화면 메뉴: 기능 페이지 주소, 개발자용은 개발자 도구가 켜졌을 때만', () => {
  assert.deepEqual(appMenu('jetson-01', false).map((m) => m.to), ['/i/jetson-01/devices', '/i/jetson-01/logs', '/i/jetson-01/presets']);
  assert.deepEqual(appMenu('jetson-01', true).map((m) => m.key), ['devices', 'logs', 'actuators', 'reports', 'notification-lab']);
  assert.deepEqual(appMenu(null, true), []);
});
