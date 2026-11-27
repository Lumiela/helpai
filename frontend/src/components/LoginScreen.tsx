import React, { useState } from 'react';
import { LogIn } from 'lucide-react';
import { useAuth } from '../lib/AuthContext';

// 통합 로그인 화면 /login (2026-09-29 오후, 사용자 결정). 로그인되면 소유 인스턴스로 이동한다(AuthContext).
// 회원가입 = 첫 구글 로그인 + 큐브 시리얼 번호. 번호는 처음 등록할 때만 넣는다. 설명 문구는 최소로.
export const LoginScreen: React.FC = () => {
  const auth = useAuth();
  const [sn, setSn] = useState('');
  return (
    <div className="app-viewport">
      <div className="app-shell bg-surface text-ink flex items-center justify-center p-6">
        <form
          className="panel w-full max-w-sm p-6 flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            void auth.signIn(sn);
          }}
        >
          <h1 className="text-lg font-bold text-ink-strong">스마트큐브 관제</h1>
          {auth.error && (
            <p className="text-sm text-danger" role="alert">
              {auth.error}
            </p>
          )}
          <input
            type="text"
            autoComplete="off"
            autoCapitalize="characters"
            className="rounded border border-line bg-panel px-2 py-1 text-sm font-mono"
            placeholder="큐브 시리얼 번호 (선택 — 나중에 장치 연결에서 추가해도 됩니다)"
            value={sn}
            onChange={(e) => setSn(e.target.value)}
            aria-label="큐브 시리얼 번호"
          />
          <button type="submit" className="btn btn-primary" disabled={auth.loading}>
            <LogIn className="w-4 h-4" aria-hidden="true" />
            <span>구글로 로그인</span>
          </button>
        </form>
      </div>
    </div>
  );
};
