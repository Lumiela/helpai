import React from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';

// 큐브가 없을 때 (2026-11-18 사용자 지시 문구·'장치 연결' 버튼을 새 화면에도 쓴다)
export type EmptyState = 'loading' | 'error' | 'none';

export const EmptyCubes: React.FC<{ state: EmptyState; devicesHref: string | null; buttonClass: string }> = ({ state, devicesHref, buttonClass }) => (
  <>
    <p>
      {state === 'loading' ? '큐브를 불러오는 중…'
        : state === 'error' ? '백엔드에 연결되지 않아 큐브를 보여줄 수 없습니다.'
          : '등록된 큐브가 없습니다. 장치를 추가해주세요.'}
    </p>
    {state === 'none' && devicesHref && (
      <Link to={devicesHref} className={buttonClass}>장치 연결<ArrowRight aria-hidden="true" /></Link>
    )}
  </>
);
