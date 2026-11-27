import assert from 'node:assert/strict';
import { test } from 'node:test';
import { boundaryFromContentType, boundaryFromHeaders, MjpegParser } from './mjpeg';

const enc = (s: string) => new TextEncoder().encode(s);
const jpeg = new Uint8Array([0xff, 0xd8, 1, 2, 3, 0xff, 0xd9]);

function part(boundary: string, body: Uint8Array, type = 'image/jpeg'): Uint8Array {
  const head = enc(`--${boundary}\r\nContent-Type: ${type}\r\nContent-Length: ${body.length}\r\nX-Frame-Seq: 7\r\n\r\n`);
  const out = new Uint8Array(head.length + body.length + 2);
  out.set(head, 0);
  out.set(body, head.length);
  out.set(enc('\r\n'), head.length + body.length);
  return out;
}

test('content-type 에서 boundary 를 읽는다', () => {
  assert.equal(boundaryFromContentType('multipart/x-mixed-replace; boundary=smartcubeframe'), 'smartcubeframe');
  assert.equal(boundaryFromContentType('multipart/x-mixed-replace; boundary="abc"'), 'abc');
  assert.equal(boundaryFromContentType('image/jpeg'), null);
  assert.equal(boundaryFromContentType(null), null);
});

test('한 덩어리에 파트 두 개', () => {
  const p = new MjpegParser('b');
  const data = new Uint8Array([...part('b', jpeg), ...part('b', jpeg)]);
  const parts = p.push(data);
  assert.equal(parts.length, 2);
  assert.deepEqual([...parts[0].body], [...jpeg]);
  assert.equal(parts[0].headers['content-type'], 'image/jpeg');
  assert.equal(parts[1].headers['x-frame-seq'], '7');
});

test('조각이 경계·헤더·본문 중간에서 끊겨도 이어 붙인다', () => {
  const p = new MjpegParser('smartcubeframe');
  const data = part('smartcubeframe', jpeg);
  const got = [];
  for (let i = 0; i < data.length; i += 5) got.push(...p.push(data.slice(i, i + 5)));
  assert.equal(got.length, 1);
  assert.deepEqual([...got[0].body], [...jpeg]);
});

test('빈 keepalive 파트(text/plain, 길이 0)도 파트로 나온다', () => {
  const p = new MjpegParser('b');
  const parts = p.push(part('b', new Uint8Array(0), 'text/plain'));
  assert.equal(parts.length, 1);
  assert.equal(parts[0].body.length, 0);
  assert.equal(parts[0].headers['content-type'], 'text/plain');
});

test('경계는 X-Mjpeg-Boundary 헤더를 우선하고, 없으면 content-type 에서 읽는다 (iOS WebKit 대응)', () => {
  const h = (m: Record<string, string>) => (name: string) => m[name] ?? null;
  assert.equal(boundaryFromHeaders(h({ 'content-type': 'application/octet-stream', 'x-mjpeg-boundary': 'smartcubeframe' })), 'smartcubeframe');
  assert.equal(boundaryFromHeaders(h({ 'content-type': 'multipart/x-mixed-replace; boundary=old' })), 'old');
  assert.equal(boundaryFromHeaders(h({ 'content-type': 'application/octet-stream' })), null);
});
