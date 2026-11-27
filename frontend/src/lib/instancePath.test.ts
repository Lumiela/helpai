import assert from 'node:assert/strict';
import { test } from 'node:test';
import { apiBaseFor, instanceDisplayName, instanceFromPath, instanceHome, INSTANCE_ID_RE, loginUrl, safeNext } from './instancePath';

test('경로에서 인스턴스 ID 를 읽는다', () => {
  assert.equal(instanceFromPath('/i/jetson-01/'), 'jetson-01');
  assert.equal(instanceFromPath('/i/jetson-01'), 'jetson-01');
  assert.equal(instanceFromPath('/i/jetson-01/anything?x=1'), 'jetson-01');
  assert.equal(instanceFromPath('/'), null);
  assert.equal(instanceFromPath('/api/health'), null);
  assert.equal(instanceFromPath('/i/'), null);
  assert.equal(instanceFromPath('/i/Jetson-01/'), null); // 대문자 불가 (api·nginx 규칙과 동일)
  assert.equal(instanceFromPath('/instances/x'), null);
});

test('인스턴스 홈과 api 기준 경로', () => {
  assert.equal(instanceHome('jetson-02'), '/i/jetson-02/');
  assert.equal(apiBaseFor(null), '/api');
  assert.equal(apiBaseFor('jetson-01'), '/i/jetson-01/api');
  assert.equal(apiBaseFor('jetson-01', 'http://127.0.0.1:8000/api'), 'http://127.0.0.1:8000/i/jetson-01/api');
  assert.equal(apiBaseFor('jetson-01', 'http://127.0.0.1:8000'), 'http://127.0.0.1:8000/i/jetson-01');
});

test('인스턴스 ID 규칙', () => {
  assert.ok(INSTANCE_ID_RE.test('jetson-01'));
  assert.ok(INSTANCE_ID_RE.test('a'));
  assert.ok(!INSTANCE_ID_RE.test('-bad'));
  assert.ok(!INSTANCE_ID_RE.test('has space'));
});

test('로그인 경로와 복귀 주소(next)는 인스턴스 홈만 허용', () => {
  assert.equal(loginUrl(), '/login');
  assert.equal(loginUrl('/i/jetson-01/'), '/login?next=%2Fi%2Fjetson-01%2F');
});

test('화면 표시 이름: jetson-01 → user-1, 그 밖은 ID 그대로 (2026-11-04, 프론트 전용)', () => {
  assert.equal(instanceDisplayName('jetson-01'), 'user-1');
  assert.equal(instanceDisplayName('jetson-02'), 'jetson-02');
  assert.equal(loginUrl('https://evil.example/'), '/login');
  assert.equal(safeNext('/i/jetson-02'), '/i/jetson-02/');
  assert.equal(safeNext('//evil.example/i/x/'), null);
  assert.equal(safeNext('/login'), null);
});
