import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chartPaths, everyMs, monotonePath, splitAtGaps, type XY } from './chartPath';

const H = 3_600_000;
// 2026-11-25 실제로 꼬였던 온도 기록: 2시간 간격 13점 → 130시간 빔 → 2점 → 10시간 빔 → 2점
const HOURS = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 154, 156, 166, 167.9];
const VALUES = [23.5, 23.6, 23.9, 24.5, 24.2, 23, 21.6, 21.6, 21.2, 20.8, 20.3, 19.4, 20.9, 20.3, 21.1, 15.9, 20.6];
const times = HOURS.map((h) => h * H);
const xy = HOURS.map((h, i) => [8 + (h / 167.9) * 304, 120 - VALUES[i] * 3] as XY);

// 구간마다 곡선 위의 좌표를 잘게 뽑는다
function sample(d: string): XY[][] {
  return d.split('M').filter(Boolean).map((part) => {
    const [head, ...curves] = part.split('C');
    const [x0, y0] = head.split('h')[0].split(' ').map(Number);
    const pts: XY[] = [[x0, y0]];
    for (const c of curves) {
      const [x1, y1, x2, y2, x3, y3] = c.split(' ').map(Number);
      const p0 = pts[pts.length - 1];
      for (let k = 1; k <= 20; k++) {
        const u = k / 20, v = 1 - u;
        pts.push([
          v ** 3 * p0[0] + 3 * v * v * u * x1 + 3 * v * u * u * x2 + u ** 3 * x3,
          v ** 3 * p0[1] + 3 * v * v * u * y1 + 3 * v * u * u * y2 + u ** 3 * y3,
        ]);
      }
    }
    return pts;
  });
}

test('집계 간격 문자열을 ms 로 읽는다', () => {
  assert.deepEqual(['15m', '2h', '6h', '30s', '1d', 'x'].map(everyMs), [900_000, 2 * H, 6 * H, 30_000, 24 * H, 0]);
});

test('기록이 빈 구간에서 끊는다. 한 칸만 빈 것은 잇는다', () => {
  assert.deepEqual(splitAtGaps(times, 2 * H), [[0, 12], [13, 14], [15, 16]]);
  assert.deepEqual(splitAtGaps([0, 2 * H, 6 * H, 8 * H], 2 * H), [[0, 3]]);
  assert.deepEqual(splitAtGaps([0, 10 * H, 20 * H], 2 * H), [[0, 0], [1, 1], [2, 2]]);
  assert.deepEqual(splitAtGaps([], 2 * H), []);
});

test('실제로 꼬였던 기록: 곡선이 시간을 거꾸로 가지 않고 구간마다 따로 그린다', () => {
  const { line, area } = chartPaths(xy, times, 2 * H, 100);
  const parts = sample(line);
  assert.deepEqual(parts.map((p) => p.length), [1 + 12 * 20, 1 + 20, 1 + 20]);
  for (const pts of parts) for (let i = 1; i < pts.length; i++) assert.ok(pts[i][0] >= pts[i - 1][0] - 1e-9);
  assert.equal(area.split('Z').length - 1, 3);
  assert.ok(!/NaN|Infinity/.test(line + area));
});

test('곡선은 이웃한 두 점의 값 밖으로 나가지 않는다', () => {
  const pts = sample(monotonePath(xy.slice(0, 13)))[0];
  pts.slice(1).forEach(([, y], j) => {
    const i = Math.floor(j / 20);
    assert.ok(y >= Math.min(xy[i][1], xy[i + 1][1]) - 1e-6 && y <= Math.max(xy[i][1], xy[i + 1][1]) + 1e-6);
  });
});

test('점 하나뿐인 구간은 점으로 남고 채우기는 없다', () => {
  assert.equal(monotonePath([[5, 7]]), 'M5 7h0.01');
  const { line, area } = chartPaths([[0, 1], [50, 2], [100, 3]], [0, 10 * H, 20 * H], 2 * H, 10);
  assert.equal(line.split('h0.01').length - 1, 3);
  assert.equal(area, '');
});
