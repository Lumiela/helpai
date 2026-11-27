import assert from 'node:assert/strict';
import { test } from 'node:test';
import { authBypassed, claimsFromToken, decodeJwtPayload, setAuthBypass, supabase } from './auth';

function fakeJwt(payload: Record<string, unknown>): string {
  const enc = (o: unknown) => Buffer.from(JSON.stringify(o)).toString('base64url');
  return `${enc({ alg: 'HS256', typ: 'JWT' })}.${enc(payload)}.sig`;
}

test('JWT 본문을 읽는다 (base64url, 한글 포함)', () => {
  const p = decodeJwtPayload(fakeJwt({ sub: 'abc', email: '홍길동@example.com', exp: 123 }));
  assert.equal(p?.sub, 'abc');
  assert.equal(p?.email, '홍길동@example.com');
  assert.equal(decodeJwtPayload('not-a-jwt'), null);
  assert.equal(decodeJwtPayload('a.b'), null);
});

test('app_metadata 에서 소유 인스턴스와 운영자 여부를 꺼낸다', () => {
  const c = claimsFromToken(fakeJwt({ sub: 'u1', email: 'a@b', exp: 99, app_metadata: { instances: ['jetson-01', 7], operator: true } }));
  assert.deepEqual(c, { sub: 'u1', email: 'a@b', instances: ['jetson-01'], operator: true, exp: 99 });
});

test('키가 없거나 이상하면 소유 없음·운영자 아님', () => {
  const c = claimsFromToken(fakeJwt({ sub: 'u1', app_metadata: { operator: 'yes' } }));
  assert.deepEqual(c?.instances, []);
  assert.equal(c?.operator, false);
  assert.deepEqual(claimsFromToken(fakeJwt({ sub: 'u1' }))?.instances, []);
});

test('실행 시점 우회 스위치: 켜면 supabase 클라이언트를 만들지 않는다', () => {
  assert.equal(authBypassed(), false);
  setAuthBypass(true);
  assert.equal(authBypassed(), true);
  assert.equal(supabase(), null);
  setAuthBypass(false);
  assert.equal(authBypassed(), false);
});
