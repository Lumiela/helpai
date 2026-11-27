// 장치(펌웨어) 값 ↔ 화면 모델 대응 규칙. 순수 함수만 두어 테스트한다.
//
// 사용자 결정(2026-09-22):
//   배기팬 → fan1, 순환팬 → fan2, 팬 1·2·3단 = 33·66·100%, 정지 = 0
//   LED 밝기 슬라이더 → led1 만 (led2 는 보내지 않는다 = api 가 현재값 유지)
//   급수 → pump
//   CO₂·댐퍼·양액 밸브 → 장치에 없음. 화면 상태만 바뀐다
//   실측 없는 센서는 null ('—' 표시)
import type { ActuatorState, DeviceInfo, EdgeDevice, MoistureInfo, SensorReading, ZoneConfig } from '../types/smartfarm';
import type { CameraInfo, ControlBody, StatusFrame, Telemetry } from './backend';

// 이 시간 안에 텔레메트리가 없으면 '통신 끊김'. 펌웨어는 3초마다 보낸다.
export const STALE_MS = 15_000;

export const FAN_LEVEL_PERCENT = [0, 33, 66, 100] as const;

export function fanLevelToPercent(level: number): number {
  const index = Math.max(0, Math.min(3, Math.round(level)));
  return FAN_LEVEL_PERCENT[index];
}

export function fanPercentToLevel(percent: number): number {
  if (!Number.isFinite(percent) || percent <= 0) return 0;
  if (percent <= 33) return 1;
  if (percent <= 66) return 2;
  return 3;
}

export const EMPTY_SENSORS: SensorReading = {
  temperature: null,
  humidity: null,
  co2: null,
  ledIntensityKlx: null,
  soilMoisture: null,
  soilTemp: null,
  ec: null,
  ph: null,
  leafTemp: null,
  vpd: null,
};

// 텔레메트리에서 실측으로 쓰는 것: 배지 수분(ADS1115), 온도·습도(SHT45 평균), 조도(BH1750), EC·pH(RS485 토양 센서), CO2(HX-2000U).
// 보정 전이거나 ADC 가 없으면 배지 수분은 없는 것으로 본다. 온습도·조도는 숫자가 아니면(null·필드 없음) '—'.
// 사용자 결정(2026-09-29): 값이 안 들어오면 '—' 로 표시.
function numberOrNull(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

export function sensorsFromTelemetry(values: Telemetry | null | undefined): SensorReading {
  if (!values) return EMPTY_SENSORS;
  const usable = values.ads1115_ok && values.moisture_calibrated && typeof values.moisture_pct === 'number';
  const lux = numberOrNull(values.lux);
  const soilEc = numberOrNull(values.soil_ec);
  return {
    ...EMPTY_SENSORS,
    soilMoisture: usable ? values.moisture_pct : null,
    temperature: numberOrNull(values.temperature),
    humidity: numberOrNull(values.humidity),
    // 화면 단위는 klx. 펌웨어는 lx 로 보낸다.
    ledIntensityKlx: lux === null ? null : Math.round(lux) / 1000,
    // 화면 단위는 dS/m. 펌웨어(RS485 토양 센서)는 µS/cm 로 보낸다.
    ec: soilEc === null ? null : soilEc / 1000,
    ph: numberOrNull(values.soil_ph),
    // CO2 HX-2000U (ppm 그대로). 예열 중이면 펌웨어가 null 을 보낸다.
    co2: numberOrNull(values.co2_ppm),
  };
}

export function moistureFromTelemetry(values: Telemetry | null | undefined): MoistureInfo | null {
  if (!values) return null;
  return {
    raw: typeof values.moisture_raw === 'number' ? values.moisture_raw : null,
    volts: typeof values.moisture_v === 'number' ? values.moisture_v : null,
    calibrated: Boolean(values.moisture_calibrated),
    adcOk: Boolean(values.ads1115_ok),
  };
}

export function deviceFromStatus(status: StatusFrame | null | undefined): DeviceInfo | null {
  if (!status) return null;
  return {
    model: status.model ?? null,
    firmware: status.fw ?? null,
    ip: status.ip ?? null,
    outputsEnabled: typeof status.outputs_enabled === 'boolean' ? status.outputs_enabled : null,
    leaseMs: typeof status.lease_ms === 'number' ? status.lease_ms : null,
    pumpMaxMs: typeof status.pump_max_ms === 'number' ? status.pump_max_ms : null,
  };
}

// 장치가 없을 때의 화면 초기값. 자동 제어 켜짐 여부는 서버(api 자동 제어)가 진실이라 App 이 덮어쓴다(2026-10-06).
export const DEFAULT_ACTUATOR: ActuatorState = {
  outerFan: false,
  outerFanSpeed: 0,
  innerFan: false,
  innerFanSpeed: 0,
  ledMode: 'day',
  coolingDamper: false,
  heatingDamper: false,
  irrigationPump: false,
  nutrientValve: false,
  ledGrowLight: false,
  ledIntensity: 0,
  co2Generator: false,
  autoMode: false,
};

// 장치가 보고한 출력 상태를 화면 상태에 덮어쓴다. 장치에 없는 항목(댐퍼·CO₂·프리셋)은 그대로 둔다.
export function actuatorFromTelemetry(previous: ActuatorState | undefined, values: Telemetry): ActuatorState {
  const base = previous ?? DEFAULT_ACTUATOR;
  const led = Math.max(0, Math.min(100, Math.round(values.led1)));
  return {
    ...base,
    outerFan: values.fan1 > 0,
    outerFanSpeed: fanPercentToLevel(values.fan1),
    innerFan: values.fan2 > 0,
    innerFanSpeed: fanPercentToLevel(values.fan2),
    ledGrowLight: led > 0,
    ledIntensity: led,
    ledMode: led > 0 ? 'day' : base.ledMode,
    irrigationPump: Boolean(values.pump),
    nutrientValve: Boolean(values.pump),
    lastLedIntensity: led > 0 ? led : base.lastLedIntensity,
    autoMode: false,
  };
}

// 화면 상태 → 장치 명령. led2 는 넣지 않는다(사용자 결정: LED 는 led1 만).
export function controlFromActuator(a: ActuatorState): ControlBody {
  return {
    fan1: a.outerFan ? fanLevelToPercent(a.outerFanSpeed) : 0,
    fan2: a.innerFan ? fanLevelToPercent(a.innerFanSpeed) : 0,
    led1: a.ledGrowLight ? Math.max(0, Math.min(100, Math.round(a.ledIntensity))) : 0,
    pump: Boolean(a.irrigationPump),
  };
}

// 이전 화면 상태와 비교해 바뀐 출력만 보낸다. 빠진 키는 api 가 장치의 마지막 값으로 채우므로,
// 팬만 바꿨는데 화면에 남은 낡은 pump 값이 같이 나가 펌프가 다시 켜지거나 꺼지는 일을 막는다.
export function controlDiff(before: ActuatorState, after: ActuatorState): ControlBody {
  const a = controlFromActuator(before);
  const b = controlFromActuator(after);
  const diff: ControlBody = {};
  for (const key of Object.keys(b) as Array<keyof ControlBody>) {
    if (a[key] !== b[key]) (diff as Record<string, number | boolean | undefined>)[key] = b[key];
  }
  return diff;
}

export const STOP_ALL: ControlBody = { fan1: 0, fan2: 0, led1: 0, pump: false };

const DEVICE_KEYS: Array<keyof ActuatorState> = [
  'outerFan', 'outerFanSpeed', 'innerFan', 'innerFanSpeed', 'ledGrowLight', 'ledIntensity', 'ledMode', 'irrigationPump',
];

// 이 변경이 장치 출력에 해당하는지 (해당하면 명령을 보낸다)
export function touchesDevice(updates: Partial<ActuatorState>): boolean {
  return DEVICE_KEYS.some((key) => updates[key] !== undefined);
}

// 큐브별 재배 설정. 장치는 이런 정보를 주지 않으므로 화면 쪽 기본값이다(측정값 아님).
export interface ZoneProfile {
  crop: string;
  growthStage: string;
  targetTemp: ZoneConfig['targetTemp'];
  targetHumidity: ZoneConfig['targetHumidity'];
  targetCo2: ZoneConfig['targetCo2'];
  targetEc: ZoneConfig['targetEc'];
  targetPh: ZoneConfig['targetPh'];
  photoperiod: ZoneConfig['photoperiod'];
  lightSaturation: ZoneConfig['lightSaturation'];
  // 2026-10-06: 서버 작물표에서 온 것(App 이 채운다). 없으면 화면은 옛 target* 를 쓴다(시험 픽스처).
  cropId?: string | null;
  ranges?: ZoneConfig['ranges'];
}

export const DEFAULT_PROFILE: ZoneProfile = {
  crop: '작물 미설정',
  growthStage: '생육 단계 미설정',
  targetTemp: { min: 20, max: 26, currentTarget: 23.5, nightTarget: 20.0 },
  targetHumidity: { min: 65, max: 80 },
  targetCo2: { min: 700, max: 1000 },
  targetEc: { min: 2.2, max: 2.8 },
  targetPh: { min: 5.8, max: 6.5 },
  photoperiod: { on: 6, off: 22 },
  lightSaturation: { peakPpfd: 1000, rampHours: 2 },
};

export interface TelemetrySample {
  values: Telemetry;
  seen: number; // epoch ms
}

export function isLive(seen: number | null | undefined, now: number): boolean {
  return typeof seen === 'number' && now - seen < STALE_MS;
}

export function buildZone(sn: string, profile: ZoneProfile, sample: TelemetrySample | null | undefined, status: StatusFrame | null | undefined, now: number): ZoneConfig {
  const values = sample?.values ?? null;
  return {
    id: sn,
    code: sn,
    name: `스마트큐브 ${sn}`,
    ...profile,
    sensors: sensorsFromTelemetry(values),
    live: isLive(sample?.seen, now),
    lastSeen: sample?.seen ?? null,
    session: values?.session ?? status?.session ?? null,
    signalDbm: typeof values?.rssi_dbm === 'number' ? values.rssi_dbm : null,
    chipTempC: numberOrNull(values?.chip_temp),
    moisture: moistureFromTelemetry(values),
    device: deviceFromStatus(status),
  };
}

export function relativeTime(at: number | null | undefined, now: number): string {
  if (typeof at !== 'number') return '수신 없음';
  const seconds = Math.max(0, Math.round((now - at) / 1000));
  if (seconds < 2) return '방금 전';
  if (seconds < 60) return `${seconds}초 전`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}분 전`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}시간 전`;
  return `${Math.floor(hours / 24)}일 전`;
}

export function buildDevice(zone: ZoneConfig, now: number): EdgeDevice {
  return {
    id: zone.id,
    name: `${zone.code} 큐브 제어기`,
    type: 'actuator_controller',
    ipAddress: zone.device?.ip ?? '—',
    macAddress: '—',
    firmwareVersion: zone.device?.firmware ?? '—',
    status: zone.live ? 'connected' : 'offline',
    signalStrength: zone.signalDbm,
    chipTempC: zone.chipTempC,
    lastPing: relativeTime(zone.lastSeen, now),
  };
}

// 카메라 보드 → 장치 목록의 한 줄 (2026-09-30, 사용자 지시: 큐브 제어기와 같은 목록에).
//   장치 "CUBE001 1번 카메라" · ID "CUBE001-C1" · 종류 카메라 · 나머지는 카메라 status 프레임에 있는 것만.
//   카메라 펌웨어가 status 에 ip · mac · rssi_dbm · chip_temp 를 넣으면 그대로 표시되고, 없으면 '—'.
//   카메라는 주기 텔레메트리가 없어 접속 판정을 큐브(15초)보다 느슨하게 10분으로 둔다(기존 카메라 목록 규칙 유지).
export const CAMERA_STALE_S = 600;

export function cameraToDevice(cam: CameraInfo, now: number): EdgeDevice {
  const match = /^(.+)-C(\d+)$/.exec(cam.sn);
  const cube = cam.cube ?? match?.[1] ?? null;
  const number = cam.number ?? (match ? Number.parseInt(match[2], 10) : null);
  const status = cam.status ?? {};
  const text = (key: string): string => {
    const v = status[key];
    return typeof v === 'string' && v.length > 0 ? v : '—';
  };
  const seenMs = typeof cam.seen === 'number' ? cam.seen * 1000 : null;
  const online = seenMs !== null && now - seenMs < CAMERA_STALE_S * 1000;
  return {
    id: cam.sn,
    name: cube !== null && number !== null ? `${cube} ${number}번 카메라` : `${cam.sn} 카메라`,
    type: 'camera',
    ipAddress: text('ip'),
    macAddress: text('mac'),
    firmwareVersion: text('fw'),
    status: online ? 'connected' : 'offline',
    signalStrength: numberOrNull(status.rssi_dbm),
    chipTempC: numberOrNull(status.chip_temp),
    lastPing: relativeTime(seenMs, now),
    streaming: Boolean(cam.stream?.on),
    note: cam.name ?? null,
  };
}

// 큐브 제어기 바로 아래에 그 큐브의 카메라가 오도록 한 목록으로 합친다. 큐브가 목록에 없는 카메라는 맨 뒤.
export function mergeDeviceRows(devices: EdgeDevice[], cameras: CameraInfo[], now: number): EdgeDevice[] {
  const rows: EdgeDevice[] = [];
  const used = new Set<string>();
  const byNumber = (a: CameraInfo, b: CameraInfo) => (a.number ?? 0) - (b.number ?? 0) || a.sn.localeCompare(b.sn);
  for (const device of devices) {
    rows.push(device);
    for (const cam of cameras.filter((c) => (c.cube ?? /^(.+)-C\d+$/.exec(c.sn)?.[1]) === device.id).sort(byNumber)) {
      rows.push(cameraToDevice(cam, now));
      used.add(cam.sn);
    }
  }
  for (const cam of cameras.filter((c) => !used.has(c.sn)).sort(byNumber)) rows.push(cameraToDevice(cam, now));
  return rows;
}

// 화면 표시용. null 은 '—'.
export function fmt(value: number | null | undefined, decimals: number): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(decimals) : '—';
}
