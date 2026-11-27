import type { ActuatorState, ZoneConfig } from '../types/smartfarm';

function automaticSettings(state: ActuatorState): ActuatorState['automaticSettings'] {
  const { autoMode, activePresetName, activePresetId, automaticSettings: saved, irrigationPump, nutrientValve, ...settings } = state;
  return settings;
}

export function restoredLedIntensity(state: Pick<ActuatorState, 'ledIntensity' | 'lastLedIntensity'>): number {
  return state.ledIntensity > 0 ? state.ledIntensity : state.lastLedIntensity > 0 ? state.lastLedIntensity : 70;
}

export function scheduledMode(zone: ZoneConfig, now: Date): ActuatorState['ledMode'] {
  const { on, off } = zone.photoperiod;
  const hour = now.getHours() + now.getMinutes() / 60 + now.getSeconds() / 3600;
  return (on === off || (on < off ? hour >= on && hour < off : hour >= on || hour < off)) ? 'day' : 'night';
}

export function activeTargetTemp(zone: ZoneConfig, actuator: ActuatorState): number {
  return actuator.ledMode === 'night' ? zone.targetTemp.nightTarget : zone.targetTemp.currentTarget;
}

export function automaticDampers(current: ActuatorState, zone: ZoneConfig): ActuatorState {
  if (!current.autoMode) return current;
  const delta = zone.sensors.temperature - activeTargetTemp(zone, current);
  return { ...current, coolingDamper: delta > 0.5, heatingDamper: delta < -0.5 };
}

export function automaticControl(current: ActuatorState, zone: ZoneConfig, now = new Date()): ActuatorState {
  if (!current.autoMode) return current;
  const saved = current.automaticSettings ?? automaticSettings(current);
  const brightness = restoredLedIntensity(saved);
  const ledMode = scheduledMode(zone, now);
  return automaticDampers({ ...current, automaticSettings: saved, ledMode,
    ledGrowLight: ledMode === 'day', ledIntensity: ledMode === 'day' ? brightness : 0,
    lastLedIntensity: brightness }, zone);
}

export function updateActuator(current: ActuatorState, updates: Partial<ActuatorState>, zone: ZoneConfig, now = new Date()): ActuatorState {
  let next: ActuatorState = { ...current, lastLedIntensity: restoredLedIntensity(current), activePresetName: null, activePresetId: null, ...updates };
  const hasControlUpdates = Object.keys(updates).some((key) =>
    !['autoMode', 'activePresetName', 'activePresetId', 'automaticSettings'].includes(key));
  if (hasControlUpdates && updates.autoMode === undefined) next.autoMode = false;
  // 자동 설정은 처음 수동으로 전환할 때만 보관한다. 이후 수동 조작으로 덮어쓰지 않는다.
  if (current.autoMode && !next.autoMode) next.automaticSettings = current.automaticSettings ?? automaticSettings(current);
  if (updates.autoMode === true && !hasControlUpdates) {
    next = { ...next, ...current.automaticSettings, irrigationPump: false, nutrientValve: false };
  }
  next.lastLedIntensity = restoredLedIntensity(next);
  if (updates.ledGrowLight === false) next.ledIntensity = 0;
  else if (updates.ledIntensity !== undefined) next.ledGrowLight = updates.ledIntensity > 0;
  else if (updates.ledGrowLight === true) next.ledIntensity = next.lastLedIntensity;
  // 주·야간 선택은 즉시 점등/소등한다. 슬라이더 등 개별 수동 조작은 그대로 허용한다.
  if (updates.ledMode !== undefined) {
    next.ledGrowLight = updates.ledMode === 'day';
    next.ledIntensity = next.ledGrowLight ? next.lastLedIntensity : 0;
  }
  if (updates.coolingDamper === true && updates.heatingDamper === true) {
    next = { ...next, coolingDamper: false, heatingDamper: false, autoMode: false };
  } else if (updates.coolingDamper === true) next.heatingDamper = false;
  else if (updates.heatingDamper === true) next.coolingDamper = false;
  if (updates.autoMode === true && next.autoMode) {
    if (hasControlUpdates) next.automaticSettings = automaticSettings(next);
    next = automaticControl(next, zone, now);
  }
  return next;
}
