import React, { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { supabase } from '../lib/auth';
import { getInstanceStatus, type InstanceStatus } from '../lib/backend';
import { useAuth } from '../lib/AuthContext';

// 인스턴스 상태 화면 (2026-09-29, 소유자 모델). 역할·소유 지정은 없다(사용자 결정).
// 서버 상태는 api /instance/status, 조작·접근 기록은 Supabase audit(행 단위 보안: 소유자는 자기 인스턴스 것만).

interface AuditRow {
  id: number;
  at: string;
  email: string | null;
  sn: string;
  action: string;
  payload: unknown;
  result: string | null;
}

const ACTION_LABEL: Record<string, string> = {
  control: '제어',
  config: '보정',
  refresh: '갱신 요청',
  claim: '큐브 등록',
  claim_denied: '큐브 등록 거절',
  operator_access: '운영자 읽기 접근',
  operator_access_denied: '운영자 접근 거절',
  operator_release: '운영자 접근 반납',
  // 2026-10-06 작물 기반 자동 제어 (자동 명령의 계정 칸은 "auto")
  auto_control: '자동 제어 명령',
  auto_off_manual: '직접 조작으로 자동 꺼짐',
  auto_set: '자동 제어 설정',
  crop_set: '작물 지정',
  // 2026-10-06 AI 리포트
  report_settings: '리포트 간격 변경',
  report_retry: '리포트 재요청',
};

function fmt(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('ko-KR', { hour12: false });
}

export const InstancePanel: React.FC = () => {
  const auth = useAuth();
  const [status, setStatus] = useState<InstanceStatus | null>(null);
  const [audit, setAudit] = useState<AuditRow[]>([]);
  const [message, setMessage] = useState<string>('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setMessage('');
    try {
      setStatus(await getInstanceStatus());
      const s = supabase();
      if (s) {
        let q = s.from('audit').select('id,at,email,sn,action,payload,result').order('at', { ascending: false }).limit(50);
        if (auth.instanceId) q = q.eq('instance_id', auth.instanceId);
        const { data, error } = await q;
        if (error) throw error;
        setAudit((data ?? []) as AuditRow[]);
      }
    } catch (e) {
      setMessage(`불러오기 실패: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }, [auth.instanceId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <h2 className="text-base font-bold text-ink-strong">인스턴스 상태</h2>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => void load()} disabled={busy}>
          <RefreshCw className={`w-4 h-4 ${busy ? 'animate-spin' : ''}`} aria-hidden="true" />
          <span>새로 고침</span>
        </button>
        {message && <span className="text-sm text-ink-muted">{message}</span>}
      </div>

      {status && (
        <section className="panel p-4">
          <h3 className="text-sm font-semibold text-ink-strong mb-2">서버</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
            <div><span className="text-ink-muted">인스턴스</span><div>{status.instance_id || '—'}</div></div>
            <div><span className="text-ink-muted">api</span><div>{status.version} · {Math.floor(status.uptime_s / 60)}분</div></div>
            <div><span className="text-ink-muted">브로커</span><div>{status.mqtt_connected ? '연결됨' : '끊김'}</div></div>
            <div><span className="text-ink-muted">이력 기록</span><div>{status.writer.enabled ? `${status.writer.written_lines}행 · 실패 ${status.writer.failed_batches}` : '꺼짐'}</div></div>
          </div>
          {status.writer.last_error && <p className="text-xs text-danger mt-2">기록 오류: {status.writer.last_error}</p>}
          <table className="w-full text-sm mt-3">
            <thead><tr className="text-ink-muted text-left"><th>큐브</th><th>펌웨어</th><th>마지막 수신</th><th>순번</th></tr></thead>
            <tbody>
              {Object.keys(status.cubes).map((sn) => {
                const c = status.cubes[sn];
                return (
                  <tr key={sn}><td>{sn}</td><td>{c.fw ?? '—'}</td><td>{c.seen ? fmt(new Date(c.seen * 1000).toISOString()) : '—'}</td><td>{c.last_seq}</td></tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}

      {(auth.enabled || auth.bypassed) && (
        <section className="panel p-4">
          <h3 className="text-sm font-semibold text-ink-strong mb-2">조작·접근 기록 (최근 50)</h3>
          {audit.length === 0 ? (
            <p className="text-sm text-ink-muted">기록이 없습니다.</p>
          ) : (
            <table className="w-full text-sm">
              <thead><tr className="text-ink-muted text-left"><th>시각</th><th>계정</th><th>큐브</th><th>동작</th><th>내용</th><th>결과</th></tr></thead>
              <tbody>
                {audit.map((a) => (
                  <tr key={a.id}>
                    <td className="whitespace-nowrap">{fmt(a.at)}</td>
                    <td>{a.email ?? '—'}</td>
                    <td className="font-mono">{a.sn}</td>
                    <td>{ACTION_LABEL[a.action] ?? a.action}</td>
                    <td className="font-mono text-xs break-all">{JSON.stringify(a.payload)}</td>
                    <td>{a.result ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
    </div>
  );
};
