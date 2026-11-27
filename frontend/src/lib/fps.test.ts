import assert from 'node:assert/strict';
import { test } from 'node:test';
import { FPS_WINDOW_MS, pruneFrameTimes, recentFps } from './fps';

test('최근 5초 동안 받은 프레임 수를 초당 값으로 낸다', () => {
  // 0초에 첫 프레임, 이후 0.5초 간격(2 fps)으로 10초
  const times = Array.from({ length: 21 }, (_, i) => i * 500);
  assert.equal(recentFps(times, 10_000, 0), 2);
});

test('보기 시작 직후에는 지난 시간만큼으로 나누고 첫 프레임은 세지 않는다', () => {
  const times = [0, 500, 1000, 1500, 2000];
  assert.equal(recentFps(times, 2000, 0), 2);
});

test('첫 프레임 뒤 1초가 안 됐거나 프레임이 없으면 null', () => {
  assert.equal(recentFps([0, 400], 800, 0), null);
  assert.equal(recentFps([], 5000, null), null);
});

test('프레임이 끊기면 값이 0 으로 내려간다', () => {
  const times = [0, 500, 1000];
  assert.equal(recentFps(times, 7000, 0), 0);
});

test('창보다 오래된 시각은 버린다', () => {
  const times = [0, 1000, 4000, 6000, 9000];
  pruneFrameTimes(times, 9000);
  assert.deepEqual(times, [6000, 9000]); // 창 경계(4000)는 recentFps 와 같이 창 밖으로 본다
  assert.equal(FPS_WINDOW_MS, 5000);
});
