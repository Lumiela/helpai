import type { ActuatorState } from '../types/smartfarm';

export function DamperControl({ actuator, onUpdate, showAutoToggle = true }: {
  actuator: ActuatorState;
  onUpdate: (updates: Partial<ActuatorState>) => void;
  showAutoToggle?: boolean;
}) {
  return (
    <div className="space-y-2">
      {showAutoToggle && <button type="button" className="btn btn-secondary btn-sm" aria-pressed={actuator.autoMode}
        onClick={() => onUpdate({ autoMode: !actuator.autoMode })}>
        큐브 자동 제어 {actuator.autoMode ? '켜짐' : '꺼짐'}
      </button>}
      <div className="seg w-full" role="group" aria-label="댐퍼 선택">
        <button type="button" aria-label="냉각 댐퍼" aria-pressed={actuator.coolingDamper}
          className="flex-1 min-w-0 px-1"
          onClick={() => onUpdate({ coolingDamper: !actuator.coolingDamper, heatingDamper: false, autoMode: false })}>
          냉각 댐퍼
        </button>
        <button type="button" aria-label="가온 댐퍼" aria-pressed={actuator.heatingDamper}
          className="flex-1 min-w-0 px-1"
          onClick={() => onUpdate({ heatingDamper: !actuator.heatingDamper, coolingDamper: false, autoMode: false })}>
          가온 댐퍼
        </button>
      </div>
      {showAutoToggle && <p className="text-xs text-ink-muted">자동 제어를 켜면 서버가 큐브에 정한 작물의 범위로 배기팬·LED·급수를 조절합니다. 작물은 큐브 상세에서 고릅니다. 직접 조작하면 자동 제어가 꺼집니다. 냉각과 가온은 동시에 가동할 수 없습니다.</p>}
    </div>
  );
}
