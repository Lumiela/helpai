import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createSseParser, type SseMessage } from './sse';

function collect() {
  const out: SseMessage[] = [];
  return { out, feed: createSseParser((m) => out.push(m)) };
}

test('data 줄과 빈 줄로 이벤트를 자른다', () => {
  const { out, feed } = collect();
  feed('data: {"a":1}\n\n');
  assert.deepEqual(out, [{ event: undefined, id: undefined, data: '{"a":1}' }]);
});

test('조각이 줄 중간에서 끊겨도 이어 붙인다', () => {
  const { out, feed } = collect();
  feed('da');
  feed('ta: {"sn":"CU');
  feed('BE001"}\n');
  assert.equal(out.length, 0);
  feed('\n');
  assert.equal(out.length, 1);
  assert.equal(out[0].data, '{"sn":"CUBE001"}');
});

test('keepalive 주석은 무시하고, 여러 data 줄은 줄바꿈으로 합친다', () => {
  const { out, feed } = collect();
  feed(': keepalive\n\n');
  assert.equal(out.length, 0);
  feed('event: x\nid: 7\ndata: 1\ndata: 2\n\n');
  assert.deepEqual(out, [{ event: 'x', id: '7', data: '1\n2' }]);
});

test('\\r\\n 줄 끝도 처리한다', () => {
  const { out, feed } = collect();
  feed('data: a\r\n\r\ndata: b\r\n\r\n');
  assert.deepEqual(out.map((m) => m.data), ['a', 'b']);
});
