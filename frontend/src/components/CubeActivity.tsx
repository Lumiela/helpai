import React from 'react';
import { Droplets, Fan, Sun } from 'lucide-react';
import type { ActuatorState, SensorReading } from '../types/smartfarm';
import { fmt } from '../lib/deviceMap';

// 2026-11-23: 실제 제작한 큐브를 그린 그림(public/smart-cube-front.webp, 1295×1214)을 바탕에 두고
// 장치 상태를 그 위에 겹쳐 보여 준다. 앞에서 본 모습이며 팬은 둘 다 뒷면 벽(배기팬 오른쪽 위, 순환팬 왼쪽 아래).
//   LED 꺼짐 → 내부를 어둡게, 켜짐 → 밝기만큼 분홍빛과 LED 바 발광
//   팬 가동 → 그림의 팬 위에서 날개가 돌고 바람 화살표, 급수 → 아래 칸 물통에서 물이 관을 타고 올라가 트레이(화분 밑)로

// 그림 좌표(px) → 화면 좌표. 높이 450 으로 놓는다. 그림 왼쪽의 제어함 바깥 부분은 잘라 내고(CROP_X),
// 제어함에 가려졌던 왼쪽 뒤 기둥과 옆면 벽은 그림 위에 다시 그려 채운다(SidePatch).
const S = 450 / 1214;
const CROP_X = 219;
// 왼쪽 옆면 복원: 그림에서 제어함 위로 보이는 옆면 띠(srcTop~srcBottom)를 제어함 자리(top~bottom)에 늘려 붙인다
const SIDE = { x: 219, w: 59, srcTop: 134, srcBottom: 212, top: 206, bottom: 852 };
const IMG_W = (1295 - CROP_X) * S;
const IMG_X = 360 - IMG_W / 2;
const px = (x: number) => IMG_X + (x - CROP_X) * S;
const py = (y: number) => 10 + y * S;

const EXHAUST = { x: px(944), y: py(283) };
const CIRCULATION = { x: px(396), y: py(539) };
const CHAMBER = { x: px(322), y: py(182), w: (1178 - 322) * S, h: (932 - 182) * S };
const LED_BAR = { x: px(330), y: py(184), w: (1135 - 330) * S };
const LUX = { x: px(470), y: py(214) };
const SOIL = { x: px(668), y: py(748) };
// 생육 카메라: 천장 가운데(LED 바 바로 아래)에서 화분을 내려다본다
const CAMERA_IMG = { x: 750, y: 214 };
const CAMERA = { x: px(CAMERA_IMG.x), y: py(CAMERA_IMG.y) };
// 아래 칸 물통(그림 좌표). 그림의 원근에 맞춰 앞면(f)과 뒷면(b) 모서리를 정했다.
//   아래 칸: 앞 사각형 x 316~1180·바닥 1160, 뒤 사각형 x 338~1043·바닥 1124 사이에서 깊이 0.3~0.8, 폭 25~70% 자리
const T = { fl: 527, fr: 894, ft: 1017, fb: 1149, bl: 518, br: 849, bt: 1012, bb: 1131, water: 1072, pump: 846 };
const TANK_IMG = { x: T.fl, y: T.ft, w: T.fr - T.fl, h: T.fb - T.ft };
// 물통 앞을 가리는 앞쪽 가로대(그림에서 그대로 잘라 와 덮는다)
const FRONT_RAILS = [{ x: 293, y: 921, w: 932, h: 62 }, { x: 293, y: 1128, w: 932, h: 64 }];
const TANK = { x: px(TANK_IMG.x), y: py(TANK_IMG.y), w: TANK_IMG.w * S, h: TANK_IMG.h * S };

// 탐험 모드에서 누를 수 있는 부품
export type CubePart = 'plant' | 'led' | 'lux' | 'camera' | 'exhaust' | 'circulation' | 'soil' | 'tank';
export interface CubeExplore {
  picked: CubePart;
  onPick: (part: CubePart) => void;
  bubbles: Record<CubePart, string>; // 말풍선 글자(짧게)
  alert?: Partial<Record<CubePart, boolean>>; // 살펴볼 필요가 있는 부품(주황 테두리)
}

// 말풍선 자리(화면 좌표)와 말풍선이 가리키는 점
const PART_SPOTS: Record<CubePart, { x: number; y: number; ax: number; ay: number }> = {
  camera: { x: px(750), y: py(110), ax: px(750), ay: py(206) },
  led: { x: px(1060), y: py(150), ax: px(1000), ay: py(186) },
  lux: { x: px(470), y: py(290), ax: px(470), ay: py(222) },
  exhaust: { x: px(944), y: py(400), ax: px(944), ay: py(330) },
  circulation: { x: px(396), y: py(440), ax: px(396), ay: py(500) },
  plant: { x: px(800), y: py(560), ax: px(780), ay: py(640) },
  soil: { x: px(560), y: py(700), ax: px(668), ay: py(750) },
  tank: { x: px(700), y: py(1060), ax: px(700), ay: py(1060) },
};

// minimal: 다른 카드가 값과 상태를 보여 줄 때 쓴다. 라벨·상태 칩을 숨기고 큐브를 크게 그린다.
// highlight: 강조할 장치(그림에서 그 부품 둘레가 빛난다). ledBadge: LED가 켜져 있으면 밝기를 그림 위에 쓴다.
export type CubeDevice = 'exhaust' | 'circulation' | 'led' | 'water';
export function CubeActivity({ actuator: a, live, sensors, plantLabel, explore, minimal = false, highlight, ledBadge = false }: { actuator: ActuatorState; live: boolean; sensors?: Pick<SensorReading, 'soilMoisture' | 'ledIntensityKlx'>; plantLabel: string; explore?: CubeExplore; minimal?: boolean; highlight?: CubeDevice | null; ledBadge?: boolean }) {
  const bare = minimal || !!explore;
  const exhaust = live && a.outerFan && a.outerFanSpeed > 0;
  const circulation = live && a.innerFan && a.innerFanSpeed > 0;
  // PREVIEW-ONLY: 시안 확인용 주소 옵션(?demo-water, ?demo-led-off). 수정본에 옮길 때 지운다.
  const demo = new URLSearchParams(window.location.search);
  const led = live && a.ledGrowLight && a.ledIntensity > 0 && !demo.has('demo-led-off');
  const water = (live && a.irrigationPump) || demo.has('demo-water');
  const diagramId = React.useId().replace(/:/g, '');
  const soilText = !live ? '확인 불가' : sensors?.soilMoisture == null ? '센서 없음' : `${fmt(sensors.soilMoisture, 1)}%`;
  const luxText = !live ? '확인 불가' : sensors?.ledIntensityKlx == null ? '센서 없음' : `${fmt(sensors.ledIntensityKlx, 1)} klx`;
  const states = [
    { key: 'exhaust', label: '배기팬', icon: Fan, active: exhaust, value: a.outerFan && a.outerFanSpeed > 0 ? `${a.outerFanSpeed}단 가동` : '정지', speed: a.outerFanSpeed },
    { key: 'circulation', label: '순환팬', icon: Fan, active: circulation, value: a.innerFan && a.innerFanSpeed > 0 ? `${a.innerFanSpeed}단 가동` : '정지', speed: a.innerFanSpeed },
    { key: 'led', label: 'LED', icon: Sun, active: led, value: a.ledGrowLight && a.ledIntensity > 0 ? `${a.ledIntensity}% 점등` : '소등', speed: 0 },
    { key: 'water', label: '급수', icon: Droplets, active: water, value: a.irrigationPump ? '급수 중' : '대기', speed: 0 },
  ];
  const ledLevel = led ? a.ledIntensity / 100 : 0;
  return (
    <>
      <div className="cube-flat-diagram" role="img" aria-label={`스마트큐브 그림(앞에서 본 모습, 팬은 뒷면, 카메라는 천장 가운데). ${states.map(s => `${s.label} ${live ? s.value : '상태 확인 불가'}`).join(', ')}. 토양 수분 센서 ${soilText}, 조도 센서 ${luxText}`}>
        <svg viewBox={bare ? '150 0 420 480' : '0 0 720 480'} aria-hidden="true">
          <defs>
            <marker id={`${diagramId}-air`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#438e83" /></marker>
            <radialGradient id={`${diagramId}-glow`} cx=".5" cy="0" r="1">
              <stop offset="0" stopColor="#ff7fb5" stopOpacity=".5" />
              <stop offset=".7" stopColor="#ffb3d2" stopOpacity=".12" />
              <stop offset="1" stopColor="#ffb3d2" stopOpacity="0" />
            </radialGradient>
          </defs>

          <svg x={IMG_X} y="10" width={IMG_W} height="450" viewBox={`${CROP_X} 0 ${1295 - CROP_X} 1214`} preserveAspectRatio="none">
            <defs>
              <linearGradient id={`${diagramId}-alu`} x1="0" y1="0" x2="1" y2="0">
                <stop offset="0" stopColor="#aeb4b8" /><stop offset=".4" stopColor="#dde1e3" /><stop offset=".75" stopColor="#c6cbce" /><stop offset="1" stopColor="#a2a9ad" />
              </linearGradient>
              <linearGradient id={`${diagramId}-side`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor="#eeb2c7" /><stop offset=".5" stopColor="#efbfd0" /><stop offset="1" stopColor="#efcbd8" />
              </linearGradient>
              <linearGradient id={`${diagramId}-sidefade`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor="#fff" stopOpacity="0" /><stop offset="1" stopColor="#fff" stopOpacity=".28" />
              </linearGradient>
              <pattern id={`${diagramId}-pc`} width="5" height="10" patternUnits="userSpaceOnUse"><path d="M2.5 0V10" stroke="#fff" strokeOpacity=".45" strokeWidth="1.2" /></pattern>
              <linearGradient id={`${diagramId}-water`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor="#a9d8f2" /><stop offset="1" stopColor="#4f9fd3" />
              </linearGradient>
              <linearGradient id={`${diagramId}-cap`} x1="0" y1="0" x2="1" y2="0">
                <stop offset="0" stopColor="#245d93" /><stop offset=".45" stopColor="#5b9bd5" /><stop offset="1" stopColor="#1f4f7d" />
              </linearGradient>
            </defs>
            <image href="/smart-cube-front.webp" x="0" y="0" width="1295" height="1214" />

            {/* 제어함에 가려졌던 왼쪽 옆면: 제어함 바로 위에 보이는 실제 옆면(뒤 기둥·분홍 벽·앞 기둥)을
                세로로 늘려 덮는다. 옆면은 세로 구조라 늘려도 원래 그림체가 유지된다. */}
            <svg x={SIDE.x} y={SIDE.top} width={SIDE.w} height={SIDE.bottom - SIDE.top} viewBox={`${SIDE.x} ${SIDE.srcTop} ${SIDE.w} ${SIDE.srcBottom - SIDE.srcTop}`} preserveAspectRatio="none">
              <image href="/smart-cube-front.webp" x="0" y="0" width="1295" height="1214" />
            </svg>
            {/* 위쪽(LED 가까이)이 더 밝은 분홍이라 아래로 갈수록 살짝 옅게 맞춘다 */}
            <rect x={SIDE.x + 12} y={SIDE.top} width={SIDE.w - 20} height={SIDE.bottom - SIDE.top} fill={`url(#${diagramId}-sidefade)`} />

            {/* 아래 칸 물통: 큐브와 같은 시점의 입체 통(앞면·윗면·오른쪽 옆면). 반투명 플라스틱이라 물과 펌프가 비친다.
                아래쪽은 앞 바닥 프레임 뒤로 들어가도록, 프레임을 다시 그 위에 덮는다(FrontRails). */}
            <g>
              <defs>
                <linearGradient id={`${diagramId}-tankfront`} x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0" stopColor="#e9eef1" /><stop offset=".1" stopColor="#ffffff" /><stop offset=".6" stopColor="#f3f6f7" /><stop offset="1" stopColor="#dde4e8" />
                </linearGradient>
                <linearGradient id={`${diagramId}-tankside`} x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0" stopColor="#d2dade" /><stop offset="1" stopColor="#b8c2c8" />
                </linearGradient>
                <clipPath id={`${diagramId}-tankfrontclip`}><rect x={T.fl + 4} y={T.ft + 4} width={T.fr - T.fl - 8} height={T.fb - T.ft - 8} rx="12" /></clipPath>
              </defs>
              {/* 오른쪽 옆면 */}
              <path d={`M${T.fr} ${T.ft + 10}L${T.br} ${T.bt + 9}V${T.bb}L${T.fr} ${T.fb}Z`} fill={`url(#${diagramId}-tankside)`} stroke="#a3afb6" strokeWidth="1.5" />
              <path d={`M${T.fr} ${T.ft + 60}L${T.br} ${T.bt + 55}V${T.bb - 2}L${T.fr} ${T.fb - 2}Z`} fill="#5b9fcc" opacity=".45" />
              {/* 윗면 */}
              <path d={`M${T.fl + 12} ${T.ft}H${T.fr - 12}L${T.br - 10} ${T.bt}H${T.bl + 10}Z`} fill="#f7f9fa" stroke="#b7c1c7" strokeWidth="1.5" />
              {/* 앞면 */}
              <rect x={T.fl} y={T.ft} width={T.fr - T.fl} height={T.fb - T.ft} rx="14" fill={`url(#${diagramId}-tankfront)`} stroke="#a9b4ba" strokeWidth="2" />
              <g clipPath={`url(#${diagramId}-tankfrontclip)`}>
                <rect x={T.fl} y={T.water} width={T.fr - T.fl} height={T.fb - T.water} fill={`url(#${diagramId}-water)`} opacity=".62" />
                <path d={`M${T.fl} ${T.water + 1}H${T.fr}`} stroke="#eaf7ff" strokeWidth="3" opacity=".9" />
                {/* 물에 잠긴 펌프 */}
                <rect x={T.pump - 20} y={T.fb - 46} width="40" height="30" rx="7" fill="#353b40" opacity=".85" />
                <rect x={T.pump - 15} y={T.fb - 42} width="7" height="22" rx="3" fill="#5f686e" opacity=".85" />
              </g>
              {/* 플라스틱 반투명감과 빛 반사 */}
              <rect x={T.fl} y={T.ft} width={T.fr - T.fl} height={T.fb - T.ft} rx="14" fill="#fff" opacity=".16" />
              <rect x={T.fl + 12} y={T.ft + 12} width="10" height={T.fb - T.ft - 30} rx="5" fill="#fff" opacity=".7" />
              {/* 뚜껑 */}
              <ellipse cx={T.fl + 62} cy={T.ft - 12} rx="17" ry="5" fill="#3f7fba" />
              <rect x={T.fl + 45} y={T.ft - 12} width="34" height="11" fill={`url(#${diagramId}-cap)`} />
              <ellipse cx={T.fl + 62} cy={T.ft - 1} rx="17" ry="5" fill="#245d93" />
              <ellipse cx={T.fl + 62} cy={T.ft - 12} rx="17" ry="5" fill="#5b9bd5" />
            </g>

            {/* 천장 가운데 생육 카메라 (ESP32-CAM) + 내려다보는 시야 */}
            <path d={`M${CAMERA_IMG.x - 8} ${CAMERA_IMG.y + 14}L560 735H940L${CAMERA_IMG.x + 8} ${CAMERA_IMG.y + 14}Z`} fill="#fff" opacity=".1" />
            <path d={`M${CAMERA_IMG.x - 8} ${CAMERA_IMG.y + 14}L560 735M${CAMERA_IMG.x + 8} ${CAMERA_IMG.y + 14}L940 735`} stroke="#fff" strokeOpacity=".45" strokeWidth="2" strokeDasharray="8 8" fill="none" />
            <g>
              <rect x={CAMERA_IMG.x - 9} y="190" width="18" height="10" rx="2" fill={`url(#${diagramId}-alu)`} stroke="#9aa1a6" />
              <rect x={CAMERA_IMG.x - 24} y="198" width="48" height="30" rx="5" fill="#2e3438" stroke="#1d2124" strokeWidth="1.5" />
              <rect x={CAMERA_IMG.x - 20} y="201" width="40" height="6" rx="2" fill="#4a5258" />
              <circle cx={CAMERA_IMG.x} cy="220" r="10" fill="#15181a" stroke="#6b7378" strokeWidth="2.5" />
              <circle cx={CAMERA_IMG.x} cy="220" r="4.5" fill="#2f5f8a" />
              <circle cx={CAMERA_IMG.x - 2} cy="218" r="1.8" fill="#cfe6fa" />
              <circle cx={CAMERA_IMG.x + 17} cy="204" r="2.2" fill="#e04a4a" />
            </g>

            {/* 실리콘 호스: 물통 속 펌프 → 통 윗면 → 선반 앞 프레임 뒤로 → 트레이 밑 */}
            <path d={`M${T.pump} ${T.fb - 46}V870`} fill="none" stroke="#b7c3ca" strokeWidth="11" strokeLinecap="round" />
            <path d={`M${T.pump} ${T.fb - 46}V870`} fill="none" stroke={water ? '#4aa3da' : '#eef3f6'} strokeWidth="7" strokeLinecap="round" />
            {water && <g className="cube-water-flow">
              <path className="cube-water-stream" d={`M${T.pump} ${T.fb - 46}V870`} fill="none" stroke="#d8f0ff" strokeWidth="3" strokeDasharray="10 14" />
              <rect className="cube-tray-water" x="400" y="842" width="640" height="16" rx="6" fill="#55a8d5" />
              <ellipse className="cube-water-ripple" cx={T.pump} cy="850" rx="22" ry="5" fill="none" stroke="#2f7fb5" strokeWidth="3" />
            </g>}

            {/* 앞쪽 프레임(선반 앞 가로대, 바닥 앞 가로대)을 원래 그림에서 그대로 가져와 물통·호스 위에 덮는다 */}
            {FRONT_RAILS.map((r) => (
              <svg key={r.y} x={r.x} y={r.y} width={r.w} height={r.h} viewBox={`${r.x} ${r.y} ${r.w} ${r.h}`}>
                <image href="/smart-cube-front.webp" x="0" y="0" width="1295" height="1214" />
              </svg>
            ))}
          </svg>

          {/* LED: 꺼지면 안쪽을 어둡게, 켜지면 밝기만큼 분홍빛을 더한다 */}
          {!led && <rect x={CHAMBER.x} y={CHAMBER.y} width={CHAMBER.w} height={CHAMBER.h} fill="#2f3438" style={{ opacity: .38, mixBlendMode: 'multiply' }} />}
          {!led && <rect x={LED_BAR.x} y={LED_BAR.y - 2} width={LED_BAR.w} height="5" rx="2" fill="#c9ced2" />}
          {led && <rect x={CHAMBER.x} y={CHAMBER.y} width={CHAMBER.w} height={CHAMBER.h} fill={`url(#${diagramId}-glow)`} style={{ opacity: .25 + ledLevel * .75 }} />}
          {led && <rect x={LED_BAR.x} y={LED_BAR.y - 2} width={LED_BAR.w} height="5" rx="2" fill="#fff0f7" className="cube-led-bar-glow" style={{ opacity: .35 + ledLevel * .65 }} />}

          {/* 팬 (그림의 팬 위에 겹침). 가동하면 날개가 돈다 */}
          {[{ ...CIRCULATION, active: circulation, speed: a.innerFanSpeed }, { ...EXHAUST, active: exhaust, speed: a.outerFanSpeed }].map((fan, i) => <g key={i} transform={`translate(${fan.x} ${fan.y})`}>
            <rect x="-21" y="-21" width="42" height="42" rx="6" fill="none" stroke={fan.active ? '#7dd3a8' : 'none'} strokeWidth="2" />
            {fan.active && <g className="cube-fan-rotor" style={{ animationDuration: `${2.4 / fan.speed}s` }}>
              <path d="M0-3C-13-17-18-1-3 0C-17 13-1 18 0 3C13 17 18 1 3 0C17-13 1-18 0-3Z" fill="#cfeee0" style={{ opacity: .85 }} />
            </g>}
          </g>)}
          {circulation && <g className="cube-airflow" markerEnd={`url(#${diagramId}-air)`}><path d={`M${CIRCULATION.x + 24} ${CIRCULATION.y - 10}C${CIRCULATION.x + 44} ${CIRCULATION.y - 16} ${CIRCULATION.x + 54} ${CIRCULATION.y} ${CIRCULATION.x + 46} ${CIRCULATION.y + 16}`} /></g>}
          {exhaust && <g className="cube-airflow cube-exhaust-flow" markerEnd={`url(#${diagramId}-air)`}><path d={`M${EXHAUST.x + 6} ${EXHAUST.y - 24}C${EXHAUST.x + 14} ${EXHAUST.y - 44} ${EXHAUST.x + 30} ${EXHAUST.y - 50} ${EXHAUST.x + 44} ${EXHAUST.y - 52}`} /></g>}

          {/* 센서 표시: 조도 센서(위쪽), 토양 수분 센서(가운데 화분) */}
          <g transform={`translate(${LUX.x} ${LUX.y})`}>
            <rect x="-7" y="-6" width="14" height="12" rx="2.5" fill="#fff4cf" stroke="#ad9146" strokeWidth="1.3" />
            <circle r="3" fill="#efc962" stroke="#ad9146" strokeWidth=".8" />
          </g>
          <g transform={`translate(${SOIL.x} ${SOIL.y}) rotate(10)`}>
            <path d="M-2 3v11M2 3v11" stroke="#8293a0" strokeWidth="2" strokeLinecap="round" />
            <rect x="-6" y="-8" width="12" height="12" rx="2" fill="#cde3da" stroke="#386e5a" strokeWidth="1.2" />
          </g>


          {/* 장치 강조: 고른 장치 둘레를 밝은 녹색으로 감싼다 */}
          {highlight === 'exhaust' && <rect x={EXHAUST.x - 27} y={EXHAUST.y - 27} width="54" height="54" rx="10" className="cube-hl" />}
          {highlight === 'circulation' && <rect x={CIRCULATION.x - 27} y={CIRCULATION.y - 27} width="54" height="54" rx="10" className="cube-hl" />}
          {highlight === 'led' && <rect x={LED_BAR.x - 6} y={LED_BAR.y - 8} width={LED_BAR.w + 12} height="16" rx="8" className="cube-hl" />}
          {highlight === 'water' && <rect x={TANK.x - 8} y={TANK.y - 64} width={TANK.w + 16} height={TANK.h + 72} rx="10" className="cube-hl" />}
          {ledBadge && led && (
            <g>
              <rect x={LED_BAR.x + LED_BAR.w / 2 - 34} y={LED_BAR.y + 7} width="68" height="18" rx="9" fill="#1e2e13" opacity=".82" />
              <text x={LED_BAR.x + LED_BAR.w / 2} y={LED_BAR.y + 20} textAnchor="middle" fontSize="11" fontWeight="700" fill="#fff">밝기 {a.ledIntensity}%</text>
            </g>
          )}

          {/* 탐험 모드: 부품마다 누를 수 있는 말풍선 */}
          {explore && (Object.keys(PART_SPOTS) as CubePart[]).map((part) => {
            const spot = PART_SPOTS[part];
            const text = explore.bubbles[part];
            const w = Math.max(44, text.length * 9 + 18);
            const on = explore.picked === part;
            const alert = explore.alert?.[part];
            return (
              <g key={part} className={`cube-bubble ${on ? 'is-on' : ''} ${alert ? 'is-alert' : ''}`} role="button" tabIndex={0}
                aria-label={`${text} 자세히 보기`} aria-pressed={on}
                onClick={() => explore.onPick(part)}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); explore.onPick(part); } }}>
                {(spot.ax !== spot.x || spot.ay !== spot.y) && <path d={`M${spot.x} ${spot.y}L${spot.ax} ${spot.ay}`} className="cube-bubble-line" />}
                {(spot.ax !== spot.x || spot.ay !== spot.y) && <circle cx={spot.ax} cy={spot.ay} r="3" className="cube-bubble-dot" />}
                {alert && !on && <rect x={spot.x - w / 2 - 4} y={spot.y - 15} width={w + 8} height="30" rx="15" className="cube-bubble-ring" />}
                <rect x={spot.x - w / 2} y={spot.y - 11} width={w} height="22" rx="11" className="cube-bubble-body" />
                <text x={spot.x} y={spot.y + 4} textAnchor="middle" className="cube-bubble-text">{text}</text>
              </g>
            );
          })}

          {/* 라벨 */}
          {!bare && <g className="cube-flat-labels" fill="#49665a" fontSize="13">
            <text x="8" y="50">조도 센서</text><text x="8" y="66" fontWeight="600">{luxText}</text>
            <path d={`M78 60H${LUX.x - 9}`} stroke="#ad9146" strokeWidth="1.1" fill="none" />
            <text x="8" y={CIRCULATION.y - 4}>순환팬</text><text x="8" y={CIRCULATION.y + 12} fontSize="11">뒷면</text>
            <path d={`M56 ${CIRCULATION.y}H${CIRCULATION.x - 22}`} stroke="#9eb4a6" fill="none" />
            <text x="612" y="26">카메라</text><text x="612" y="42" fontSize="11">천장 가운데</text>
            <path d={`M${CAMERA.x} ${CAMERA.y - 9}V20H606`} stroke="#9eb4a6" fill="none" />
            <text x="612" y="76">LED 조명</text>
            <path d={`M${LED_BAR.x + LED_BAR.w} ${LED_BAR.y}L606 72`} stroke="#9eb4a6" fill="none" />
            <text x="612" y="112">배기팬</text><text x="612" y="128" fontSize="11">뒷면</text>
            <path d={`M${EXHAUST.x + 22} ${EXHAUST.y}H606`} stroke="#9eb4a6" fill="none" />
            <text x="612" y={TANK.y + 8}>물통·급수</text><text x="612" y={TANK.y + 24} fontSize="11">화분 밑으로 저면관수</text>
            <path d={`M${TANK.x + TANK.w} ${TANK.y + 12}H606`} stroke="#9eb4a6" fill="none" />
            <path d={`M${SOIL.x + 7} ${SOIL.y}L586 332H606`} stroke="#658d78" strokeWidth="1.1" fill="none" />
            <text x="612" y="328">토양 수분</text><text x="612" y="344" fontWeight="600">{soilText}</text>
            <text x="360" y="474" textAnchor="middle">{plantLabel}</text>
          </g>}
        </svg>
        {!live && <span className="cube-activity-disconnected">통신 끊김 · 상태 확인 불가</span>}
      </div>
      {!bare && <div className="cube-activity-status" aria-label="장치 동작 상태">
        {states.map(({ key, label, icon: Icon, active, value, speed }) => (
          <div key={key} className={`cube-activity-item cube-activity-${key} ${active ? 'is-active' : ''}`}>
            <Icon aria-hidden="true" className={active && speed > 0 ? 'cube-status-fan' : ''} style={speed > 0 ? { animationDuration: `${2.4 / speed}s` } : undefined} />
            <span><span className="cube-activity-label">{label}</span><strong>{live ? value : '확인 불가'}</strong></span>
            <span className="cube-activity-dot" aria-hidden="true" />
          </div>
        ))}
      </div>}
    </>
  );
}
