import React, { useEffect, useState } from 'react';
import { Camera, Check, ChevronDown, Copy, Eye, EyeOff, KeyRound, Menu, PlusCircle, RefreshCw, X } from 'lucide-react';
import { EdgeDevice } from '../types/smartfarm';
import {
  BackendError,
  deleteCamera,
  deleteCube,
  getBroker,
  getCameras,
  getProvisionCodes,
  postAddCamera,
  postBrokerRotate,
  postEraseCube,
  postProvisionCode,
  type BrokerInfo,
  type CameraInfo,
  type ProvisionCode,
  type ProvisionStatus,
} from '../lib/backend';
import { EdgeNotice } from '../lib/agToolkit';
import { mergeDeviceRows } from '../lib/deviceMap';
import { Dropdown } from './Dropdown';
import { QrCode } from './QrCode';

interface EdgeDevicesListProps {
  devices: EdgeDevice[];
  canManage: boolean; // 소유자만 장치 추가·브로커 계정
  onAdded: () => void;
  onPing: (device: EdgeDevice) => void;
}

const TYPE_LABEL: Record<EdgeDevice['type'], string> = {
  sensor_node: '센서 노드',
  actuator_controller: '제어기',
  camera: '카메라',
  weather_station: '기상 센서',
  irrigation_unit: '급수 장치',
};

const STATUS_LABEL: Record<EdgeDevice['status'], { text: string; cls: string }> = {
  connected: { text: '연결됨', cls: 'badge-ok' },
  offline: { text: '통신 끊김', cls: 'badge-danger' },
  unpaired: { text: '미등록', cls: 'badge-warn' },
  updating: { text: '업데이트 중', cls: 'badge-info' },
};

function signalText(dbm: number | null) {
  if (dbm === null) return '—';
  const grade = dbm >= -55 ? '좋음' : dbm >= -67 ? '보통' : '약함';
  return `${dbm} dBm, ${grade}`;
}

// ESP32 칩 내부 온도 (2026-09-30). 보정 없는 센서라 절대값보다 추이를 본다. 없으면 '—'.
const CHIP_TEMP_NOTE = 'ESP32 칩 내부 온도 센서. 보정이 없어 개체마다 수 °C 차이가 나며, 평소보다 얼마나 올랐는지를 보는 값입니다.';
function chipTempText(c: number | null) {
  return c === null ? '—' : `${c.toFixed(1)} °C`;
}

// 설정 보드 장치 등록 (2026-11-16, 계획서 edgeserver-JS/2026-11-16_설정보드장치등록_계획서 §6·§7)
const PROVISION_LABEL: Record<ProvisionStatus, { text: string; cls: string }> = {
  waiting: { text: '등록 대기', cls: 'badge-info' },
  registered: { text: '장치 연결 중', cls: 'badge-info' },
  done: { text: '등록 완료', cls: 'badge-ok' },
  failed: { text: '등록 실패', cls: 'badge-danger' },
  expired: { text: '만료', cls: 'badge-warn' },
  replaced: { text: '무효(새 코드 발급)', cls: 'badge-warn' },
};
const PROVISION_ACTIVE: ProvisionStatus[] = ['waiting', 'registered'];

function durationText(s: number) {
  return s >= 60 ? `${Math.round(s / 60)}분` : `${s}초`;
}

function timeText(epochS: number) {
  return new Date(epochS * 1000).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// 아직 한 번도 붙지 않은 장치 — 큐브마다 가장 최근 코드 기준, 등록이 끝나지 않았고 연결 목록에 없는 장치
function unpairedBoards(codes: ProvisionCode[]): Set<string> {
  const latest = new Map<string, ProvisionCode>();
  for (const c of codes) if (c.status !== 'replaced' && (!latest.has(c.sn) || c.issued_at > latest.get(c.sn)!.issued_at)) latest.set(c.sn, c);
  const out = new Set<string>();
  for (const c of latest.values()) {
    if (c.status === 'done') continue;
    for (const board of c.expected) if (!c.connected.includes(board)) out.add(board);
  }
  return out;
}

async function copyText(text: string, label: string) {
  try {
    await navigator.clipboard.writeText(text);
    EdgeNotice.toast(`${label} 복사됨`, 2000, { record: false });
  } catch {
    EdgeNotice.toast(`${label} 복사 실패 — 직접 선택해 복사하세요`, 3000, { record: false });
  }
}

// 2026-09-29 오후: "장치 추가"(시리얼 번호로 이 인스턴스에 큐브 추가)와 "브로커 계정"(펌웨어에 넣을 값) 을 여기 둔다.
// 백엔드 연결 상태는 상단 헤더로 옮겼다. 목록은 백엔드가 아는 큐브(장치)에서 온다.
export const EdgeDevicesList: React.FC<EdgeDevicesListProps> = ({ devices, canManage, onAdded, onPing }) => {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [sn, setSn] = useState('');
  const [addMsg, setAddMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [broker, setBroker] = useState<BrokerInfo | null>(null);
  const [showPw, setShowPw] = useState(false);
  const [brokerMsg, setBrokerMsg] = useState<string | null>(null);
  // 카메라(09-29 오후): 큐브 + 번호 → <큐브>-C<번호>
  const [addingCam, setAddingCam] = useState(false);
  const [camCube, setCamCube] = useState('');
  const [camNo, setCamNo] = useState('1');
  const [camName, setCamName] = useState('');
  const [camMsg, setCamMsg] = useState<string | null>(null);
  const [cameras, setCameras] = useState<CameraInfo[]>([]);
  // 설정 보드 장치 등록: 발급한 코드 목록과 지금 펼쳐 둔 코드
  const [codes, setCodes] = useState<ProvisionCode[]>([]);
  const [shownCode, setShownCode] = useState<string | null>(null);

  const loadCameras = async () => {
    try {
      setCameras(await getCameras());
    } catch {
      setCameras([]);
    }
  };
  const loadCodes = async () => {
    if (!canManage) return;
    try {
      setCodes(await getProvisionCodes());
    } catch {
      // 등록 코드 라우트가 없는 api(설정 보드 등록 이전 판) — 코드 표시 없이 동작
    }
  };
  useEffect(() => {
    void loadCodes();
    const timer = setInterval(() => void loadCodes(), 15_000);
    return () => clearInterval(timer);
  }, [canManage]);
  const shown = codes.find((c) => c.code === shownCode) ?? null;
  // 펼쳐 둔 코드가 등록 대기·연결 중이면 3초마다 상태를 다시 읽는다
  const shownActive = shown !== null && PROVISION_ACTIVE.includes(shown.status);
  useEffect(() => {
    if (!shownActive) return;
    const timer = setInterval(() => {
      void loadCodes();
      void loadCameras();
      onAdded();
    }, 3_000);
    return () => clearInterval(timer);
  }, [shownActive]);
  // 카메라는 큐브 제어기와 같은 목록에 줄로 들어간다(2026-09-30). 접속 상태·마지막 통신이 낡지 않게 15초마다 다시 읽는다.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    void loadCameras();
    const timer = setInterval(() => {
      void loadCameras();
      setNowMs(Date.now());
    }, 15_000);
    return () => clearInterval(timer);
  }, [devices.length]);
  const unpaired = unpairedBoards(codes);
  const rows = mergeDeviceRows(devices, cameras, nowMs).map((d) =>
    d.status !== 'connected' && unpaired.has(d.id) ? { ...d, status: 'unpaired' as const } : d,
  );

  const addCamera = async () => {
    setBusy(true);
    setCamMsg(null);
    try {
      const r = await postAddCamera(camCube.trim(), Number.parseInt(camNo, 10) || 1, camName.trim());
      EdgeNotice.toast(r.already ? `${r.camera} 는 이미 있습니다` : `${r.camera} 추가됨`, 3000, { record: false });
      setAddingCam(false);
      setCamName('');
      await loadCameras();
    } catch (e) {
      setCamMsg(e instanceof BackendError ? e.message : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  // 장치 추가 = 등록 코드 발급(2026-11-16, §7 5-8). 큐브는 발급과 동시에 목록에 들어가고, 장치가 붙기 전까지 '미등록'.
  const issue = async (cubeSn: string) => {
    setBusy(true);
    setAddMsg(null);
    try {
      const r = await postProvisionCode(cubeSn.trim());
      EdgeNotice.toast(`${r.sn} 등록 코드 발급됨`, 3000, { record: false });
      setCodes((prev) => [r, ...prev.filter((c) => c.code !== r.code)]);
      setShownCode(r.code);
      setSn('');
      setAdding(false);
      onAdded();
      void loadCodes();
      if (broker) await loadBroker();
    } catch (e) {
      const message = e instanceof BackendError ? e.message : e instanceof Error ? e.message : String(e);
      setAddMsg(e instanceof BackendError && e.status === 404 ? `서버가 등록 코드 발급을 아직 지원하지 않습니다 (${message})` : message);
    } finally {
      setBusy(false);
    }
  };

  // 장치 메뉴(2026-11-18 사용자 결정): 응답 확인 · 연결 초기화(큐브 전체 = 메인 보드·카메라·설정 보드의 설정값 지우기,
  // 큐브 줄에만, 목록에 남음) · 장치 제거(큐브를 빼면 카메라도 함께)
  const errText = (e: unknown) =>
    e instanceof BackendError && e.status === 404 && /모르는 경로|Not Found/i.test(e.message)
      ? '서버가 이 기능을 아직 지원하지 않습니다'
      : e instanceof Error
        ? e.message
        : String(e);

  // 연결 초기화 = 큐브 전체(메인 보드·카메라·설정 보드). 설정 보드가 와이파이(AP)를 다시 열면 등록 코드로 다시 설정한다.
  const eraseCube = (device: EdgeDevice) => {
    const cube = device.id;
    const cams = [...new Set([...cameras.filter((c) => c.cube === cube).map((c) => c.sn), `${cube}-C1`, `${cube}-C2`])].sort();
    EdgeNotice.confirm(
      `${cube} 연결 초기화`,
      `${cube} 큐브 전체(메인 보드 ${cube}, 카메라 ${cams.join(', ')}, 설정 보드 ${cube}-AP)의 와이파이·서버 계정 설정을 지웁니다. ` +
        `설정 보드가 와이파이(${cube}-AP)를 다시 열면 등록 코드를 새로 발급해 다시 설정하세요. 장치는 목록에 남습니다.`,
      () => {
        void (async () => {
          try {
            const r = await postEraseCube(cube);
            EdgeNotice.toast(`${r.sn} 연결 초기화 명령을 보냈습니다 (${r.targets.length}대)`, 3000, { record: false });
            onAdded();
            void loadCameras();
          } catch (e) {
            EdgeNotice.toast(`${cube} 연결 초기화 실패 — ${errText(e)}`, 4000, { record: false });
          }
        })();
      },
      '연결 초기화',
    );
  };

  // 장치 제거 = 연결 초기화를 먼저 보낸 뒤 목록에서 빼기(2026-11-18 사용자 결정). 큐브는 큐브 전체, 카메라는 그 카메라.
  const removeDevice = (device: EdgeDevice) => {
    const isCam = device.type === 'camera';
    const cams = isCam ? [] : cameras.filter((c) => c.cube === device.id).map((c) => c.sn);
    const eraseWhat = isCam ? device.id : `큐브 전체(메인 보드·카메라·설정 보드 ${device.id}-AP)`;
    EdgeNotice.confirm(
      `${device.id} 장치 제거`,
      `먼저 ${eraseWhat}에 연결 초기화(와이파이·서버 계정 설정 지우기)를 보낸 뒤, ${device.id} 를 장치 목록에서 뺍니다.` +
        `${cams.length ? ` 이 큐브의 카메라(${cams.join(', ')})도 함께 빠집니다.` : ''}` +
        ' 지금 꺼져 있는 보드는 초기화 명령을 받지 못합니다.',
      () => {
        void (async () => {
          try {
            const r = isCam ? await deleteCamera(device.id) : await deleteCube(device.id);
            EdgeNotice.toast(`${r.removed.join(', ')} 제거됨 (연결 초기화 먼저 보냄)`, 3000, { record: false });
            if (expandedId && r.removed.includes(expandedId)) setExpandedId(null);
            onAdded();
            void loadCameras();
            void loadCodes();
          } catch (e) {
            EdgeNotice.toast(`${device.id} 제거 실패 — ${errText(e)}`, 4000, { record: false });
          }
        })();
      },
      '제거',
    );
  };

  // 응답 확인 버튼 자리에 두는 햄버거 메뉴. 카메라 줄은 장치 제거만(응답 확인·연결 초기화 없음), 연결 초기화·제거는 소유자만.
  const deviceMenu = (device: EdgeDevice) => {
    const canPing = device.type !== 'camera';
    if (!canPing && !canManage) return null;
    return (
      <Dropdown
        label={`${device.id} 메뉴`}
        role="menu"
        align="right"
        floating
        trigger={({ open, toggle, buttonProps }) => (
          <button
            type="button"
            {...buttonProps}
            onClick={toggle}
            className="btn btn-secondary btn-sm px-2"
            aria-label={open ? `${device.id} 메뉴 닫기` : `${device.id} 메뉴 열기`}
          >
            <Menu className="w-5 h-5" aria-hidden="true" />
          </button>
        )}
      >
        {(close) => (
          <>
            {canPing && (
              <button
                type="button"
                role="menuitem"
                className="menu-item"
                onClick={() => {
                  close();
                  onPing(device);
                }}
              >
                <span>응답 확인</span>
              </button>
            )}
            {canManage && (
              <>
                {device.type !== 'camera' && (
                  <button
                    type="button"
                    role="menuitem"
                    className="menu-item"
                    onClick={() => {
                      close();
                      eraseCube(device);
                    }}
                  >
                    <span>연결 초기화</span>
                  </button>
                )}
                <button
                  type="button"
                  role="menuitem"
                  className="menu-item text-danger"
                  onClick={() => {
                    close();
                    removeDevice(device);
                  }}
                >
                  <span>장치 제거</span>
                </button>
              </>
            )}
          </>
        )}
      </Dropdown>
    );
  };

  const loadBroker = async () => {
    setBrokerMsg(null);
    try {
      setBroker(await getBroker());
    } catch (e) {
      setBrokerMsg(e instanceof BackendError ? e.message : e instanceof Error ? e.message : String(e));
    }
  };

  const rotate = async () => {
    EdgeNotice.confirm(
      '브로커 비밀번호 재발급',
      '접속 중인 큐브는 다음 재접속부터 새 비밀번호가 필요합니다. 펌웨어 secrets.h 를 바꿔 다시 구워야 합니다.',
      () => {
        void (async () => {
          setBrokerMsg(null);
          try {
            setBroker(await postBrokerRotate());
            setShowPw(true);
          } catch (e) {
            setBrokerMsg(e instanceof BackendError ? e.message : e instanceof Error ? e.message : String(e));
          }
        })();
      },
      '재발급',
    );
  };

  return (
    <div className="bounded-page flex flex-col gap-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-ink-strong">장치 연결</h2>
        {canManage && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => {
                setAdding((v) => !v);
                setAddMsg(null);
              }}
              aria-expanded={adding}
            >
              <PlusCircle className="w-4 h-4" aria-hidden="true" />
              <span>장치 추가</span>
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => {
                setAddingCam((v) => !v);
                setCamMsg(null);
                if (!camCube && devices[0]) setCamCube(devices[0].id);
              }}
              aria-expanded={addingCam}
            >
              <Camera className="w-4 h-4" aria-hidden="true" />
              <span>카메라 추가</span>
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => {
                if (broker) {
                  setBroker(null);
                  setShowPw(false);
                } else void loadBroker();
              }}
              aria-expanded={broker !== null}
            >
              <KeyRound className="w-4 h-4" aria-hidden="true" />
              <span>브로커 계정</span>
            </button>
          </div>
        )}
      </header>

      {adding && canManage && (
        <form
          className="panel p-4 flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void issue(sn);
          }}
        >
          <input
            type="text"
            autoComplete="off"
            autoCapitalize="characters"
            className="rounded border border-line bg-panel px-2 py-1 text-sm font-mono"
            placeholder="큐브 시리얼 번호 (예: CUBE002)"
            value={sn}
            onChange={(e) => setSn(e.target.value)}
            aria-label="큐브 시리얼 번호"
          />
          <button type="submit" className="btn btn-primary btn-sm" disabled={busy || sn.trim().length === 0}>
            {busy ? '발급하는 중…' : '등록 코드 발급'}
          </button>
          {addMsg && (
            <span className="text-sm text-danger" role="alert">
              {addMsg}
            </span>
          )}
        </form>
      )}

      {shown && canManage && (
        <section className="panel p-4 flex flex-col gap-3 text-sm" aria-label={`${shown.sn} 등록 코드`}>
          <div className="flex items-center gap-2">
            <span className="font-semibold text-ink-strong">{shown.sn} 등록 코드</span>
            <span className={`badge ${PROVISION_LABEL[shown.status].cls}`}>{PROVISION_LABEL[shown.status].text}</span>
            <button type="button" className="btn btn-ghost btn-sm ml-auto" onClick={() => setShownCode(null)} aria-label="등록 코드 닫기">
              <X className="w-4 h-4" aria-hidden="true" />
            </button>
          </div>
          <div className="flex flex-wrap items-start gap-4">
            <QrCode text={shown.link} size={176} label={`${shown.sn} 등록 코드 QR`} />
            <div className="flex flex-col gap-2 min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-mono text-2xl font-semibold tracking-wider text-ink-strong">{shown.code}</span>
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => void copyText(shown.code, '등록 코드')}>
                  <Copy className="w-4 h-4" aria-hidden="true" />
                  <span>복사하기</span>
                </button>
              </div>
              <p className="text-ink-muted">{timeText(shown.expires_at)}까지 쓸 수 있습니다 (24시간).</p>
              {/* 2026-11-16 "QR 을 입구로": 설정 보드는 캡티브 창을 띄우지 않고, AP 연결 → QR 스캔 → 와이파이 입력 순서 */}
              <ol className="list-decimal pl-5 space-y-1">
                <li>
                  큐브 전원을 켜고, 휴대폰 설정에서 와이파이 <span className="font-mono">{shown.sn}-AP</span>에 연결합니다.
                </li>
                <li>휴대폰 카메라로 이 QR을 스캔합니다. 설정 페이지가 코드가 채워진 채로 열립니다.</li>
                <li>연결할 와이파이(2.4GHz) 이름과 비밀번호를 넣고 저장합니다.</li>
              </ol>
              <p className="text-ink-muted">
                QR을 쓸 수 없으면 브라우저에서 <span className="font-mono">192.168.4.1</span>을 열고 코드를 붙여 넣으세요.
              </p>
            </div>
          </div>
          {(shown.status === 'registered' || shown.status === 'failed') && (
            <ul className="flex flex-wrap gap-3" aria-label="장치 연결">
              {shown.expected.map((board) => {
                const ok = shown.connected.includes(board);
                const waiting = shown.status === 'registered';
                return (
                  <li key={board} className={`flex items-center gap-1 font-mono ${ok ? 'text-ink-strong' : waiting ? 'text-ink-muted' : 'text-danger'}`}>
                    {ok ? (
                      <Check className="w-4 h-4" aria-hidden="true" />
                    ) : waiting ? (
                      <RefreshCw className="w-4 h-4 animate-spin" aria-hidden="true" />
                    ) : (
                      <X className="w-4 h-4" aria-hidden="true" />
                    )}
                    {board}
                    <span className="sr-only">{ok ? '연결됨' : waiting ? '연결 기다리는 중' : '연결 안 됨'}</span>
                  </li>
                );
              })}
            </ul>
          )}
          {shown.status === 'done' && <p className="text-ink-strong">모든 장치가 서버에 연결되었습니다. 설정 보드의 와이파이는 꺼집니다.</p>}
          {(shown.status === 'failed' || shown.status === 'expired' || shown.status === 'replaced') && (
            <div className="flex flex-wrap items-center gap-2" role="alert">
              <p className="text-danger">
                {shown.status === 'failed'
                  ? `${durationText(shown.timeout_s)} 안에 등록이 끝나지 않았습니다. 큐브 전원을 다시 연결하고 코드를 새로 발급하세요.`
                  : shown.status === 'expired'
                    ? '유효 시간이 지났습니다. 코드를 새로 발급하세요.'
                    : '이 큐브에 새 코드가 발급되어 이 코드는 쓸 수 없습니다.'}
              </p>
              {shown.status !== 'replaced' && (
                <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={() => void issue(shown.sn)}>
                  코드 재발급
                </button>
              )}
            </div>
          )}
        </section>
      )}

      {addingCam && canManage && (
        <form
          className="panel p-4 flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void addCamera();
          }}
        >
          <select value={camCube} onChange={(e) => setCamCube(e.target.value)} aria-label="큐브" className="text-sm">
            {devices.length === 0 && <option value="">큐브 없음</option>}
            {devices.map((d) => (
              <option key={d.id} value={d.id}>
                {d.id}
              </option>
            ))}
          </select>
          <input type="number" min={1} max={99} value={camNo} onChange={(e) => setCamNo(e.target.value)} aria-label="카메라 번호" className="w-20" />
          <input type="text" value={camName} onChange={(e) => setCamName(e.target.value)} placeholder="이름 (선택)" aria-label="카메라 이름" className="rounded border border-line bg-panel px-2 py-1 text-sm" />
          <span className="text-xs text-ink-muted font-mono">{camCube ? `${camCube}-C${camNo || 1}` : ''}</span>
          <button type="submit" className="btn btn-primary btn-sm" disabled={busy || !camCube}>
            {busy ? '추가하는 중…' : '추가'}
          </button>
          {camMsg && (
            <span className="text-sm text-danger" role="alert">
              {camMsg}
            </span>
          )}
        </form>
      )}

      {broker && canManage && (
        <section className="panel p-4 flex flex-col gap-2 text-sm" aria-label="브로커 계정">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-ink-strong">브로커 계정</span>
            <span className={`badge ${broker.ready ? 'badge-ok' : 'badge-warn'}`}>{broker.ready ? '브로커 반영됨' : '반영 대기'}</span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => void loadBroker()} aria-label="다시 불러오기">
              <RefreshCw className="w-4 h-4" aria-hidden="true" />
            </button>
            <button type="button" className="btn btn-ghost btn-sm ml-auto" onClick={rotate}>
              비밀번호 재발급
            </button>
          </div>
          <dl className="kv">
            <dt>주소</dt>
            <dd className="font-mono flex items-center gap-1">
              {broker.host}:{broker.port} {broker.tls ? '(TLS)' : ''}
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => void copyText(broker.host, '주소')} aria-label="주소 복사">
                <Copy className="w-3.5 h-3.5" aria-hidden="true" />
              </button>
            </dd>
            <dt>앱 이름</dt>
            <dd className="font-mono">{broker.app}</dd>
            <dt>사용자</dt>
            <dd className="font-mono flex items-center gap-1">
              {broker.username}
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => void copyText(broker.username, '사용자')} aria-label="사용자 복사">
                <Copy className="w-3.5 h-3.5" aria-hidden="true" />
              </button>
            </dd>
            <dt>비밀번호</dt>
            <dd className="font-mono flex items-center gap-1">
              {broker.password ? (showPw ? broker.password : '•'.repeat(12)) : '—'}
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowPw((v) => !v)} aria-label={showPw ? '비밀번호 숨기기' : '비밀번호 보기'}>
                {showPw ? <EyeOff className="w-3.5 h-3.5" aria-hidden="true" /> : <Eye className="w-3.5 h-3.5" aria-hidden="true" />}
              </button>
              {broker.password && (
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => void copyText(broker.password ?? '', '비밀번호')} aria-label="비밀번호 복사">
                  <Copy className="w-3.5 h-3.5" aria-hidden="true" />
                </button>
              )}
            </dd>
            <dt>큐브</dt>
            <dd className="font-mono">{broker.cubes.join(', ') || '—'}</dd>
          </dl>
          {brokerMsg && (
            <p className="text-sm text-danger" role="alert">
              {brokerMsg}
            </p>
          )}
        </section>
      )}
      {brokerMsg && !broker && (
        <p className="text-sm text-danger" role="alert">
          {brokerMsg}
        </p>
      )}

      {rows.length === 0 && <p className="panel p-6 text-sm text-ink-muted">등록된 장치가 없습니다.</p>}

      {/* 좁은 화면: 장치명과 상태만 보이고, 누르면 세부 정보가 펼쳐진다 */}
      <ul className="device-mobile-list list-scroll md:hidden panel divide-y divide-line" aria-label="장치 목록" tabIndex={0}>
        {rows.map((device) => {
          const status = STATUS_LABEL[device.status];
          const open = expandedId === device.id;
          const detailId = `device-detail-${device.id}`;
          return (
            <li key={device.id}>
              <button
                type="button"
                aria-expanded={open}
                aria-controls={detailId}
                onClick={() => setExpandedId(open ? null : device.id)}
                className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left hover:bg-inset"
              >
                <span className="font-semibold text-ink-strong">
                  {device.name}
                  {device.note && <span className="ml-2 font-normal text-ink-muted">{device.note}</span>}
                </span>
                <span className="flex items-center gap-2 shrink-0">
                  {device.streaming && <span className="badge badge-info">스트리밍</span>}
                  <span className={`badge ${status.cls}`}>{status.text}</span>
                  <ChevronDown className={`w-4 h-4 text-ink-muted ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
                </span>
              </button>
              {open && (
                <div id={detailId} className="px-4 pb-4 space-y-3">
                  <dl className="kv">
                    <dt>ID</dt>
                    <dd>{device.id}</dd>
                    <dt>종류</dt>
                    <dd>{TYPE_LABEL[device.type]}</dd>
                    <dt>IP 주소</dt>
                    <dd>{device.ipAddress}</dd>
                    <dt>MAC 주소</dt>
                    <dd>{device.macAddress}</dd>
                    <dt>펌웨어</dt>
                    <dd>{device.firmwareVersion}</dd>
                    <dt>신호 세기</dt>
                    <dd>{signalText(device.signalStrength)}</dd>
                    <dt title={CHIP_TEMP_NOTE}>칩 온도</dt>
                    <dd title={CHIP_TEMP_NOTE}>{chipTempText(device.chipTempC)}</dd>
                    <dt>마지막 통신</dt>
                    <dd>{device.lastPing}</dd>
                  </dl>
                  <div className="flex justify-end">{deviceMenu(device)}</div>
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {/* 넓은 화면: 표 */}
      <div className="device-table-list list-scroll hidden md:block panel" role="region" aria-label="장치 목록" tabIndex={0}>
        <table className="table">
          <thead>
            <tr>
              <th scope="col">장치</th>
              <th scope="col">ID</th>
              <th scope="col">종류</th>
              <th scope="col">IP 주소</th>
              <th scope="col">MAC 주소</th>
              <th scope="col">펌웨어</th>
              <th scope="col">신호</th>
              <th scope="col" title={CHIP_TEMP_NOTE}>칩 온도</th>
              <th scope="col">마지막 통신</th>
              <th scope="col">상태</th>
              <th scope="col">
                <span className="sr-only">메뉴</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((device) => {
              const status = STATUS_LABEL[device.status];
              return (
                <tr key={device.id}>
                  <td className="font-semibold text-ink-strong whitespace-nowrap">
                    {device.name}
                    {device.note && <span className="ml-2 font-normal text-ink-muted">{device.note}</span>}
                  </td>
                  <td className="whitespace-nowrap">{device.id}</td>
                  <td className="whitespace-nowrap">{TYPE_LABEL[device.type]}</td>
                  <td className="whitespace-nowrap">{device.ipAddress}</td>
                  <td className="whitespace-nowrap text-ink-muted">{device.macAddress}</td>
                  <td className="whitespace-nowrap">{device.firmwareVersion}</td>
                  <td className="whitespace-nowrap">{signalText(device.signalStrength)}</td>
                  <td className="whitespace-nowrap" title={CHIP_TEMP_NOTE}>{chipTempText(device.chipTempC)}</td>
                  <td className="whitespace-nowrap text-ink-muted">{device.lastPing}</td>
                  <td className="whitespace-nowrap">
                    <span className={`badge ${status.cls}`}>{status.text}</span>
                    {device.streaming && <span className="badge badge-info ml-1">스트리밍</span>}
                  </td>
                  <td className="text-right">{deviceMenu(device)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};
