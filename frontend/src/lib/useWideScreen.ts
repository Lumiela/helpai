import { useEffect, useState } from 'react';

// 2026-11-25 사용자 결정: 주소를 나누지 않고 화면 폭으로 모바일 앱(767px 이하)과 웹 대시보드(768px 이상)를 바꾼다.
export const WIDE_QUERY = '(min-width: 768px)';

export function useWideScreen(): boolean {
  const [wide, setWide] = useState(() => window.matchMedia(WIDE_QUERY).matches);
  useEffect(() => {
    const media = window.matchMedia(WIDE_QUERY);
    const onChange = () => setWide(media.matches);
    onChange();
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, []);
  return wide;
}
