import React, { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';

interface TriggerRenderProps {
  open: boolean;
  toggle: () => void;
  buttonProps: {
    'aria-expanded': boolean;
    'aria-haspopup': 'menu' | 'listbox';
    'aria-controls': string;
  };
}

interface DropdownProps {
  label: string;
  role: 'menu' | 'listbox';
  align?: 'left' | 'right';
  panelClassName?: string;
  // true: 패널을 화면 기준(position: fixed)으로 띄운다 — 스크롤 영역(표 등) 안에서 잘리지 않게(2026-11-18 장치 목록 메뉴).
  //       아래 공간이 모자라면 위로 연다. 화면이 스크롤되거나 크기가 바뀌면 닫는다.
  floating?: boolean;
  trigger: (props: TriggerRenderProps) => React.ReactNode;
  children: (close: () => void) => React.ReactNode;
}

const FLOAT_GAP = 8;
const FLOAT_MIN_BELOW = 180; // 아래 공간이 이보다 작고 위가 더 넓으면 위로 연다

// 바깥 클릭과 Esc로 닫히는 드롭다운. 햄버거 메뉴와 큐브 선택에 같이 쓴다.
export const Dropdown: React.FC<DropdownProps> = ({ label, role, align = 'left', panelClassName = '', floating = false, trigger, children }) => {
  const [open, setOpen] = useState(false);
  const [floatStyle, setFloatStyle] = useState<React.CSSProperties | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const panelId = useId();

  useLayoutEffect(() => {
    if (!open || !floating || !rootRef.current) {
      setFloatStyle(null);
      return;
    }
    const r = rootRef.current.getBoundingClientRect();
    const below = window.innerHeight - r.bottom;
    const style: React.CSSProperties = { position: 'fixed' };
    if (below < FLOAT_MIN_BELOW && r.top > below) style.bottom = window.innerHeight - r.top + FLOAT_GAP;
    else style.top = r.bottom + FLOAT_GAP;
    if (align === 'right') style.right = window.innerWidth - r.right;
    else style.left = r.left;
    setFloatStyle(style);
  }, [open, floating, align]);

  useEffect(() => {
    if (!open || !floating) return;
    const onMove = () => setOpen(false);
    window.addEventListener('scroll', onMove, true);
    window.addEventListener('resize', onMove);
    return () => {
      window.removeEventListener('scroll', onMove, true);
      window.removeEventListener('resize', onMove);
    };
  }, [open, floating]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const close = () => setOpen(false);

  return (
    <div ref={rootRef} className="relative inline-block">
      {trigger({
        open,
        toggle: () => setOpen((o) => !o),
        buttonProps: { 'aria-expanded': open, 'aria-haspopup': role, 'aria-controls': panelId },
      })}
      {open && (!floating || floatStyle) && (
        <div
          id={panelId}
          role={role}
          aria-label={label}
          style={floating ? (floatStyle ?? undefined) : undefined}
          className={
            floating
              ? `z-50 min-w-56 panel p-1 shadow-lg text-left ${panelClassName}`
              : `absolute z-50 mt-2 min-w-56 panel p-1 shadow-lg ${align === 'right' ? 'right-0' : 'left-0'} ${panelClassName}`
          }
        >
          {children(close)}
        </div>
      )}
    </div>
  );
};
