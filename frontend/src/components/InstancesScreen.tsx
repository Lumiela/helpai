import React, { useState } from 'react';
import { ArrowRight, PlusCircle } from 'lucide-react';
import { useAuth } from '../lib/AuthContext';
import { instanceDisplayName, instanceHome } from '../lib/instancePath';

// /login 에서 로그인된 뒤, 소유 인스턴스가 여럿이거나 없을 때 보이는 화면 (2026-09-29 오후).
// 하나면 AuthContext 가 바로 그 인스턴스로 보내므로 이 화면은 뜨지 않는다. 설명 문구는 최소로.
export const InstancesScreen: React.FC = () => {
  const auth = useAuth();
  const [sn, setSn] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const owned = auth.meGlobal?.instances ?? [];
  const granted = Object.keys(auth.meGlobal?.grants ?? {}).filter((i) => !owned.includes(i));
  const items = [...owned.map((iid) => ({ iid, label: '소유자' })), ...granted.map((iid) => ({ iid, label: '운영자 읽기' }))];

  const register = async () => {
    setMessage(null);
    try {
      const iid = await auth.claim(sn);
      window.location.assign(instanceHome(iid));
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="app-viewport">
      <div className="app-shell bg-surface text-ink flex items-center justify-center p-6">
        <div className="panel w-full max-w-md p-6 flex flex-col gap-5">
          <div>
            <h1 className="text-lg font-bold text-ink-strong">스마트큐브 관제</h1>
            <p className="text-sm text-ink-muted mt-1">{auth.bypassed ? '인증 꺼짐 (프로토타입) — 인스턴스를 고르세요' : auth.claims?.email}</p>
          </div>

          {auth.error && (
            <p className="text-sm text-danger" role="alert">
              {auth.error}
            </p>
          )}

          {items.length > 0 && (
            <ul className="flex flex-col gap-2" aria-label="내 인스턴스">
              {items.map(({ iid, label }) => (
                <li key={iid}>
                  <a href={instanceHome(iid)} className="btn btn-secondary w-full justify-between">
                    <span className="font-mono">{instanceDisplayName(iid)}</span>
                    <span className="flex items-center gap-1 text-xs text-ink-muted">
                      {label} <ArrowRight className="w-4 h-4" aria-hidden="true" />
                    </span>
                  </a>
                </li>
              ))}
            </ul>
          )}

          <form
            className={`flex flex-col gap-2 ${items.length > 0 ? 'border-t border-line pt-4' : ''}`}
            onSubmit={(e) => {
              e.preventDefault();
              void register();
            }}
          >
            <input
              type="text"
              autoComplete="off"
              autoCapitalize="characters"
              className="rounded border border-line bg-panel px-2 py-1 text-sm font-mono"
              placeholder="큐브 시리얼 번호 (예: CUBE001)"
              value={sn}
              onChange={(e) => setSn(e.target.value)}
              aria-label="큐브 시리얼 번호"
            />
            <button type="submit" className="btn btn-primary" disabled={auth.claiming || sn.trim().length === 0}>
              <PlusCircle className="w-4 h-4" aria-hidden="true" />
              <span>{auth.claiming ? '등록하는 중…' : '큐브 등록'}</span>
            </button>
            {message && (
              <p className="text-sm text-danger" role="alert">
                {message}
              </p>
            )}
          </form>

          <button type="button" className="btn btn-ghost btn-sm self-start" onClick={() => void auth.signOut()}>
            로그아웃
          </button>
        </div>
      </div>
    </div>
  );
};
