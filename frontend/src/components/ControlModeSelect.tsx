import { useState } from 'react';
import { ChevronDown } from 'lucide-react';
import type { ActuatorState, ZoneConfig } from '../types/smartfarm';
import { EdgeNotice } from '../lib/agToolkit';
import { pickSettings, readPresets, activeSavedPreset, type SavedPreset } from '../lib/presets';

function loadPresets() {
  try { return { presets: readPresets(), error: '' }; }
  catch { return { presets: [] as SavedPreset[], error: '저장된 프리셋을 읽지 못했습니다.' }; }
}

export function ControlModeSelect({ zone, actuator, onUpdate }: {
  zone: ZoneConfig;
  actuator: ActuatorState;
  onUpdate: (zoneId: string, updates: Partial<ActuatorState>) => void;
}) {
  const [{ presets, error }, setStored] = useState(loadPresets);
  const zonePresets = presets.filter((preset) => preset.zoneId === zone.id);
  const active = activeSavedPreset(presets, zone.id, actuator);
  const value = actuator.activePresetName
    ? active ? `preset:${active.id}` : 'current-preset'
    : actuator.autoMode ? 'auto' : 'manual';

  return (
    <div className="relative min-w-0 max-w-[55%]">
      <select
        aria-label="큐브 제어 상태 선택"
        title={actuator.activePresetName || (actuator.autoMode ? '자동 제어' : '수동 제어')}
        className="block w-full cursor-pointer appearance-none truncate border-0 bg-panel py-1 pl-2 pr-7 text-right text-sm text-ink-muted hover:bg-inset"
        value={value}
        onFocus={() => setStored(loadPresets())}
        onPointerDown={() => setStored(loadPresets())}
        onChange={(event) => {
          const next = event.target.value;
          if (next === 'auto' || next === 'manual') {
            // 2026-10-06: 자동 제어는 서버가 한다. 결과 알림은 App 이 서버 응답을 보고 띄운다.
            if (next === 'auto' && !zone.cropId) {
              EdgeNotice.info(`${zone.code} 자동 제어`, '작물을 먼저 선택하세요.');
              return;
            }
            onUpdate(zone.id, { autoMode: next === 'auto' });
            return;
          }
          const preset = zonePresets.find((item) => `preset:${item.id}` === next);
          if (!preset) return;
          onUpdate(zone.id, { ...pickSettings(preset.settings), activePresetName: preset.name, activePresetId: preset.id });
          EdgeNotice.toast(`${preset.name}을 불러왔습니다.`);
        }}
      >
        <option value="auto">자동 제어</option>
        <option value="manual">수동 제어</option>
        {value === 'current-preset' && <option value="current-preset" disabled>{actuator.activePresetName}</option>}
        <optgroup label="저장된 프리셋">
          {zonePresets.map((preset) => <option key={preset.id} value={`preset:${preset.id}`}>{preset.name}</option>)}
          {zonePresets.length === 0 && <option disabled>{error || '저장된 프리셋 없음'}</option>}
        </optgroup>
      </select>
      <ChevronDown className="pointer-events-none absolute right-1 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-muted" aria-hidden="true" />
    </div>
  );
}
