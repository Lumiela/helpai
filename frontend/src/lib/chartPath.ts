// 이력 그래프의 선 (2026-11-25): 기록이 빈 구간에서는 선을 끊고, 곡선이 시간을 거꾸로 가지 않게 한다.
// 예전 식(카디널 스플라인)은 점 간격이 일정하다고 보아, 빈 구간 양쪽에서 곡선이 고리처럼 꼬였다.
// 순수 함수만 두어 node:test 로 검사한다.

export type XY = [number, number];

const UNIT_MS: Record<string, number> = { s: 1_000, m: 60_000, h: 3_600_000, d: 86_400_000 };

// 집계 간격('15m'·'2h'·'6h')을 ms 로
export function everyMs(every: string): number {
  const m = /^([0-9]+)([smhd])$/.exec(every);
  return m ? Number(m[1]) * UNIT_MS[m[2]] : 0;
}

// 이어진 구간의 [처음, 끝] 번호. 점 사이가 집계 간격의 2.5배를 넘으면(2칸 이상 비면) 끊는다.
export function splitAtGaps(times: number[], stepMs: number): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  if (times.length === 0) return out;
  let from = 0;
  for (let i = 1; i < times.length; i++) {
    if (stepMs > 0 && times[i] - times[i - 1] > stepMs * 2.5) {
      out.push([from, i - 1]);
      from = i;
    }
  }
  out.push([from, times.length - 1]);
  return out;
}

// 단조 3차 곡선(Fritsch–Carlson): x 가 되돌아가지 않고, 값이 이웃한 두 점 밖으로 나가지 않는다.
export function monotonePath(xy: XY[]): string {
  if (xy.length === 0) return '';
  const start = `M${xy[0][0]} ${xy[0][1]}`;
  if (xy.length === 1) return `${start}h0.01`; // 점 하나뿐인 구간은 둥근 선끝으로 점처럼 보인다
  const n = xy.length;
  const slope = (i: number) => {
    const dx = xy[i + 1][0] - xy[i][0];
    return dx > 0 ? (xy[i + 1][1] - xy[i][1]) / dx : 0;
  };
  const t: number[] = new Array(n).fill(0);
  for (let i = 1; i < n - 1; i++) {
    const s0 = slope(i - 1), s1 = slope(i);
    const h0 = xy[i][0] - xy[i - 1][0], h1 = xy[i + 1][0] - xy[i][0];
    const p = h0 + h1 > 0 ? (s0 * h1 + s1 * h0) / (h0 + h1) : 0;
    t[i] = (Math.sign(s0) + Math.sign(s1)) * Math.min(Math.abs(s0), Math.abs(s1), 0.5 * Math.abs(p));
  }
  if (n === 2) {
    t[0] = slope(0);
    t[1] = slope(0);
  } else {
    t[0] = (3 * slope(0) - t[1]) / 2;
    t[n - 1] = (3 * slope(n - 2) - t[n - 2]) / 2;
  }
  let d = start;
  for (let i = 0; i < n - 1; i++) {
    const dx = (xy[i + 1][0] - xy[i][0]) / 3;
    d += `C${xy[i][0] + dx} ${xy[i][1] + dx * t[i]} ${xy[i + 1][0] - dx} ${xy[i + 1][1] - dx * t[i + 1]} ${xy[i + 1][0]} ${xy[i + 1][1]}`;
  }
  return d;
}

// 화면에 그릴 선과(구간마다 따로) 선 아래 채우기
export function chartPaths(xy: XY[], times: number[], stepMs: number, baseY: number): { line: string; area: string } {
  let line = '';
  let area = '';
  for (const [from, to] of splitAtGaps(times, stepMs)) {
    const d = monotonePath(xy.slice(from, to + 1));
    line += d;
    if (to > from) area += `${d}L${xy[to][0]} ${baseY}L${xy[from][0]} ${baseY}Z`;
  }
  return { line, area };
}
