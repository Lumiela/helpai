import type { ActuatorState } from '../types/smartfarm';

export const STORAGE_KEY = 'smartcube-presets-v1';
const SETTING_KEYS = [
  'outerFan', 'outerFanSpeed', 'innerFan', 'innerFanSpeed', 'ledMode',
  'ledGrowLight', 'ledIntensity', 'co2Generator', 'irrigationPump', 'nutrientValve',
] as const;
const DAMPER_KEYS = ['coolingDamper', 'heatingDamper', 'autoMode'] as const;
type Settings = Pick<ActuatorState, (typeof SETTING_KEYS)[number]> & Partial<Pick<ActuatorState, (typeof DAMPER_KEYS)[number] | 'lastLedIntensity'>>;
export interface SavedPreset {
  id: string;
  zoneId: string;
  name: string;
  settings: Settings;
}

function isPreset(value: unknown): value is SavedPreset {
  if (!value || typeof value !== 'object') return false;
  const p = value as SavedPreset;
  const s = p.settings;
  return typeof p.id === 'string' && typeof p.zoneId === 'string' && typeof p.name === 'string'
    && !!s && SETTING_KEYS.every((key) => {
      const v = s[key];
      if (key === 'ledMode') return v === 'day' || v === 'night';
      if (key === 'outerFanSpeed' || key === 'innerFanSpeed') return Number.isInteger(v) && Number(v) >= 0 && Number(v) <= 3;
      if (key === 'ledIntensity') return typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 100;
      return typeof v === 'boolean';
    }) && DAMPER_KEYS.every((key) => s[key] === undefined || typeof s[key] === 'boolean')
    && (s.lastLedIntensity === undefined || (Number.isFinite(s.lastLedIntensity) && s.lastLedIntensity > 0 && s.lastLedIntensity <= 100));
}

export function readPresets(): SavedPreset[] {
  const data: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]');
  if (!Array.isArray(data) || !data.every(isPreset)) throw new Error('Invalid presets');
  return data;
}

export function pickSettings(actuator: Settings): Settings {
  return Object.fromEntries([...SETTING_KEYS, ...DAMPER_KEYS, 'lastLedIntensity']
    .filter((key) => actuator[key] !== undefined).map((key) => [key, actuator[key]])) as Settings;
}

export function activeSavedPreset(presets: SavedPreset[], zoneId: string, actuator: ActuatorState): SavedPreset | undefined {
  const candidates = presets.filter((preset) => preset.zoneId === zoneId);
  if (actuator.activePresetId) return candidates.find((preset) => preset.id === actuator.activePresetId);
  // 이전 이름 기반 상태는 하나로 식별되는 경우에만 연결한다.
  const named = candidates.filter((preset) => preset.name === actuator.activePresetName);
  return named.length === 1 ? named[0] : undefined;
}
