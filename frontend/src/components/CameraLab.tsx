import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Camera, Image as ImageIcon, RefreshCw } from 'lucide-react';
import { useAuth } from '../lib/AuthContext';
import {
  BackendError, getCameras, getSnapshotBlob, getSnapshots, postSnapshotPair, postSnapshotRequest,
  type CameraInfo, type SnapshotPairResult, type SnapshotRow,
} from '../lib/backend';

interface CameraLabProps {
  cubes: string[];
}

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('ko-KR', { hour12: false });
}

// 카메라가 보낸 촬영 시각(epoch ms) → 시:분:초.밀리초
function fmtMs(ms: number | null | undefined): string {
  if (!ms) return '—';
  const d = new Date(ms);
  return `${d.toLocaleTimeString('ko-KR', { hour12: false })}.${String(ms % 1000).padStart(3, '0')}`;
}

// 즉시 촬영 배율(2026-10-08 사용자 지시): 1·1.5·2·3배. 펌웨어 cube-cam-0.6-zoom 이 센서 가운데를 잘라 원본 화소로 보낸다(늘리기 없음).
// 그 촬영 한 번에만 적용되고 주기 촬영·스트리밍은 항상 1배. 줌을 모르는 펌웨어는 1배로 찍는다.
const ZOOMS = [1, 1.5, 2, 3] as const;
const ZOOM_SIZE: Record<number, string> = { 1: '2560×1920', 1.5: '1712×1280', 2: '1280×960', 3: '848×640' };

// 개발자 도구(알림 API 테스트) 안의 카메라 시험 (2026-09-29 사용자 지시): 즉시 촬영 + 최신 사진 조회.
// 사진은 고객용이 아니므로 고객 화면에는 없고 여기서만 본다. 즉시 촬영은 소유자만(서버 규칙).
export const CameraLab: React.FC<CameraLabProps> = ({ cubes }) => {
  const auth = useAuth();
  const [cube, setCube] = useState<string>('');
  const [cams, setCams] = useState<CameraInfo[]>([]);
  const [cam, setCam] = useState<string>('');
  const [zoom, setZoom] = useState<number>(1);
  // 동시 촬영(2026-11-11): 카메라 A·B 와 마지막 결과(두 사진 blob URL 포함)
  const [camA, setCamA] = useState<string>('');
  const [camB, setCamB] = useState<string>('');
  const [pair, setPair] = useState<{ result: SnapshotPairResult; urls: [string, string] } | null>(null);
  const pairUrlsRef = useRef<string[]>([]);
  const [rows, setRows] = useState<SnapshotRow[]>([]);
  const [thumbs, setThumbs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<'snap' | 'pair' | 'list' | null>(null);
  const [log, setLog] = useState<string>('');
  const thumbsRef = useRef(thumbs);
  thumbsRef.current = thumbs;

  useEffect(() => {
    if (!cube && cubes[0]) setCube(cubes[0]);
  }, [cubes, cube]);

  const loadCams = useCallback(async () => {
    if (!cube) return;
    try {
      const all = await getCameras();
      const mine = all.filter((c) => c.cube === cube);
      setCams(mine);
      setCam((prev) => (mine.some((c) => c.sn === prev) ? prev : mine[0]?.sn ?? ''));
      setCamA((prev) => (mine.some((c) => c.sn === prev) ? prev : mine[0]?.sn ?? ''));
      setCamB((prev) => (mine.some((c) => c.sn === prev) ? prev : mine[1]?.sn ?? ''));
    } catch (e) {
      setCams([]);
      setLog(e instanceof BackendError ? e.message : e instanceof Error ? e.message : String(e));
    }
  }, [cube]);

  useEffect(() => {
    void loadCams();
  }, [loadCams]);

  useEffect(
    () => () => {
      for (const u of Object.values<string>(thumbsRef.current)) URL.revokeObjectURL(u);
      for (const u of pairUrlsRef.current) URL.revokeObjectURL(u);
    },
    [],
  );

  const list = async () => {
    if (!cube) return;
    setBusy('list');
    try {
      const r = await getSnapshots(cube, { cam: cam || undefined, limit: 6 });
      setRows(r.rows);
      setLog(`목록 ${r.count}건 (큐브 ${r.cube}${r.camera ? `, 카메라 ${r.camera}` : ''})`);
      const next: Record<string, string> = {};
      for (const row of r.rows) {
        const key = `${row.camera}/${row.path}`;
        if (thumbsRef.current[key]) {
          next[key] = thumbsRef.current[key];
          continue;
        }
        try {
          next[key] = URL.createObjectURL(await getSnapshotBlob(cube, row.camera, row.date, row.file));
        } catch {
          // 썸네일 실패는 무시
        }
      }
      for (const [k, u] of Object.entries<string>(thumbsRef.current)) if (!next[k]) URL.revokeObjectURL(u);
      setThumbs(next);
    } catch (e) {
      setLog(e instanceof BackendError ? e.message : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const snap = async () => {
    if (!cube) return;
    setBusy('snap');
    setLog(`촬영 명령(배율 ${zoom}배)을 보내고 업로드를 기다리는 중(최대 60초)…`);
    try {
      const r = await postSnapshotRequest(cube, cam || undefined, zoom);
      const z = r.zoom_pct ? `${r.zoom_pct / 100}배` : zoom !== 1 ? '1배 — 카메라가 배율을 적용하지 않음(펌웨어 확인)' : '1배';
      setLog(`촬영 완료: ${r.camera} ${r.path} (${Math.round(r.bytes / 1024)}KB, ${fmtTime(r.taken_at)}, 배율 ${z})`);
      await list();
    } catch (e) {
      setLog(e instanceof BackendError ? `실패 (HTTP ${e.status}): ${e.message}` : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  // 동시 촬영: 두 카메라에 명령을 연달아 보내고 두 장을 함께 받는다. 결과는 두 사진 + 촬영 시각 차이
  const snapPair = async () => {
    if (!cube || !camA || !camB || camA === camB) return;
    setBusy('pair');
    setLog(`동시 촬영 명령(${camA} + ${camB}, 배율 ${zoom}배)을 보내고 두 업로드를 기다리는 중(최대 60초)…`);
    try {
      const r = await postSnapshotPair(cube, camA, camB, zoom);
      const skew =
        r.capture_skew_ms === null
          ? '촬영 시각 차이 — (카메라가 촬영 시각을 보내지 않음: 펌웨어 0.7.1-captime 필요)'
          : `촬영 시각 차이 ${r.capture_skew_ms}ms`;
      setLog(`동시 촬영 완료 (${r.pair_id}): ${skew}, 서버 도착 차이 ${r.arrival_skew_ms}ms`);
      const urls: [string, string] = [
        URL.createObjectURL(await getSnapshotBlob(cube, r.a.camera, r.a.date, r.a.file)),
        URL.createObjectURL(await getSnapshotBlob(cube, r.b.camera, r.b.date, r.b.file)),
      ];
      for (const u of pairUrlsRef.current) URL.revokeObjectURL(u);
      pairUrlsRef.current = urls;
      setPair({ result: r, urls });
      await list();
    } catch (e) {
      setLog(e instanceof BackendError ? `실패 (HTTP ${e.status}): ${e.message}` : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="panel p-5 space-y-4" aria-labelledby="api-camera">
      <div className="flex items-center justify-between gap-3">
        <h3 id="api-camera" className="text-base font-semibold text-ink-strong font-mono">
          camera.snapshot
        </h3>
        <span className="text-sm text-ink-muted">즉시 촬영 · 최신 사진 조회 (학습용, 고객 화면에는 없음)</span>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <label className="flex items-center gap-2">
          <span className="text-ink-muted">큐브</span>
          <select value={cube} onChange={(e) => setCube(e.target.value)} aria-label="큐브">
            {cubes.length === 0 && <option value="">큐브 없음</option>}
            {cubes.map((sn) => (
              <option key={sn} value={sn}>
                {sn}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2">
          <span className="text-ink-muted">카메라</span>
          <select value={cam} onChange={(e) => setCam(e.target.value)} aria-label="카메라">
            {cams.length === 0 && <option value="">없음</option>}
            {cams.length > 1 && <option value="">(전체)</option>}
            {cams.map((c) => (
              <option key={c.sn} value={c.sn}>
                {c.sn}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => void loadCams()} aria-label="카메라 다시 불러오기">
          <RefreshCw className="w-4 h-4" aria-hidden="true" />
        </button>
        <label className="flex items-center gap-2">
          <span className="text-ink-muted">배율</span>
          <select value={String(zoom)} onChange={(e) => setZoom(Number(e.target.value))} aria-label="촬영 배율">
            {ZOOMS.map((z) => (
              <option key={z} value={String(z)}>
                {z}배 ({ZOOM_SIZE[z]})
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="btn btn-primary btn-sm" onClick={() => void snap()} disabled={busy !== null || !cube || cams.length === 0 || !auth.canControl}>
          <Camera className="w-4 h-4" aria-hidden="true" />
          <span>{busy === 'snap' ? '촬영 중…' : '즉시 촬영'}</span>
        </button>
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => void list()} disabled={busy !== null || !cube}>
          <ImageIcon className="w-4 h-4" aria-hidden="true" />
          <span>{busy === 'list' ? '조회 중…' : '최신 사진 조회'}</span>
        </button>
      </div>
      {cams.length >= 2 && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-ink-muted">동시 촬영(2대, 스테레오 시험)</span>
          <label className="flex items-center gap-2">
            <span className="text-ink-muted">A</span>
            <select value={camA} onChange={(e) => setCamA(e.target.value)} aria-label="동시 촬영 카메라 A">
              {cams.map((c) => (
                <option key={c.sn} value={c.sn}>
                  {c.sn}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2">
            <span className="text-ink-muted">B</span>
            <select value={camB} onChange={(e) => setCamB(e.target.value)} aria-label="동시 촬영 카메라 B">
              {cams.map((c) => (
                <option key={c.sn} value={c.sn}>
                  {c.sn}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => void snapPair()}
            disabled={busy !== null || !cube || !camA || !camB || camA === camB || !auth.canControl}
          >
            <Camera className="w-4 h-4" aria-hidden="true" />
            <span>{busy === 'pair' ? '동시 촬영 중…' : '동시 촬영'}</span>
          </button>
          {camA === camB && <span className="text-xs text-ink-muted">서로 다른 카메라를 고르세요</span>}
        </div>
      )}
      {!auth.canControl && <p className="text-xs text-ink-muted">즉시 촬영은 소유자만 할 수 있습니다.</p>}
      <p className="text-xs text-ink-muted">
        배율은 펌웨어 cube-cam-0.6-zoom 카메라에서만, 이 촬영 한 번에만 적용됩니다. 센서 가운데를 잘라 원본 화소 그대로 보내므로 늘려서 흐려지지는 않고 화소 수만 줄어듭니다.
      </p>
      {log && <pre className="text-xs whitespace-pre-wrap">{log}</pre>}

      {pair && (
        <div className="rounded border border-line p-3 text-xs space-y-2">
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <span className="font-mono">pair {pair.result.pair_id}</span>
            <span>
              촬영 시각 차이:{' '}
              <b>{pair.result.capture_skew_ms === null ? '— (펌웨어가 촬영 시각을 안 보냄)' : `${pair.result.capture_skew_ms}ms`}</b>
            </span>
            <span>서버 도착 차이: {pair.result.arrival_skew_ms}ms</span>
          </div>
          <div className="grid grid-cols-2 gap-3">
            {([['A', pair.result.a, pair.urls[0]], ['B', pair.result.b, pair.urls[1]]] as const).map(([k, row, url]) => (
              <div key={k} className="flex flex-col gap-1">
                <img src={url} alt={`${k} ${row.camera}`} className="w-full rounded" />
                <span className="font-mono">
                  {k} · {row.camera}
                </span>
                <span className="text-ink-muted">
                  카메라 촬영 {fmtMs(row.cap_at)} · 명령→촬영 {row.cmd_ms ?? '—'}ms · 서버 저장 {fmtTime(row.taken_at)}
                </span>
                <span className="font-mono text-ink-muted break-all">{row.path}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {rows.length > 0 && (
        <ul className="grid grid-cols-2 md:grid-cols-3 gap-3">
          {rows.map((row) => {
            const key = `${row.camera}/${row.path}`;
            const url = thumbs[key];
            return (
              <li key={key} className="rounded border border-line p-2 text-xs flex flex-col gap-1">
                {url ? (
                  <img src={url} alt={`${row.camera} ${row.file}`} className="w-full rounded" />
                ) : (
                  <div className="h-24 rounded bg-inset flex items-center justify-center text-ink-muted">불러오는 중…</div>
                )}
                <span className="font-mono">{row.camera}</span>
                <span className="text-ink-muted">
                  {fmtTime(row.taken_at)} · {row.trigger ?? '—'} · {Math.round(row.bytes / 1024)}KB
                  {row.zoom_pct ? ` · ${row.zoom_pct / 100}배` : ''}
                  {row.cap_at ? ` · 카메라 촬영 ${fmtMs(row.cap_at)}` : ''}
                </span>
                <span className="font-mono text-ink-muted break-all">{row.path}</span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
};
