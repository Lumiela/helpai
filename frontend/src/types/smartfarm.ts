// 큐브 식별자는 장치 일련번호(SC-01)를 그대로 쓴다. (2026-09-22 백엔드 연결 전에는 'zone-1' 같은 고정값이었다)
export type ZoneId = string;

// 값이 null 이면 그 항목의 실측 센서가 없다는 뜻이다. 화면은 '—' 로 표시하고 경고·그래프를 만들지 않는다.
export interface SensorReading {
  temperature: number | null; // °C
  humidity: number | null; // %
  co2: number | null; // ppm
  ledIntensityKlx: number | null; // klx
  soilMoisture: number | null; // %
  soilTemp: number | null; // °C
  ec: number | null; // dS/m (전기전도도)
  ph: number | null; // pH
  leafTemp: number | null; // °C
  vpd: number | null; // kPa (포화수증기압차)
}

// 장치가 접속 직후 보내는 상태 프레임에서 뽑은 정보
export interface DeviceInfo {
  model: string | null;
  firmware: string | null;
  ip: string | null;
  outputsEnabled: boolean | null; // false 면 펌웨어가 모든 제어 명령을 거부한다
  leaseMs: number | null; // 제어 명령 유지 시간. 지나면 장치가 안전 상태로 돌아간다
  pumpMaxMs: number | null; // 펌프 1회 최대 가동 시간
}

// 배지 수분 센서의 부속 정보
export interface MoistureInfo {
  raw: number | null;
  volts: number | null;
  calibrated: boolean;
  adcOk: boolean;
}

export interface ValueRange {
  min: number;
  max: number;
}

// 2026-10-06: 화면의 허용 범위. 작물이 배정된 큐브는 서버 작물표의 지금 시간대(주간/야간) 범위, 없으면 null('—').
// lux 는 lx 단위(화면 klx 로 바꿔 표시). lib/zoneRanges.ts 참고.
export interface ZoneRanges {
  temp: ValueRange | null;
  humidity: ValueRange | null;
  co2: ValueRange | null;
  ec: ValueRange | null;
  ph: ValueRange | null;
  soil: ValueRange | null;
  lux: ValueRange | null;
}

export interface ZoneConfig {
  id: ZoneId;
  code: string; // 화면 표기용 큐브 코드 (SC-01)
  name: string; // 스마트큐브 SC-01
  crop: string;
  growthStage: string;
  targetTemp: { min: number; max: number; currentTarget: number; nightTarget: number }; // currentTarget = 주간 목표
  targetHumidity: { min: number; max: number };
  targetCo2: { min: number; max: number };
  targetEc: { min: number; max: number };
  targetPh: { min: number; max: number };
  photoperiod: { on: number; off: number }; // 주간 시작·종료 시각 (0~24, 소수점은 분)
  lightSaturation: { peakPpfd: number; rampHours: number }; // 시각화용 참고 프로파일, 실측 최적값 아님
  // 2026-10-06 작물 기반 자동 제어: 배정된 작물 ID(없으면 null)와 허용 범위. 시험 픽스처에는 없다(옛 target* 를 씀).
  cropId?: string | null;
  ranges?: ZoneRanges;
  sensors: SensorReading;
  // 아래는 백엔드(장치)에서 온 것
  live: boolean; // 최근 몇 초 안에 텔레메트리를 받았는지
  lastSeen: number | null; // 마지막 텔레메트리 epoch ms
  session: string | null; // 펌웨어가 접속마다 새로 발급하는 세션
  signalDbm: number | null; // 무선 신호 세기
  chipTempC: number | null; // ESP32 칩 내부 온도 (°C, 보정 없음) — 2026-09-30
  moisture: MoistureInfo | null;
  device: DeviceInfo | null;
}

export interface ActuatorState {
  outerFan: boolean; // 배기팬
  outerFanSpeed: number; // 1-3단
  innerFan: boolean; // 순환팬
  innerFanSpeed: number; // 1-3단
  ledMode: 'day' | 'night'; // 광 주기
  coolingDamper: boolean; // 냉각 댐퍼 (자동)
  heatingDamper: boolean; // 가온 댐퍼 (자동)
  irrigationPump: boolean;
  nutrientValve: boolean;
  ledGrowLight: boolean;
  ledIntensity: number; // 0-100%
  co2Generator: boolean;
  autoMode: boolean;
  activePresetName?: string | null;
  activePresetId?: string | null;
  lastLedIntensity?: number; // 소등 후 복원할 마지막 유효 밝기
  automaticSettings?: Omit<ActuatorState, 'autoMode' | 'activePresetName' | 'activePresetId' | 'automaticSettings' | 'irrigationPump' | 'nutrientValve'>;
}

export interface NotificationLogItem {
  id: string;
  type: 'toast' | 'info' | 'message' | 'discover';
  title?: string;
  content: string;
  timestamp: string; // 표시용 (오전/오후 H:mm)
  at?: number; // 발생 시각 epoch ms (기간 조회용)
  severity: 'info' | 'warning' | 'danger' | 'success';
  zoneId?: ZoneId;
  handled?: boolean;
}

export interface EdgeDevice {
  id: string;
  name: string;
  type: 'sensor_node' | 'actuator_controller' | 'camera' | 'weather_station' | 'irrigation_unit';
  ipAddress: string;
  macAddress: string;
  firmwareVersion: string;
  status: 'connected' | 'offline' | 'unpaired' | 'updating';
  signalStrength: number | null; // dBm
  chipTempC: number | null; // ESP32 칩 내부 온도 (°C) — 2026-09-30
  lastPing: string;
  streaming?: boolean; // 카메라만: 지금 스트리밍 중인지 — 2026-09-30
  note?: string | null; // 카메라만: 사용자가 붙인 이름
}
