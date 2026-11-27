// 모바일 앱의 화분 목록 (데모). 2026-11-24
//
// 센서·제어는 큐브 단위라서 화분마다 따로 재거나 조작할 수 없다. 화분 고르기는 "어떤 식물을 살펴볼지" 고르는 탐색 기능이다.
// 이름과 사진은 데모다: 사진은 큐브 그림(public/smart-cube-front.webp, 1295×1214)에서 그 화분 자리를 잘라 쓴다.

export const CUBE_IMAGE = { src: '/smart-cube-front.webp', w: 1295, h: 1214 };

export interface Pot {
  id: string;
  name: string; // 화면에 쓰는 이름 (데모)
  note: string; // 한 줄 설명 (데모)
  crop: { x: number; y: number; w: number; h: number }; // 큐브 그림에서 자를 영역(px)
}

export const POTS: Pot[] = [
  { id: 'pot-1', name: '1번 화분', note: '분홍 꽃이 핀 화분', crop: { x: 360, y: 585, w: 250, h: 300 } },
  { id: 'pot-2', name: '2번 화분', note: '노란 꽃이 핀 화분', crop: { x: 530, y: 560, w: 250, h: 300 } },
  { id: 'pot-3', name: '3번 화분', note: '새순이 많은 화분', crop: { x: 700, y: 560, w: 250, h: 300 } },
  { id: 'pot-4', name: '4번 화분', note: '안쪽 노란 꽃 화분', crop: { x: 845, y: 560, w: 250, h: 300 } },
];

// CSS 배경으로 그림의 일부만 보이게 한다(요소의 가로세로비 = crop 비율).
// wide: 홈의 큰 사진. 같은 화분을 가운데 두고 가로로 넓게 자른다.
export function potPhotoStyle(pot: Pot, wide = false): { backgroundImage: string; backgroundSize: string; backgroundPosition: string; aspectRatio: string } {
  const c = pot.crop;
  const { x, y, w, h } = wide
    ? { x: Math.max(0, Math.min(CUBE_IMAGE.w - 330, c.x + c.w / 2 - 165)), y: c.y - 10, w: 330, h: 260 }
    : c;
  return {
    backgroundImage: `url(${CUBE_IMAGE.src})`,
    backgroundSize: `${(CUBE_IMAGE.w / w) * 100}% auto`,
    backgroundPosition: `${(x / (CUBE_IMAGE.w - w)) * 100}% ${(y / (CUBE_IMAGE.h - h)) * 100}%`,
    aspectRatio: `${w} / ${h}`,
  };
}
