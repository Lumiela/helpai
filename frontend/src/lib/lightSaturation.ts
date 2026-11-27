import type { ZoneConfig } from '../types/smartfarm';

// 켜짐·꺼짐 구간의 부드러운 변화는 시각화용 가정이며 생리학적 측정 곡선이 아니다.
export function daylightFactor(on: number, off: number, hour: number, ramp: number): number {
  if (on === off) return 1;
  const dayLength = ((off - on) % 24 + 24) % 24;
  const elapsed = ((hour - on) % 24 + 24) % 24;
  if (elapsed >= dayLength) return 0;
  const transition = Math.min(Math.max(0, ramp), dayLength / 2);
  if (transition === 0) return 1;
  const progress = Math.min(1, elapsed / transition, (dayLength - elapsed) / transition);
  return progress * progress * (3 - 2 * progress);
}

export function saturationPpfdAt(profile: ZoneConfig['lightSaturation'], period: ZoneConfig['photoperiod'], hour: number): number {
  return profile.peakPpfd * daylightFactor(period.on, period.off, hour, profile.rampHours);
}
