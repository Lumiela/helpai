import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  actuatorFromTelemetry, buildDevice, buildZone, cameraToDevice, controlDiff, controlFromActuator, DEFAULT_ACTUATOR, DEFAULT_PROFILE, fanLevelToPercent, mergeDeviceRows,
  fanPercentToLevel, fmt, isLive, relativeTime, sensorsFromTelemetry, STALE_MS, touchesDevice,
} from './deviceMap';
import type { CameraInfo, Telemetry } from './backend';

const telemetry: Telemetry = {
  session: 'd306fd958bed21950fbbc3ee49b78d66', uptime_ms: 1000, applied_seq: 0, ads1115_ok: true, moisture_calibrated: true,
  moisture_raw: 24, moisture_v: 0.158, moisture_pct: 100, fan1_rpm: -1, fan2_rpm: -1, led1: 0, led2: 0, pump: false,
  fan1: 100, fan2: 100, lease_active: false, pump_locked: false, rssi_dbm: -54,
};

test('팬 단수와 퍼센트는 33·66·100 으로 왕복한다', () => {
  assert.deepEqual([0, 1, 2, 3].map(fanLevelToPercent), [0, 33, 66, 100]);
  assert.deepEqual([0, 1, 33, 34, 66, 67, 100].map(fanPercentToLevel), [0, 1, 1, 2, 2, 3, 3]);
  for (const level of [0, 1, 2, 3]) assert.equal(fanPercentToLevel(fanLevelToPercent(level)), level);
});

test('옛 펌웨어 텔레메트리에서 실측은 배지 수분뿐이고 나머지는 null 이다', () => {
  const sensors = sensorsFromTelemetry(telemetry);
  assert.equal(sensors.soilMoisture, 100);
  for (const key of ['temperature', 'humidity', 'co2', 'ledIntensityKlx', 'soilTemp', 'ec', 'ph', 'leafTemp', 'vpd'] as const) {
    assert.equal(sensors[key], null);
  }
  assert.equal(sensorsFromTelemetry({ ...telemetry, moisture_calibrated: false }).soilMoisture, null);
  assert.equal(sensorsFromTelemetry({ ...telemetry, ads1115_ok: false, moisture_pct: null }).soilMoisture, null);
  assert.equal(sensorsFromTelemetry(null).soilMoisture, null);
});

test('devkit 1.1.0 텔레메트리의 온도·습도·조도가 화면 값으로 들어가고, null 이면 그대로 null 이다', () => {
  const full = sensorsFromTelemetry({
    ...telemetry, temperature: 24.3, humidity: 55.1, lux: 1840.4, temp_1: 24.1, hum_1: 55.0, temp_2: 24.5, hum_2: 55.2,
    temp_3: null, hum_3: null, sht45_count: 2,
  });
  assert.equal(full.temperature, 24.3);
  assert.equal(full.humidity, 55.1);
  assert.equal(full.ledIntensityKlx, 1.84); // lx → klx
  assert.equal(full.soilMoisture, 100);

  const none = sensorsFromTelemetry({ ...telemetry, temperature: null, humidity: null, lux: null, sht45_count: 0 });
  assert.equal(none.temperature, null);
  assert.equal(none.humidity, null);
  assert.equal(none.ledIntensityKlx, null);
  assert.equal(none.co2, null);
});

test('RS485 토양 센서의 EC·pH 가 화면 값으로 들어간다 (EC 는 µS/cm → dS/m)', () => {
  const soil = sensorsFromTelemetry({ ...telemetry, soil_ec: 1280, soil_ph: 6.4 });
  assert.equal(soil.ec, 1.28);
  assert.equal(soil.ph, 6.4);
  const none = sensorsFromTelemetry({ ...telemetry, soil_ec: null, soil_ph: null });
  assert.equal(none.ec, null);
  assert.equal(none.ph, null);
});

test('CO2 센서(co2_ppm)가 화면 값으로 들어가고, 예열 중(null)이면 그대로 null 이다', () => {
  assert.equal(sensorsFromTelemetry({ ...telemetry, co2_ok: true, co2_ppm: 420 }).co2, 420);
  assert.equal(sensorsFromTelemetry({ ...telemetry, co2_ok: true, co2_ppm: null }).co2, null);
});

test('장치 보고값이 화면 구동기 상태로 들어가고 화면 전용 항목은 유지된다', () => {
  const previous = { ...DEFAULT_ACTUATOR, co2Generator: true, coolingDamper: true, lastLedIntensity: 40 };
  const state = actuatorFromTelemetry(previous, { ...telemetry, fan1: 66, fan2: 0, led1: 55, pump: true });
  assert.equal(state.outerFan, true);
  assert.equal(state.outerFanSpeed, 2);
  assert.equal(state.innerFan, false);
  assert.equal(state.innerFanSpeed, 0);
  assert.equal(state.ledGrowLight, true);
  assert.equal(state.ledIntensity, 55);
  assert.equal(state.lastLedIntensity, 55);
  assert.equal(state.irrigationPump, true);
  assert.equal(state.co2Generator, true);
  assert.equal(state.coolingDamper, true);
  assert.equal(state.autoMode, false);
  const dark = actuatorFromTelemetry(state, { ...telemetry, led1: 0 });
  assert.equal(dark.ledGrowLight, false);
  assert.equal(dark.lastLedIntensity, 55);
});

test('화면 상태에서 만든 명령은 fan1·fan2·led1·pump 네 키만 담고 led2 는 없다', () => {
  const body = controlFromActuator({ ...DEFAULT_ACTUATOR, outerFan: true, outerFanSpeed: 1, innerFan: true, innerFanSpeed: 3, ledGrowLight: true, ledIntensity: 70, irrigationPump: true });
  assert.deepEqual(body, { fan1: 33, fan2: 100, led1: 70, pump: true });
  assert.equal('led2' in body, false);
  assert.deepEqual(controlFromActuator({ ...DEFAULT_ACTUATOR, ledGrowLight: false, ledIntensity: 70 }), { fan1: 0, fan2: 0, led1: 0, pump: false });
});

test('장치 출력에 해당하는 변경만 명령을 만든다', () => {
  assert.equal(touchesDevice({ outerFanSpeed: 2 }), true);
  assert.equal(touchesDevice({ ledIntensity: 10 }), true);
  assert.equal(touchesDevice({ irrigationPump: true }), true);
  assert.equal(touchesDevice({ coolingDamper: true }), false);
  assert.equal(touchesDevice({ co2Generator: true }), false);
  assert.equal(touchesDevice({ autoMode: false }), false);
  assert.equal(touchesDevice({}), false);
});

test('통신 끊김 판정과 상대 시각', () => {
  const now = 1_790_000_000_000;
  assert.equal(isLive(now - STALE_MS + 1, now), true);
  assert.equal(isLive(now - STALE_MS, now), false);
  assert.equal(isLive(null, now), false);
  assert.equal(relativeTime(now - 500, now), '방금 전');
  assert.equal(relativeTime(now - 4_000, now), '4초 전');
  assert.equal(relativeTime(now - 90_000, now), '1분 전');
  assert.equal(relativeTime(null, now), '수신 없음');
});

test('큐브 화면 모델은 일련번호·실측·상태 정보를 합친다', () => {
  const now = 1_790_000_000_000;
  const zone = buildZone('SC-01', DEFAULT_PROFILE, { values: telemetry, seen: now - 3_000 }, {
    name: 'ag.smartcube.esp32', vendor: 'SmartCube', model: 'ESP32-WROOM-32D', sn: 'SC-01', fw: 'esp32-1.0.0', ip: '192.168.20.36',
    transport: 'wifi-mqtt-tls', session: telemetry.session, last_received_seq: 2, outputs_enabled: false, lease_ms: 15000, pump_max_ms: 10000, protocol: 'ag-value-v1',
  }, now);
  assert.equal(zone.id, 'SC-01');
  assert.equal(zone.code, 'SC-01');
  assert.equal(zone.live, true);
  assert.equal(zone.sensors.soilMoisture, 100);
  assert.equal(zone.sensors.temperature, null);
  assert.equal(zone.signalDbm, -54);
  assert.equal(zone.device?.outputsEnabled, false);
  assert.equal(zone.device?.pumpMaxMs, 10000);
  assert.equal(zone.moisture?.raw, 24);
  const silent = buildZone('SC-02', DEFAULT_PROFILE, null, null, now);
  assert.equal(silent.live, false);
  assert.equal(silent.device, null);
  assert.equal(silent.sensors.soilMoisture, null);
});

test('표시 형식은 null 을 대시로 낸다', () => {
  assert.equal(fmt(12.345, 1), '12.3');
  assert.equal(fmt(null, 1), '—');
  assert.equal(fmt(Number.NaN, 2), '—');
});

test('칩 온도(chip_temp)는 큐브와 장치 목록에 그대로 실리고, 없으면 null 이다', () => {
  const now = Date.now();
  const withTemp = buildZone('CUBE001', DEFAULT_PROFILE, { values: { ...telemetry, chip_temp: 52.4 }, seen: now - 1_000 }, null, now);
  assert.equal(withTemp.chipTempC, 52.4);
  assert.equal(buildDevice(withTemp, now).chipTempC, 52.4);
  const without = buildZone('CUBE001', DEFAULT_PROFILE, { values: telemetry, seen: now - 1_000 }, null, now);
  assert.equal(without.chipTempC, null);
  assert.equal(buildDevice(without, now).chipTempC, null);
  const nulled = buildZone('CUBE001', DEFAULT_PROFILE, { values: { ...telemetry, chip_temp: null }, seen: now - 1_000 }, null, now);
  assert.equal(nulled.chipTempC, null);
});

test('카메라는 큐브 제어기 바로 아래 줄로 들어가고, status 에 없는 값은 — 로 남는다', () => {
  const now = Date.now();
  const stream = { on: false, since: null, until: null, frames: 0, last_frame: null, viewers: 0 };
  const cam1: CameraInfo = {
    sn: 'CUBE001-C1', cube: 'CUBE001', number: 1, name: null, seen: now / 1000 - 30,
    status: { sn: 'CUBE001-C1', fw: 'cube-cam-0.1', model: 'CAM-V1', sensor: 'unknown', stream: 0, heap: 151224 },
    last_file: null, last_taken: null, stream,
  };
  const cam2: CameraInfo = {
    sn: 'CUBE001-C2', cube: 'CUBE001', number: 2, name: '측면', seen: now / 1000 - 5,
    status: { fw: 'cube-cam-0.2', ip: '192.168.20.77', rssi_dbm: -52, chip_temp: 61.2 },
    last_file: null, last_taken: null, stream: { ...stream, on: true },
  };
  const orphan: CameraInfo = { sn: 'CUBE009-C1', cube: null, number: null, seen: null, status: null, last_file: null, last_taken: null, stream };

  const d1 = cameraToDevice(cam1, now);
  assert.equal(d1.name, 'CUBE001 1번 카메라');
  assert.equal(d1.id, 'CUBE001-C1');
  assert.equal(d1.type, 'camera');
  assert.equal(d1.ipAddress, '—');
  assert.equal(d1.firmwareVersion, 'cube-cam-0.1');
  assert.equal(d1.signalStrength, null);
  assert.equal(d1.status, 'connected');
  assert.equal(d1.streaming, false);

  const d2 = cameraToDevice(cam2, now);
  assert.equal(d2.ipAddress, '192.168.20.77');
  assert.equal(d2.signalStrength, -52);
  assert.equal(d2.chipTempC, 61.2);
  assert.equal(d2.streaming, true);
  assert.equal(d2.note, '측면');

  const d9 = cameraToDevice(orphan, now);
  assert.equal(d9.name, 'CUBE009 1번 카메라');
  assert.equal(d9.status, 'offline');
  assert.equal(d9.lastPing, '수신 없음');

  const cube = buildDevice(buildZone('CUBE001', DEFAULT_PROFILE, { values: telemetry, seen: now - 1_000 }, null, now), now);
  const rows = mergeDeviceRows([cube], [cam2, orphan, cam1], now);
  assert.deepEqual(rows.map((r) => r.id), ['CUBE001', 'CUBE001-C1', 'CUBE001-C2', 'CUBE009-C1']);
});

test('명령에는 바뀐 출력만 담는다 — 팬만 바꾸면 화면에 남은 pump 값은 보내지 않는다', () => {
  const before = { ...DEFAULT_ACTUATOR, irrigationPump: true, ledGrowLight: true, ledIntensity: 70 };
  assert.deepEqual(controlDiff(before, { ...before, outerFan: true, outerFanSpeed: 2 }), { fan1: 66 });
  assert.deepEqual(controlDiff(before, { ...before, irrigationPump: false }), { pump: false });
  assert.deepEqual(controlDiff(before, { ...before, ledIntensity: 40 }), { led1: 40 });
  assert.deepEqual(controlDiff(before, { ...before }), {});
});
