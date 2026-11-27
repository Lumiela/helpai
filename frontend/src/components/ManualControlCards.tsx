import { Children, useEffect, useRef, useState, type ReactNode } from 'react';

const DEFAULT_LABELS = ['주간 온도', '배기팬', '순환팬', 'LED 밝기'];

export function ManualControlCards({ children, zoneId, disabled = false, labels: LABELS = DEFAULT_LABELS }: { children: ReactNode; zoneId: string; disabled?: boolean; labels?: string[] }) {
  const cards = Children.toArray(children);
  const [mobile, setMobile] = useState(() => window.matchMedia('(max-width: 767px)').matches);
  const [active, setActive] = useState(0);
  const [offset, setOffset] = useState(0);
  const [cardHeight, setCardHeight] = useState<number>();
  const trackRef = useRef<HTMLDivElement>(null);
  const gesture = useRef<{ id: number; x: number; y: number; width: number } | null>(null);

  useEffect(() => { setActive(0); setOffset(0); gesture.current = null; }, [zoneId]);

  useEffect(() => {
    if (!mobile) return;
    const card = trackRef.current?.children[active];
    if (!card) return;
    const measure = () => {
      const height = card.getBoundingClientRect().height;
      if (height > 0) setCardHeight(height);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(card);
    return () => observer.disconnect();
  }, [active, mobile]);

  useEffect(() => {
    const media = window.matchMedia('(max-width: 767px)');
    const resize = () => {
      setMobile(media.matches);
      setOffset(0);
      gesture.current = null;
    };
    media.addEventListener('change', resize);
    return () => media.removeEventListener('change', resize);
  }, []);

  const select = (index: number) => {
    setActive(Math.max(0, Math.min(cards.length - 1, index)));
    setOffset(0);
  };

  return (
    <div className="min-w-0">
      <div className="flex items-center justify-center mb-1 md:hidden" aria-label="수동 제어 카드 선택">
        {cards.map((_, index) => (
          <button key={LABELS[index]} type="button" aria-label={`${LABELS[index]} 카드 보기`}
            aria-current={active === index ? 'step' : undefined}
            className="w-6 h-6 text-sm text-ink-strong" onClick={() => select(index)}>
            <span aria-hidden="true">{active === index ? '●' : '○'}</span>
          </button>
        ))}
        <span className="sr-only" aria-live="polite">{active + 1} / {cards.length} · {LABELS[active]}</span>
      </div>
      <div
        className="overflow-hidden md:overflow-visible touch-pan-y select-none"
        style={mobile && cardHeight ? { height: cardHeight } : undefined}
        role="group"
        aria-label="수동 제어 카드"
        tabIndex={mobile ? 0 : undefined}
        onKeyDown={(event) => {
          if (!mobile || event.target !== event.currentTarget) return;
          if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
            event.preventDefault();
            select(active + (event.key === 'ArrowRight' ? 1 : -1));
          }
        }}
        onPointerDown={(event) => {
          if (!mobile || !event.isPrimary || event.button !== 0 ||
            (event.target as HTMLElement).closest('button, input, select, label, a')) return;
          if (event.pointerType === 'mouse') event.preventDefault();
          gesture.current = { id: event.pointerId, x: event.clientX, y: event.clientY, width: event.currentTarget.clientWidth };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          const start = gesture.current;
          if (!start || start.id !== event.pointerId) return;
          const dx = event.clientX - start.x;
          if (Math.abs(event.clientY - start.y) > Math.abs(dx) && Math.abs(dx) < 12) return;
          const blocked = (active === 0 && dx > 0) || (active === cards.length - 1 && dx < 0);
          setOffset(blocked ? 0 : Math.max(-start.width, Math.min(start.width, dx)));
        }}
        onPointerUp={(event) => {
          const start = gesture.current;
          if (!start || start.id !== event.pointerId) return;
          const dx = event.clientX - start.x;
          const dy = event.clientY - start.y;
          gesture.current = null;
          // 속도와 관계없이 한 번에 한 장만 이동하고, 짧은 드래그는 원래 카드로 복귀한다.
          select(active + (Math.abs(dx) >= Math.min(60, start.width * 0.2) && Math.abs(dx) > Math.abs(dy) ? (dx < 0 ? 1 : -1) : 0));
        }}
        onPointerCancel={() => { gesture.current = null; setOffset(0); }}
        onLostPointerCapture={() => { gesture.current = null; setOffset(0); }}
      >
        <div
          ref={trackRef}
          className={`flex items-start md:items-stretch md:grid md:grid-cols-1 md:gap-3 ${offset === 0 ? 'transition-transform duration-200 motion-reduce:transition-none' : ''}`}
          style={mobile ? { transform: `translateX(calc(-${active * 100}% + ${offset}px))` } : undefined}
        >
          {cards.map((card, index) => (
            <div key={LABELS[index]} className="basis-full shrink-0 min-w-0 p-1 md:p-0"
              role="group" aria-label={LABELS[index]} aria-hidden={mobile && index !== active}
              inert={mobile && index !== active}>
              <fieldset disabled={disabled} aria-label={`${LABELS[index]} 제어`}
                className="min-w-0 h-full border-0 p-0 m-0 disabled:grayscale disabled:opacity-50 [&_button:disabled]:cursor-not-allowed [&_input:disabled]:cursor-not-allowed">
                {card}
              </fieldset>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
