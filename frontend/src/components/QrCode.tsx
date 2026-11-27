import React, { useMemo } from 'react';
import qrcode from 'qrcode-generator';

// QR 코드 SVG (2026-11-16, 설정 보드 장치 등록의 등록 코드 발급 화면).
// 휴대폰 카메라가 읽기 쉽게 화면 테마와 관계없이 흰 바탕에 검은 칸, 둘레에 표준 여백(4칸)을 둔다.
const QUIET = 4;

interface QrCodeProps {
  text: string;
  size?: number; // 화면 픽셀
  label: string; // 화면 읽기 프로그램용 설명
}

export const QrCode: React.FC<QrCodeProps> = ({ text, size = 200, label }) => {
  const { count, path } = useMemo(() => {
    const qr = qrcode(0, 'M'); // 형 번호 자동, 오류 정정 M(약 15%)
    qr.addData(text);
    qr.make();
    const n = qr.getModuleCount();
    let d = '';
    for (let r = 0; r < n; r += 1) {
      for (let c = 0; c < n; c += 1) {
        if (qr.isDark(r, c)) d += `M${c + QUIET} ${r + QUIET}h1v1h-1z`;
      }
    }
    return { count: n, path: d };
  }, [text]);
  const span = count + QUIET * 2;
  return (
    <svg
      viewBox={`0 0 ${span} ${span}`}
      width={size}
      height={size}
      role="img"
      aria-label={label}
      shapeRendering="crispEdges"
      className="shrink-0 rounded"
    >
      <rect width={span} height={span} fill="#ffffff" />
      <path d={path} fill="#000000" />
    </svg>
  );
};
