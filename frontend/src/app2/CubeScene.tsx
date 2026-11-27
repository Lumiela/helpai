import React from 'react';
import './cubeScene.css';

// 2026-11-24 큐브 시뮬레이션 그림(모바일 앱 상태 화면).
// 바탕은 /smart-cube-front.webp(1295×1214) 그림체 그대로. 모든 겹침은 그림 좌표로 그린다.
//   - 왼쪽 제어함은 잘라 내고, 그 자리에 실제 옆면 띠를 늘려 붙여 큐브 옆면이 이어지게 한다.
//   - 트레이 바로 아래 칸에 물통 + 물에 잠긴 펌프 + 트레이 밑까지 올라가는 호스(저면관수).
//   - 조명·팬·펌프 모습은 장치가 "보고한" 상태(props)만 따른다. 누른 순간 바뀌지 않는다.
// src/components/CubeActivity.tsx 의 좌표와 그림 방식을 그대로 가져왔다.

const IMG = '/smart-cube-front.webp';
export const VIEW = { x: 219, y: 22, w: 1012, h: 1192 }; // 제어함을 뺀 큐브 부분
const SIDE = { x: 219, w: 59, srcTop: 134, srcBottom: 212, top: 206, bottom: 852 };
const CHAMBER = { x: 232, y: 150, w: 1178 - 232, h: 932 - 150 }; // 왼쪽 옆면까지 포함
const LED_BAR = { x: 330, y: 184, w: 1135 - 330 };
const EXHAUST = { x: 944, y: 283 };
const CIRCULATION = { x: 396, y: 539 };
const CAMERA = { x: 750, y: 214 };
// 물통(아래 칸). 앞면 왼·오른·위·아래, 뚜껑 뒤쪽(원근으로 조금 좁고 높다), 물 높이, 펌프 x
const T = { l: 506, r: 940, t: 1006, b: 1150, bl: 522, br: 930, bt: 984, water: 1046, pump: 866 };
const HOSE_TOP = 874; // 트레이 밑
const FRONT_RAILS = [{ x: 293, y: 921, w: 932, h: 62 }, { x: 293, y: 1128, w: 932, h: 64 }];
// 화분 밑동(젖은 흙 표시 자리)
const POTS = [{ x: 488, y: 790 }, { x: 650, y: 792 }, { x: 822, y: 790 }, { x: 952, y: 768 }];

export interface CubeSceneState {
  live: boolean;
  led: number; // 0~100, 0이면 꺼짐
  inner: number; // 순환팬 0~3
  outer: number; // 배기팬 0~3
  pump: boolean;
}

// box: 다른 SVG 안에 넣을 때 자리(그 SVG 좌표). 없으면 부모 폭에 맞춰 늘어난다.
export const CubeScene: React.FC<{ s: CubeSceneState; label: string; box?: { x: number; y: number; w: number; h: number } }> = ({ s, label, box }) => {
  const id = React.useId().replace(/:/g, '');
  const led = s.live && s.led > 0;
  const level = led ? s.led / 100 : 0;
  const pump = s.live && s.pump;
  return (
    <svg className={`sc ${s.live ? '' : 'is-offline'} ${pump ? 'is-pumping' : ''} ${led ? 'is-lit' : 'is-dark'}`}
      viewBox={`${VIEW.x} ${VIEW.y} ${VIEW.w} ${VIEW.h}`} role="img" aria-label={label}
      {...(box ? { x: box.x, y: box.y, width: box.w, height: box.h, style: { width: box.w, height: box.h } } : {})}>
      <defs>
        <radialGradient id={`${id}-glow`} cx=".5" cy="0" r="1">
          <stop offset="0" stopColor="#ff7fb5" stopOpacity=".55" />
          <stop offset=".7" stopColor="#ffb3d2" stopOpacity=".14" />
          <stop offset="1" stopColor="#ffb3d2" stopOpacity="0" />
        </radialGradient>
        <linearGradient id={`${id}-fade`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#fff" stopOpacity="0" /><stop offset="1" stopColor="#fff" stopOpacity=".28" />
        </linearGradient>
        <linearGradient id={`${id}-water`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#9fd2ef" /><stop offset="1" stopColor="#3f8fc6" />
        </linearGradient>
        {/* 반투명 플라스틱 통: 가장자리는 진하고 가운데는 비친다 */}
        <linearGradient id={`${id}-tf`} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#dfe6ea" stopOpacity=".95" /><stop offset=".08" stopColor="#f7fafb" stopOpacity=".55" />
          <stop offset=".9" stopColor="#f2f6f8" stopOpacity=".45" /><stop offset="1" stopColor="#d3dce1" stopOpacity=".95" />
        </linearGradient>
        <linearGradient id={`${id}-lid`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#f4f7f8" /><stop offset="1" stopColor="#d9e0e4" />
        </linearGradient>
        <linearGradient id={`${id}-pump`} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#3a4248" /><stop offset=".45" stopColor="#5a646b" /><stop offset="1" stopColor="#2b3136" />
        </linearGradient>
        {/* 팬이 돌 때 날개가 번져 보이는 원 */}
        <radialGradient id={`${id}-blur`} cx=".5" cy=".5" r=".5">
          <stop offset="0" stopColor="#dfeee8" stopOpacity=".1" /><stop offset=".55" stopColor="#dfeee8" stopOpacity=".5" />
          <stop offset=".85" stopColor="#dfeee8" stopOpacity=".25" /><stop offset="1" stopColor="#dfeee8" stopOpacity="0" />
        </radialGradient>
        <clipPath id={`${id}-tclip`}><rect x={T.l + 3} y={T.t + 3} width={T.r - T.l - 6} height={T.b - T.t - 6} rx="10" /></clipPath>
      </defs>

      <image href={IMG} x="0" y="0" width="1295" height="1214" />

      {/* 제어함 자리: 그 위에 보이는 실제 옆면 띠를 세로로 늘려 덮는다 */}
      <svg x={SIDE.x} y={SIDE.top} width={SIDE.w} height={SIDE.bottom - SIDE.top} viewBox={`${SIDE.x} ${SIDE.srcTop} ${SIDE.w} ${SIDE.srcBottom - SIDE.srcTop}`} preserveAspectRatio="none">
        <image href={IMG} x="0" y="0" width="1295" height="1214" />
      </svg>
      <rect x={SIDE.x + 12} y={SIDE.top} width={SIDE.w - 20} height={SIDE.bottom - SIDE.top} fill={`url(#${id}-fade)`} />

      {/* 물통: 바닥 그림자 → 통 안(물·펌프·속 호스) → 반투명 앞면 → 뚜껑 */}
      <ellipse cx={(T.l + T.r) / 2} cy={T.b - 4} rx={(T.r - T.l) / 2 + 14} ry="12" fill="#5b646a" opacity=".18" />
      <g clipPath={`url(#${id}-tclip)`}>
        <rect x={T.l} y={T.t} width={T.r - T.l} height={T.b - T.t} fill="#eef4f7" opacity=".55" />
        {/* 물: 펌프가 돌면 수위가 조금 내려간다(연출, 숫자 없음) */}
        <g className="sc-tank-water">
          <rect x={T.l} y={T.water} width={T.r - T.l} height={T.b - T.water + 40} fill={`url(#${id}-water)`} opacity=".85" />
          <rect x={T.l} y={T.water - 5} width={T.r - T.l} height="9" fill="#cfeaf8" opacity=".9" />
          <path d={`M${T.l} ${T.water + 4}H${T.r}`} stroke="#fff" strokeWidth="2" opacity=".8" />
        </g>
        {/* 속 호스(펌프 → 뚜껑) */}
        <path d={`M${T.pump} ${T.b - 60}V${T.t}`} fill="none" stroke="#8b989f" strokeWidth="13" />
        <path className="sc-hose" d={`M${T.pump} ${T.b - 60}V${T.t}`} fill="none" strokeWidth="8" />
        {/* 물에 잠긴 펌프 */}
        <g opacity=".92">
          <rect x={T.pump - 30} y={T.b - 62} width="60" height="50" rx="10" fill={`url(#${id}-pump)`} stroke="#22272b" strokeWidth="2" />
          {[0, 1, 2, 3].map((i) => <rect key={i} x={T.pump - 21 + i * 12} y={T.b - 36} width="5" height="18" rx="2" fill="#1d2226" />)}
          <rect x={T.pump - 9} y={T.b - 70} width="18" height="10" rx="3" fill="#4a535a" stroke="#22272b" strokeWidth="1.5" />
          <circle className="sc-pump-led" cx={T.pump + 19} cy={T.b - 51} r="4" fill={pump ? '#7be08f' : '#7d878d'} />
        </g>
        {/* 물 올릴 때 펌프 둘레 거품 */}
        <g className="sc-bubbles">
          {[[-42, 0], [-30, 14], [38, 6], [46, -10], [-50, -14]].map(([dx, dy], i) => (
            <circle key={i} cx={T.pump + dx} cy={T.b - 50 + dy} r={i % 2 ? 3 : 4.5} fill="none" stroke="#fff" strokeWidth="1.6" style={{ animationDelay: `${i * 0.35}s` }} />
          ))}
        </g>
      </g>
      {/* 반투명 앞면: 테두리, 왼쪽 빛 반사, 오른쪽 눈금 */}
      <rect x={T.l} y={T.t} width={T.r - T.l} height={T.b - T.t} rx="12" fill={`url(#${id}-tf)`} stroke="#8f9ba2" strokeWidth="2.5" />
      <rect x={T.l + 16} y={T.t + 18} width="9" height={T.b - T.t - 40} rx="4.5" fill="#fff" opacity=".75" />
      <rect x={T.l + 31} y={T.t + 18} width="4" height={T.b - T.t - 70} rx="2" fill="#fff" opacity=".5" />
      {[0, 1, 2, 3].map((i) => <path key={i} d={`M${T.r - 30} ${T.t + 28 + i * 22}h${i % 2 ? 9 : 16}`} stroke="#7f8b92" strokeWidth="2" opacity=".55" />)}
      {/* 뚜껑: 윗면(원근) + 앞 테 + 호스 구멍 */}
      <path d={`M${T.l - 4} ${T.t + 2}L${T.bl} ${T.bt}H${T.br}L${T.r + 4} ${T.t + 2}Z`} fill={`url(#${id}-lid)`} stroke="#8f9ba2" strokeWidth="2" strokeLinejoin="round" />
      <rect x={T.l - 6} y={T.t - 2} width={T.r - T.l + 12} height="14" rx="5" fill="#e4e9ec" stroke="#8f9ba2" strokeWidth="2" />
      <ellipse cx={T.pump} cy={T.bt + 11} rx="13" ry="5" fill="#59646b" />

      {/* 바깥 호스: 뚜껑 → 트레이 밑(가운데 가로대 뒤를 지나간다) */}
      <path d={`M${T.pump} ${T.bt + 10}V${HOSE_TOP}`} fill="none" stroke="#8b989f" strokeWidth="14" strokeLinecap="round" />
      <path className="sc-hose" d={`M${T.pump} ${T.bt + 10}V${HOSE_TOP}`} fill="none" strokeWidth="9" strokeLinecap="round" />
      <path className="sc-stream" d={`M${T.pump} ${T.bt + 10}V${HOSE_TOP}`} fill="none" stroke="#e3f4ff" strokeWidth="3.5" strokeDasharray="10 14" />
      <path d={`M${T.pump} ${T.bt + 10}V${HOSE_TOP}`} fill="none" stroke="#fff" strokeWidth="2" opacity=".55" transform="translate(-3 0)" />
      <rect x={T.pump - 13} y={HOSE_TOP - 6} width="26" height="12" rx="3" fill="#4a535a" stroke="#22272b" strokeWidth="1.5" />

      {/* 트레이 밑에 차오르는 물과 젖은 화분 밑동 */}
      <rect className="sc-tray-water" x="404" y="850" width="632" height="9" rx="4.5" fill="#55a8d5" />
      <g className="sc-wet">{POTS.map((p) => <ellipse key={p.x} cx={p.x} cy={p.y} rx="46" ry="9" fill="#3c2a1e" />)}</g>

      {/* 앞쪽 가로대를 원래 그림에서 잘라 와 물통·호스 앞에 덮는다 */}
      {FRONT_RAILS.map((r) => (
        <svg key={r.y} x={r.x} y={r.y} width={r.w} height={r.h} viewBox={`${r.x} ${r.y} ${r.w} ${r.h}`}>
          <image href={IMG} x="0" y="0" width="1295" height="1214" />
        </svg>
      ))}

      {/* 천장 가운데 카메라 */}
      <g>
        <rect x={CAMERA.x - 24} y="198" width="48" height="30" rx="5" fill="#2e3438" stroke="#1d2124" strokeWidth="1.5" />
        <circle cx={CAMERA.x} cy="220" r="10" fill="#15181a" stroke="#6b7378" strokeWidth="2.5" />
        <circle cx={CAMERA.x} cy="220" r="4.5" fill="#2f5f8a" />
        <circle cx={CAMERA.x + 17} cy="204" r="2.2" fill="#e04a4a" />
      </g>

      {/* 조명: 꺼지면 안쪽을 저녁 톤으로, 켜지면 밝기만큼 분홍빛 */}
      <rect className="sc-dark" x={CHAMBER.x} y={CHAMBER.y} width={CHAMBER.w} height={CHAMBER.h} fill="#262b3a" />
      <rect className="sc-glow" x={CHAMBER.x} y={CHAMBER.y} width={CHAMBER.w} height={CHAMBER.h} fill={`url(#${id}-glow)`} style={{ opacity: led ? 0.25 + level * 0.75 : 0 }} />
      <rect className="sc-ledbar" x={LED_BAR.x} y={LED_BAR.y - 4} width={LED_BAR.w} height="10" rx="4" style={{ opacity: led ? 0.4 + level * 0.6 : 1 }} />

      {/* 팬: 원래 그림의 팬은 그대로 두고, 켜졌을 때만 날개가 번지며 도는 모습을 얹는다. 세기만큼 빨리 돈다 */}
      {[{ ...CIRCULATION, speed: s.live ? s.inner : 0, key: 'in' }, { ...EXHAUST, speed: s.live ? s.outer : 0, key: 'out' }].map((f) => (
        <g key={f.key} transform={`translate(${f.x} ${f.y})`} className={`sc-fan ${f.speed > 0 ? 'is-on' : ''}`}>
          <circle r="38" fill={`url(#${id}-blur)`} className="sc-fanblur" />
          <g className="sc-rotor is-on" style={{ animationDuration: `${1.2 / Math.max(1, f.speed)}s` }}>
            <circle r="40" fill="transparent" />
            {[0, 120, 240].map((deg) => (
              <path key={deg} transform={`rotate(${deg})`} d="M0 0C6-10 18-26 30-24C34-14 22 2 0 0Z" fill="#16302a" opacity=".55" />
            ))}
            <circle r="9" fill="#1f2b27" />
          </g>
        </g>
      ))}
      {/* 배기팬 바람: 팬 둘레로 흘러나가는 옅은 바람 줄 */}
      {s.live && s.outer > 0 && (
        <g className="sc-air" transform={`translate(${EXHAUST.x} ${EXHAUST.y})`}>
          <path d="M58-26C86-40 108-36 128-22" />
          <path d="M62 2C92-4 116 2 138 16" />
          <path d="M56 28C82 36 100 50 112 66" />
        </g>
      )}
    </svg>
  );
};
