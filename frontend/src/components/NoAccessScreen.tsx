import React, { useState } from 'react';
import { KeyRound } from 'lucide-react';
import { useAuth } from '../lib/AuthContext';
import { BackendError, postOperatorAccess } from '../lib/backend';

// 로그인은 됐지만 이 인스턴스(/i/<iid>/)의 소유자가 아닌 사람에게 보이는 화면 (2026-09-29, 소유자 모델·다중 인스턴스).
// 운영자 계정이면 마스터 키 + 사유로 읽기 접근을 요청할 수 있다.
export const NoAccessScreen: React.FC = () => {
  const auth = useAuth();
  const [key, setKey] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const isOperator = auth.me?.operator === true;

  const request = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await postOperatorAccess(key, reason);
      setKey('');
      await auth.refreshMe();
    } catch (e) {
      setMessage(e instanceof BackendError ? e.message : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="app-viewport">
      <div className="app-shell bg-surface text-ink flex items-center justify-center p-6">
        <div className="panel w-full max-w-md p-6 flex flex-col gap-4">
          <div>
            <h1 className="text-lg font-bold text-ink-strong">스마트큐브 관제</h1>
            <p className="text-sm text-ink-muted mt-1">
              {auth.claims?.email} 계정은 {auth.instanceId ?? '이 인스턴스'} 의 소유자가 아닙니다.
            </p>
          </div>

          {isOperator && (
            <form
              className="flex flex-col gap-2 border-t border-line pt-4"
              onSubmit={(e) => {
                e.preventDefault();
                void request();
              }}
            >
              <p className="text-sm text-ink-strong font-semibold">운영자 읽기 접근</p>
              <input
                type="password"
                autoComplete="off"
                className="rounded border border-line bg-panel px-2 py-1 text-sm"
                placeholder="마스터 키"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                aria-label="마스터 키"
              />
              <input
                type="text"
                className="rounded border border-line bg-panel px-2 py-1 text-sm"
                placeholder="접근 사유 (예: 고객 요청 점검)"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                aria-label="접근 사유"
              />
              <button type="submit" className="btn btn-primary" disabled={busy || key.length === 0 || reason.trim().length < 2}>
                <KeyRound className="w-4 h-4" aria-hidden="true" />
                <span>읽기 접근 요청</span>
              </button>
              {message && (
                <p className="text-sm text-danger" role="alert">
                  {message}
                </p>
              )}
            </form>
          )}

          <div className="flex items-center gap-2">
            <a href="/login" className="btn btn-secondary btn-sm">
              내 인스턴스
            </a>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => void auth.signOut()}>
              다른 계정으로 로그인
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
