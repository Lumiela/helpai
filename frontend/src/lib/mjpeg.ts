// MJPEG(multipart/x-mixed-replace) 파서 (2026-09-29, 카메라 스트리밍).
//
//   브라우저 <img> 는 Authorization 헤더를 못 붙이므로 fetch 로 스트림을 읽어 경계마다 JPEG 를 잘라 Blob URL 로 그린다.
//   api 는 "--<boundary>\r\nContent-Type: image/jpeg\r\nContent-Length: N\r\n...\r\n\r\n<N바이트>\r\n" 을 반복한다.
//   순수 파서(push → 프레임 배열)만 두어 node:test 로 검사한다.

export function boundaryFromContentType(contentType: string | null): string | null {
  if (!contentType) return null;
  const m = /boundary="?([^";]+)"?/i.exec(contentType);
  return m ? m[1] : null;
}

// 2026-09-30: api 0.6.1 은 Content-Type 을 application/octet-stream 으로 보내고 경계를 X-Mjpeg-Boundary 헤더에 싣는다
// (iOS WebKit 이 multipart/x-mixed-replace 를 fetch 로 못 읽는 문제). 옛 api(content-type 안의 boundary)도 그대로 받는다.
export function boundaryFromHeaders(get: (name: string) => string | null): string | null {
  const explicit = (get('x-mjpeg-boundary') ?? '').trim();
  if (explicit) return explicit;
  return boundaryFromContentType(get('content-type'));
}

function indexOf(buf: Uint8Array, pat: Uint8Array, from = 0): number {
  outer: for (let i = from; i <= buf.length - pat.length; i++) {
    for (let j = 0; j < pat.length; j++) if (buf[i + j] !== pat[j]) continue outer;
    return i;
  }
  return -1;
}

function concat(a: Uint8Array, b: Uint8Array): Uint8Array {
  if (a.length === 0) return b;
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

export interface MjpegPart {
  headers: Record<string, string>;
  body: Uint8Array;
}

export class MjpegParser {
  private buf = new Uint8Array(0);
  private readonly delim: Uint8Array;
  private readonly crlfcrlf = new Uint8Array([13, 10, 13, 10]);

  constructor(boundary: string) {
    this.delim = new TextEncoder().encode(`--${boundary}`);
  }

  // 새 조각을 넣고, 완성된 파트(헤더 + 본문)를 전부 돌려준다. Content-Length 가 없는 파트는 다음 경계까지를 본문으로 본다.
  push(chunk: Uint8Array): MjpegPart[] {
    this.buf = concat(this.buf, chunk);
    const out: MjpegPart[] = [];
    for (;;) {
      const start = indexOf(this.buf, this.delim);
      if (start < 0) {
        // 경계가 아직 없으면 마지막 몇 바이트만 남기고 버린다(경계가 조각 사이에 걸칠 수 있음)
        if (this.buf.length > this.delim.length) this.buf = this.buf.slice(this.buf.length - this.delim.length);
        return out;
      }
      const headEnd = indexOf(this.buf, this.crlfcrlf, start + this.delim.length);
      if (headEnd < 0) return out;
      const headText = new TextDecoder().decode(this.buf.slice(start + this.delim.length, headEnd));
      const headers: Record<string, string> = {};
      for (const line of headText.split('\r\n')) {
        const i = line.indexOf(':');
        if (i > 0) headers[line.slice(0, i).trim().toLowerCase()] = line.slice(i + 1).trim();
      }
      const bodyStart = headEnd + 4;
      const len = headers['content-length'] !== undefined ? Number.parseInt(headers['content-length'], 10) : NaN;
      let bodyEnd: number;
      let next: number;
      if (Number.isFinite(len) && len >= 0) {
        if (this.buf.length < bodyStart + len) return out;
        bodyEnd = bodyStart + len;
        next = bodyEnd;
      } else {
        const nb = indexOf(this.buf, this.delim, bodyStart);
        if (nb < 0) return out;
        bodyEnd = nb;
        next = nb;
      }
      out.push({ headers, body: this.buf.slice(bodyStart, bodyEnd) });
      this.buf = this.buf.slice(next);
    }
  }
}
