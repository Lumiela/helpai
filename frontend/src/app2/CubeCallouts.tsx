import React, { useState } from 'react';
import { CubeScene, type CubeSceneState } from './CubeScene';

// 2026-11-24 상태 화면 큐브: "제품 사진 + 주석 콜아웃 + 돋보기 렌즈".
// 좌표는 모두 이 상자 기준 단위(가로 100 × 세로 120)로 둔다. 상자가 같은 비율로 커지고 줄어서
// 화면 폭이 달라도 점이 부품 위에 그대로 붙어 있다. 큐브 그림 좌표(1295×1214 원본)에서 변환한다.

const BOX_H = 120;
const CUBE = { x: 24, y: 15.6, w: 56 }; // 그림이 놓이는 자리(단위)
const VIEW = { x: 219, y: 22, w: 1012 }; // CubeScene 의 viewBox 와 같아야 한다
const K = CUBE.w / VIEW.w;
const at = (X: number, Y: number) => ({ x: CUBE.x + (X - VIEW.x) * K, y: CUBE.y + (Y - VIEW.y) * K });

export type CalloutKey = 'light' | 'camera' | 'soil' | 'air' | 'water';
export interface CalloutItem {
  key: CalloutKey;
  name: string;
  value: string;
  warn?: boolean; // 범위를 벗어남 → 주황
  onPick: () => void;
}

// 점 위치(원본 그림 좌표), 꺾은선, 라벨 자리
const PLACES: Record<CalloutKey, { dot: { x: number; y: number }; path: (d: { x: number; y: number }) => string; label: React.CSSProperties; align: 'left' | 'right' | 'center' }> = {
  camera: { dot: at(750, 214), path: (d) => `M${d.x} ${d.y}V7H31`, label: { left: '4%', top: '1%' }, align: 'left' },
  light: { dot: at(1010, 184), path: (d) => `M${d.x} ${d.y}V14`, label: { right: '2%', top: '1%' }, align: 'right' },
  air: { dot: at(1050, 430), path: (d) => `M${d.x} ${d.y}H90V${BOX_H * 0.735}`, label: { right: '2%', top: '74%' }, align: 'right' },
  soil: { dot: at(822, 772), path: (d) => `M${d.x} ${d.y}V86H52V${BOX_H * 0.735}`, label: { left: '36%', top: '74%', width: '32%' }, align: 'center' },
  water: { dot: at(700, 1060), path: (d) => `M${d.x} ${d.y}V84H14V${BOX_H * 0.735}`, label: { left: '3%', top: '74%' }, align: 'left' },
};
const ORDER: CalloutKey[] = ['camera', 'light', 'air', 'soil', 'water'];

// 렌즈: 왼쪽 가운데. 큐브 그림 속 식물 한 곳을 크게 보여 준다(카메라 사진이 생기면 그걸로 바꾼다)
const LENS = { cx: 16, cy: 45, r: 15 };
const LENS_TARGET = at(540, 560);
const LENS_SRC = { x: 420, y: 430, size: 250 };

export const CubeCallouts: React.FC<{
  scene: CubeSceneState;
  sceneLabel: string;
  items: CalloutItem[];
  lens: { name: string; value: string; alert?: boolean }; // alert: 식물에 이상 → 빨간 불이 깜빡인다
  offline: string | null; // "연결 끊김 · 마지막 수신 HH:MM"
}> = ({ scene, sceneLabel, items, lens, offline }) => {
  const [picked, setPicked] = useState<CalloutKey | null>(null);
  const byKey = new Map<CalloutKey, CalloutItem>(items.map((i) => [i.key, i]));
  return (
    <div className={`cx ${offline ? 'is-off' : ''}`}>
      <div className="cx-cube" style={{ left: `${CUBE.x}%`, top: `${(CUBE.y / BOX_H) * 100}%`, width: `${CUBE.w}%` }}>
        <span className="cx-shadow" aria-hidden="true" />
        <CubeScene s={scene} label={sceneLabel} />
      </div>

      {/* 가는 선: 아래에 옅은 그림자 선을 깔아 밝은 바탕에서도 보이게 */}
      <svg className="cx-lines" viewBox={`0 0 100 ${BOX_H}`} aria-hidden="true">
        {ORDER.map((k, i) => {
          const p = PLACES[k];
          const it = byKey.get(k);
          if (!it) return null;
          const d = p.path(p.dot);
          return (
            <g key={k} className={`cx-line ${picked === k ? 'is-picked' : ''} ${it.warn ? 'is-warn' : ''}`} style={{ animationDelay: `${0.1 + i * 0.08}s` }}>
              <path d={d} className="cx-line-under" pathLength={1} />
              <path d={d} className="cx-line-top" pathLength={1} />
            </g>
          );
        })}
        <g className="cx-line" style={{ animationDelay: '0.5s' }}>
          <path d={`M${LENS.cx + LENS.r} ${LENS.cy}L${LENS_TARGET.x} ${LENS_TARGET.y}`} className="cx-line-under" pathLength={1} />
          <path d={`M${LENS.cx + LENS.r} ${LENS.cy}L${LENS_TARGET.x} ${LENS_TARGET.y}`} className="cx-line-top" pathLength={1} />
        </g>
      </svg>

      {/* 점(누르면 그 항목 화면으로) */}
      {ORDER.map((k, i) => {
        const it = byKey.get(k);
        if (!it) return null;
        const p = PLACES[k];
        return (
          <button key={k} type="button" className={`cx-dot ${picked === k ? 'is-picked' : ''} ${it.warn ? 'is-warn' : ''}`}
            style={{ left: `${p.dot.x}%`, top: `${(p.dot.y / BOX_H) * 100}%`, animationDelay: `${i * 0.08}s` }}
            aria-label={`${it.name} ${it.value}. 자세히 보기`}
            onClick={() => { setPicked(k); window.setTimeout(it.onPick, 180); }}>
            <span aria-hidden="true" />
          </button>
        );
      })}
      <span className={`cx-dot is-static ${lens.alert && !offline ? 'is-alert' : ''}`} style={{ left: `${LENS_TARGET.x}%`, top: `${(LENS_TARGET.y / BOX_H) * 100}%`, animationDelay: '0.45s' }} aria-hidden="true"><span /></span>

      {/* 라벨: 글자만 */}
      {ORDER.map((k, i) => {
        const it = byKey.get(k);
        if (!it) return null;
        const p = PLACES[k];
        return (
          <div key={k} className={`cx-label is-${p.align} ${it.warn ? 'is-warn' : ''} ${picked === k ? 'is-picked' : ''}`} style={{ ...p.label, animationDelay: `${0.35 + i * 0.07}s` }} aria-hidden="true">
            <small>{it.name}</small>
            <b>{it.value}</b>
          </div>
        );
      })}

      {/* 돋보기 렌즈 */}
      <div className={`cx-lens ${lens.alert && !offline ? 'is-alert' : ''}`} style={{ left: `${LENS.cx - LENS.r}%`, top: `${((LENS.cy - LENS.r) / BOX_H) * 100}%`, width: `${LENS.r * 2}%` }} aria-hidden="true">
        {lens.alert && !offline && <span className="cx-lens-lamp" />}
        <svg viewBox={`${LENS_SRC.x} ${LENS_SRC.y} ${LENS_SRC.size} ${LENS_SRC.size}`} preserveAspectRatio="xMidYMid slice">
          <image href="/smart-cube-front.webp" x="0" y="0" width="1295" height="1214" />
        </svg>
      </div>
      <div className={`cx-label is-left cx-lens-label ${lens.alert && !offline ? 'is-alert' : ''}`} role={lens.alert && !offline ? 'status' : undefined} style={{ left: '3%', top: `${((LENS.cy + LENS.r) / BOX_H) * 100 + 3}%`, animationDelay: '0.6s' }}>
        <small>{lens.name}</small>
        <b>{lens.value}</b>
      </div>

      {offline && <p className="cx-off" role="status">{offline}</p>}
    </div>
  );
};
