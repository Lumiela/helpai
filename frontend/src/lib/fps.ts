// 실시간 보기 fps (2026-10-02, 사용자 지시: 개발자 모드에서 스트리밍 화면에 최근 5초 fps 표시).
//
//   받은 프레임 시각(ms, 오름차순)으로 최근 창(기본 5초) 동안의 초당 프레임 수를 낸다.
//   순수 함수만 두어 node:test 로 검사한다.

export const FPS_WINDOW_MS = 5000;

// 창보다 오래된 시각을 버린다(배열을 직접 줄인다).
export function pruneFrameTimes(times: number[], now: number, windowMs = FPS_WINDOW_MS): void {
  let i = 0;
  while (i < times.length && times[i] <= now - windowMs) i++;
  if (i > 0) times.splice(0, i);
}

// startedAt = 첫 프레임 시각. 보기 시작 직후에는 창을 지난 시간만큼으로 줄여 값이 낮게 나오지 않게 한다.
// 첫 프레임은 세지 않는다(서버가 바로 보내는 직전 프레임일 수 있음). 지난 시간이 1초 미만이면 null.
export function recentFps(
  times: readonly number[],
  now: number,
  startedAt: number | null,
  windowMs = FPS_WINDOW_MS,
): number | null {
  if (startedAt === null) return null;
  const span = Math.min(windowMs, now - startedAt);
  if (span < 1000) return null;
  let n = 0;
  for (const t of times) if (t > now - span && t <= now) n++;
  return n / (span / 1000);
}
