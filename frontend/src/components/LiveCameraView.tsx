import React, { useEffect, useRef, useState } from 'react';
import { useAuth } from '../lib/AuthContext';
import { BackendError, getCameras, openMjpegStream, postStreamStart, postStreamStop, type CameraInfo } from '../lib/backend';
import { pruneFrameTimes, recentFps } from '../lib/fps';

interface LiveCameraViewProps {
  cubeSn: string;
  devMode?: boolean;
}

// 큐브 단면 카드의 "생육 카메라 보기" (2026-09-29 오후, 사용자 결정: 별도 카드 없음, 사진은 고객용이 아님).
// 열리면 스트리밍을 시작하고 MJPEG 를 fetch 로 받아 그린다. 닫히면(언마운트) 정지한다. 카메라가 여럿이면 고른다.
// 2026-10-02 사용자 지시: 개발자 모드에서는 최근 5초 fps 를 배지로 보여 준다(1초마다 갱신).
export const LiveCameraView: React.FC<LiveCameraViewProps> = ({ cubeSn, devMode = false }) => {
  const auth = useAuth();
  const [cams, setCams] = useState<CameraInfo[] | null>(null);
  const [cam, setCam] = useState<string>('');
  const [url, setUrl] = useState<string | null>(null);
  const [frames, setFrames] = useState(0);
  const [fps, setFps] = useState<number | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const closeRef = useRef<(() => void) | null>(null);
  const urlRef = useRef<string | null>(null);
  const frameTimesRef = useRef<number[]>([]);
  const firstFrameAtRef = useRef<number | null>(null);

  useEffect(() => {
    if (!devMode || !cam) return;
    const tick = () => {
      const now = performance.now();
      pruneFrameTimes(frameTimesRef.current, now);
      setFps(recentFps(frameTimesRef.current, now, firstFrameAtRef.current));
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [devMode, cam]);

  useEffect(() => {
    let alive = true;
    void getCameras()
      .then((all) => {
        if (!alive) return;
        const mine = all.filter((c) => c.cube === cubeSn);
        setCams(mine);
        setCam((prev) => (mine.some((c) => c.sn === prev) ? prev : mine[0]?.sn ?? ''));
      })
      .catch((e) => {
        if (!alive) return;
        setCams([]);
        setMessage(e instanceof BackendError ? e.message : e instanceof Error ? e.message : String(e));
      });
    return () => {
      alive = false;
    };
  }, [cubeSn]);

  useEffect(() => {
    if (!cam) return;
    let alive = true;
    setMessage(null);
    setFrames(0);
    setFps(null);
    frameTimesRef.current = [];
    firstFrameAtRef.current = null;
    void (async () => {
      try {
        if (auth.canControl) await postStreamStart(cam);
      } catch (e) {
        if (alive) setMessage(e instanceof BackendError ? e.message : e instanceof Error ? e.message : String(e));
        return;
      }
      if (!alive) return;
      closeRef.current = openMjpegStream(
        cam,
        (blob) => {
          if (!alive) return;
          const t = performance.now();
          if (firstFrameAtRef.current === null) firstFrameAtRef.current = t;
          frameTimesRef.current.push(t);
          pruneFrameTimes(frameTimesRef.current, t);
          const next = URL.createObjectURL(blob);
          if (urlRef.current) URL.revokeObjectURL(urlRef.current);
          urlRef.current = next;
          setUrl(next);
          setFrames((n) => n + 1);
        },
        (err) => {
          if (alive) setMessage(err);
        },
      );
    })();
    return () => {
      alive = false;
      closeRef.current?.();
      closeRef.current = null;
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
      setUrl(null);
      if (auth.canControl) void postStreamStop(cam).catch(() => undefined);
    };
    // canControl 변화로 재시작하지 않는다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cam]);

  if (cams === null) return <p className="text-sm text-ink-muted">카메라를 확인하는 중…</p>;
  if (cams.length === 0) return <p className="text-sm text-ink-muted">이 큐브에는 생육 카메라가 연결돼 있지 않습니다.</p>;

  return (
    <div className="absolute inset-0 flex flex-col gap-2">
      <div className="flex items-center gap-2 text-xs shrink-0">
        {cams.length > 1 ? (
          <select value={cam} onChange={(e) => setCam(e.target.value)} aria-label="카메라 선택" className="text-xs">
            {cams.map((c) => (
              <option key={c.sn} value={c.sn}>
                {c.name ? `${c.name} (${c.sn})` : c.sn}
              </option>
            ))}
          </select>
        ) : (
          <span className="font-mono">{cam}</span>
        )}
        <span className={`badge ${url ? 'badge-ok' : 'badge-info'}`}>{url ? `실시간 · ${frames}` : '연결 중'}</span>
        {devMode && url && (
          <span className="badge badge-info" title="개발자 모드: 최근 5초 동안 받은 프레임 기준, 1초마다 갱신">
            {fps === null ? '— fps' : `${fps.toFixed(1)} fps`}
          </span>
        )}
        {message && (
          <span className="text-danger" role="alert">
            {message}
          </span>
        )}
      </div>
      <div className="relative min-h-0 flex-1 rounded-md overflow-hidden bg-inset flex items-center justify-center">
        {url ? (
          <img src={url} alt={`${cam} 실시간`} className="max-h-full max-w-full object-contain" />
        ) : (
          <p className="text-sm text-ink-muted">프레임을 기다리는 중…</p>
        )}
      </div>
    </div>
  );
};
