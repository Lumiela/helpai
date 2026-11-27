import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Check, ChevronRight, Droplets, Eye, Fan, Lightbulb, LogOut, Sprout, WifiOff, X, Settings, OctagonAlert, Sun, Thermometer, Waves,
} from 'lucide-react';
import type { NotificationLogItem } from '../types/smartfarm';
import { getSnapshotBlob, getSnapshots, type CropProfile, type SnapshotRow } from '../lib/backend';
import type { AppMenuItem } from '../lib/appMenu';
import { extraMetricsOf, extraText, hasChart } from '../lib/extraMetrics';
import { isOff, levelOf, readLocal, today, val, word, writeLocal, type LocalData, type MetricKey } from './data';
import { IconCircle, LineChart, ReportCard } from './shared';
import type { Ctx } from './SmartCubeApp';

// ---------- 화면 3: 돌보기 ----------
// 센서 값으로 해결된 일은 자동 체크, 관찰하기처럼 사람이 하는 일은 직접 체크(이 기기에만 저장).
export const CareScreen: React.FC<{ ctx: Ctx; embedded?: boolean }> = ({ ctx, embedded = false }) => {
  const { zone, m, a } = ctx;
  const [local, setLocal] = useState<LocalData>(() => readLocal(zone.id));
  const observed = local.observed === today();
  const soilLv = levelOf(m.soil.value, m.soil.range);
  const lightLv = levelOf(m.light.value, m.light.range);
  const live = zone.live;
  const tasks: Array<{ key: string; icon: React.ElementType; title: string; status: string; done: boolean; manual?: boolean; action?: { label: string; run: () => void } }> = [
    {
      key: 'water', icon: Droplets, title: '물 주기',
      status: !live ? '상태 확인 불가' : soilLv === 'low' ? `흙이 말랐어요 (${val(m.soil)}%)` : soilLv === 'none' ? '흙 센서 값이 없어요' : `흙이 ${word(m.soil)} (${val(m.soil)}%)`,
      done: live && soilLv !== 'low' && soilLv !== 'none',
      action: live && soilLv === 'low' ? { label: '물 주기', run: ctx.askWater } : undefined,
    },
    {
      key: 'light', icon: Lightbulb, title: '빛 확인',
      status: !live ? '상태 확인 불가' : a.ledGrowLight ? `조명 켜짐 ${a.ledIntensity}% · 빛 ${word(m.light)}` : `조명 꺼짐 · 빛 ${word(m.light)}`,
      done: live && (lightLv === 'ok' || (a.ledGrowLight && lightLv !== 'low')),
      action: live && lightLv === 'low' && !a.ledGrowLight ? { label: '조명 켜기', run: () => ctx.send('led', 70, { ledGrowLight: true, ledIntensity: 70 }) } : undefined,
    },
    {
      key: 'vent', icon: Fan, title: '환기',
      status: !live ? '상태 확인 불가' : isOff(m.temp) || isOff(m.air) ? `온도 ${word(m.temp)} · 습도 ${word(m.air)}` : '온도와 습도가 좋아요',
      done: live && !isOff(m.temp) && !isOff(m.air) && m.temp.value !== null,
      action: live && (isOff(m.temp) || isOff(m.air)) && !a.outerFan ? { label: '환기 켜기', run: () => ctx.send('outer', 2, { outerFan: true, outerFanSpeed: 2 }) } : undefined,
    },
    { key: 'observe', icon: Eye, title: '관찰하기', status: observed ? '오늘 관찰했어요' : '새 잎, 꽃봉오리, 잎 색을 살펴봐요', done: observed, manual: true },
  ];
  const doneCount = tasks.filter((t) => t.done).length;
  return (
    <section className={embedded ? "a2-today" : "a2-screen"} aria-labelledby="a2-care-title">
      {embedded
        ? <div className="a2-row-between"><h2 id="a2-care-title" className="a2-h2">오늘 할 일</h2><small className="a2-label">{doneCount} / {tasks.length} 완료</small></div>
        : <><h1 id="a2-care-title" className="a2-title-sm">오늘 <span>돌보기</span></h1><p className="a2-sub">{doneCount} / {tasks.length} 완료 · 센서로 확인된 일은 저절로 체크돼요</p></>}
      {ctx.lockReason && !embedded && <p className="a2-lock">{ctx.lockReason}</p>}
      <ul className="a2-tasks">
        {tasks.map((t) => (
          <li key={t.key} className={`a2-card a2-task ${t.done ? 'is-done' : ''}`}>
            <IconCircle icon={t.icon} tone={t.done ? 'lime' : 'soft'} />
            <div className="min-w-0 flex-1">
              <p><b>{t.title}</b>{t.manual && <span className="a2-tag-sm">직접 체크</span>}</p>
              <small className="a2-label">{t.status}</small>
              {t.action && !t.done && <button type="button" className="a2-btn-chip" disabled={ctx.lockReason !== null} onClick={t.action.run}>{t.action.label}</button>}
            </div>
            {t.manual
              ? <button type="button" role="checkbox" aria-checked={t.done} className={`a2-check ${t.done ? 'is-on' : ''}`} aria-label={`${t.title} ${t.done ? '체크 해제' : '체크'}`}
                onClick={() => setLocal(writeLocal(zone.id, { observed: observed ? undefined : today() }))}><Check aria-hidden="true" strokeWidth={2} /></button>
              : <span className={`a2-check ${t.done ? 'is-on' : ''}`} role="img" aria-label={t.done ? '완료' : '아직'}><Check aria-hidden="true" strokeWidth={2} /></span>}
          </li>
        ))}
      </ul>
      <p className="a2-note">센서로 확인된 일은 저절로 체크돼요. 관찰하기 체크는 이 기기에만 저장돼요.</p>
    </section>
  );
};

// ---------- 화면 4: 기록 ----------
export const RecordsScreen: React.FC<{ ctx: Ctx }> = ({ ctx }) => {
  const { zone, m } = ctx;
  const [local, setLocal] = useState<LocalData>(() => readLocal(zone.id));
  const days = local.startDate ? Math.max(1, Math.ceil((Date.now() - Date.parse(local.startDate)) / 86_400_000)) : null;
  const PERIODS = [
    { key: '-24h', label: '24시간', every: '15m' },
    { key: '-7d', label: '7일', every: '2h' },
    { key: days ? `-${days}d` : '-30d', label: days ? '재배 전체' : '재배 전체(30일)', every: '6h' },
  ];
  const [pi, setPi] = useState(0);
  const extras = extraMetricsOf(zone); // EC·CO₂(그래프) · pH(지금 값)
  const charts = [...(['soil', 'temp', 'air', 'light'] as MetricKey[]).map((x) => m[x]), ...extras.filter(hasChart)];
  const [k, setK] = useState<string>('soil');
  const [note, setNote] = useState('');
  const [sown, setSown] = useState(local.germination?.sown?.toString() ?? '');
  const [sprouted, setSprouted] = useState(local.germination?.sprouted?.toString() ?? '');
  const rate = local.germination?.sown && local.germination.sprouted !== null && local.germination?.sprouted !== undefined
    ? Math.round((local.germination.sprouted / local.germination.sown) * 100) : null;
  return (
    <section className="a2-screen" aria-labelledby="a2-rec-title">
      <h1 id="a2-rec-title" className="a2-title-sm">성장 <span>일지</span></h1>
      <p className="a2-sub">{days ? `키운 지 ${days}일째` : '재배 시작일은 재배 설정에서 정할 수 있어요'}</p>

      <div className="a2-card">
        <div className="a2-chips a2-chips-sm" role="radiogroup" aria-label="기간">
          {PERIODS.map((x, i) => <button key={x.label} type="button" role="radio" aria-checked={pi === i} className={pi === i ? 'is-on' : ''} onClick={() => setPi(i)}>{x.label}</button>)}
        </div>
        <div className="a2-chips a2-chips-sm a2-chips-ghost" role="radiogroup" aria-label="항목">
          {charts.map((x) => <button key={x.key} type="button" role="radio" aria-checked={k === x.key} className={k === x.key ? 'is-on' : ''} onClick={() => setK(x.key)}>{x.label}</button>)}
        </div>
        <LineChart sn={zone.id} metric={charts.find((x) => x.key === k) ?? charts[0]} start={PERIODS[pi].key} every={PERIODS[pi].every} />
        <p className="a2-note">지금 {extras.map((x) => `${x.label} ${extraText(x)}`).join(' · ')}</p>
      </div>

      <ReportCard sn={zone.id} />

      <Photos sn={zone.id} />

      <div className="a2-card">
        <h2 className="a2-h2">관찰 메모</h2>
        <form className="a2-note-form" onSubmit={(e) => {
          e.preventDefault();
          if (!note.trim()) return;
          setLocal(writeLocal(zone.id, { notes: [{ id: String(Date.now()), at: Date.now(), text: note.trim() }, ...(local.notes ?? [])].slice(0, 100) }));
          setNote('');
        }}>
          <textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} rows={3} placeholder="오늘 본 변화를 적어 보세요. 예) 새 잎이 두 장 났어요" aria-label="관찰 메모" />
          <button type="submit" className="a2-btn-lime" disabled={!note.trim()}>메모 남기기</button>
        </form>
        {(local.notes ?? []).length === 0 ? <p className="a2-note">아직 메모가 없어요.</p> : (
          <ul className="a2-notes">
            {(local.notes ?? []).map((n) => (
              <li key={n.id}>
                <time>{new Date(n.at).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</time>
                <p>{n.text}</p>
                <button type="button" aria-label="메모 지우기" onClick={() => setLocal(writeLocal(zone.id, { notes: (local.notes ?? []).filter((x) => x.id !== n.id) }))}><X aria-hidden="true" /></button>
              </li>
            ))}
          </ul>
        )}
        <p className="a2-note">메모는 이 기기에만 저장돼요.</p>
      </div>

      <div className="a2-card">
        <h2 className="a2-h2">발아 기록</h2>
        <form className="a2-germ" onSubmit={(e) => {
          e.preventDefault();
          const s = Number(sown), g = Number(sprouted);
          if (!Number.isInteger(s) || s < 1 || !Number.isInteger(g) || g < 0 || g > s) return;
          setLocal(writeLocal(zone.id, { germination: { sown: s, sprouted: g, at: Date.now() } }));
        }}>
          <label>심은 씨앗<input inputMode="numeric" value={sown} onChange={(e) => setSown(e.target.value.replace(/\D/g, ''))} placeholder="0" /></label>
          <label>싹 난 수<input inputMode="numeric" value={sprouted} onChange={(e) => setSprouted(e.target.value.replace(/\D/g, ''))} placeholder="0" /></label>
          <button type="submit" className="a2-btn-chip">저장</button>
        </form>
        <p className="a2-germ-rate"><b>{rate === null ? '—' : `${rate}%`}</b> 발아율{local.germination?.at ? ` · ${new Date(local.germination.at).toLocaleDateString('ko-KR')} 기록` : ''}</p>
        <p className="a2-note">싹 난 수는 심은 씨앗보다 많을 수 없어요. 이 기기에만 저장돼요.</p>
      </div>
    </section>
  );
};

// 날짜별 카메라 사진 (실제 촬영본만)
const Photos: React.FC<{ sn: string }> = ({ sn }) => {
  const [rows, setRows] = useState<SnapshotRow[] | null>(null);
  const [urls, setUrls] = useState<Record<string, string>>({});
  useEffect(() => {
    let alive = true;
    const made: string[] = [];
    getSnapshots(sn, { limit: 12 })
      .then(async (list) => {
        if (!alive) return;
        setRows(list.rows);
        for (const r of list.rows.slice(0, 8)) {
          try {
            const u = URL.createObjectURL(await getSnapshotBlob(sn, r.camera, r.date, r.file));
            made.push(u);
            if (alive) setUrls((prev) => ({ ...prev, [r.path]: u }));
          } catch { /* 한 장 실패는 건너뛴다 */ }
        }
      })
      .catch(() => { if (alive) setRows([]); });
    return () => { alive = false; made.forEach((u) => URL.revokeObjectURL(u)); };
  }, [sn]);
  const byDate = (rows ?? []).reduce<Record<string, SnapshotRow[]>>((acc, r) => { (acc[r.date] ??= []).push(r); return acc; }, {});
  return (
    <div className="a2-card">
      <h2 className="a2-h2">날짜별 사진</h2>
      {rows === null ? <p className="a2-note">사진을 불러오는 중…</p>
        : rows.length === 0 ? <p className="a2-note">아직 찍은 사진이 없어요. 천장 카메라가 찍으면 여기에 모여요.</p>
          : (Object.entries(byDate) as Array<[string, SnapshotRow[]]>).map(([date, list]) => (
            <div key={date} className="a2-photos">
              <small className="a2-label">{date}</small>
              <div className="a2-photo-row">
                {list.map((r) => urls[r.path]
                  ? <img key={r.path} src={urls[r.path]} alt={`${date} 큐브 사진`} />
                  : <span key={r.path} className="a2-photo-wait" />)}
              </div>
            </div>
          ))}
    </div>
  );
};

// ---------- 화면 5: 알림 ----------
// 지금 상태에서 나온 알림(식물이 말하는 말투 + 바로 실행) + 서버·화면이 남긴 알림 이력
export const AlertsScreen: React.FC<{ ctx: Ctx; logs: NotificationLogItem[] }> = ({ ctx, logs }) => {
  const { zone, m, a } = ctx;
  const now: Array<{ key: string; icon: React.ElementType; say: string; check: string; todo: string; action?: { label: string; run: () => void } }> = [];
  if (!zone.live) now.push({ key: 'off', icon: WifiOff, say: '큐브와 연락이 안 돼요 📡', check: '큐브 전원이 켜져 있는지, 와이파이가 연결됐는지 확인해 주세요.', todo: '다시 연결되면 자동으로 알림이 사라져요.' });
  if (zone.live && levelOf(m.soil.value, m.soil.range) === 'low') now.push({ key: 'dry', icon: Droplets, say: '목이 말라요, 물 주세요 💧', check: `흙 촉촉함 ${val(m.soil)}% · 좋아하는 범위 ${m.soil.range.min}~${m.soil.range.max}%`, todo: '물을 한 번 주고 30분 뒤 흙 촉촉함이 올라갔는지 봐 주세요.', action: { label: '물 주기', run: ctx.askWater } });
  if (zone.live && levelOf(m.temp.value, m.temp.range) === 'high') now.push({ key: 'hot', icon: Thermometer, say: '조금 더워요, 바람을 켜 줄래요? 🌬️', check: `온도 ${val(m.temp)}°C · 좋아하는 범위 ${m.temp.range.min}~${m.temp.range.max}°C`, todo: a.outerFan ? '환기팬이 이미 돌고 있어요. 10분쯤 뒤 온도가 내려갔는지 봐 주세요.' : '환기팬으로 더운 공기를 내보내 주세요.', action: a.outerFan ? undefined : { label: '환기 켜기', run: () => ctx.send('outer', 2, { outerFan: true, outerFanSpeed: 2 }) } });
  if (zone.live && levelOf(m.air.value, m.air.range) === 'high') now.push({ key: 'humid', icon: Waves, say: '여기 좀 습해요 😮‍💨', check: `습도 ${val(m.air)}% · 좋아하는 범위 ${m.air.range.min}~${m.air.range.max}%`, todo: a.outerFan ? '환기팬이 이미 돌고 있어요. 10분쯤 뒤 습도가 내려갔는지 봐 주세요.' : '환기하면 곰팡이를 막을 수 있어요.', action: a.outerFan ? undefined : { label: '환기 켜기', run: () => ctx.send('outer', 2, { outerFan: true, outerFanSpeed: 2 }) } });
  if (zone.live && levelOf(m.light.value, m.light.range) === 'low' && !a.ledGrowLight) now.push({ key: 'dark', icon: Sun, say: '어두워요, 불 좀 켜 줄래요? 💡', check: `빛 ${val(m.light)}klx · 좋아하는 범위 ${m.light.range.min}~${m.light.range.max}klx`, todo: '조명을 켜 주세요.', action: { label: '조명 켜기', run: () => ctx.send('led', 70, { ledGrowLight: true, ledIntensity: 70 }) } });
  const history = logs.slice(0, 30);
  return (
    <section className="a2-screen" aria-labelledby="a2-alert-title">
      <h1 id="a2-alert-title" className="a2-title-sm">식물이 <span>하는 말</span></h1>
      <p className="a2-sub">지금 상태를 보고 바로 알려 줘요</p>
      {now.length === 0 && <div className="a2-card a2-calm"><IconCircle icon={Sprout} tone="lime" /><p><b>지금은 괜찮아요 🌱</b><br /><small className="a2-label">새 알림이 생기면 여기에 보여요.</small></p></div>}
      <ul className="a2-alerts">
        {now.map((x) => (
          <li key={x.key} className="a2-card a2-alert">
            <div className="a2-alert-head"><IconCircle icon={x.icon} tone="warn" /><p><b>{x.say}</b><small className="a2-label">지금</small></p></div>
            <p className="a2-alert-line"><span>확인할 것</span>{x.check}</p>
            <p className="a2-alert-line"><span>이렇게 해요</span>{x.todo}</p>
            {x.action && <button type="button" className="a2-btn-lime" disabled={ctx.lockReason !== null} onClick={x.action.run}>{x.action.label}</button>}
          </li>
        ))}
      </ul>
      <h2 className="a2-h2 a2-mt">지난 알림</h2>
      {history.length === 0 ? <p className="a2-note">아직 지난 알림이 없어요. 급수 실패·누수·연결 끊김 같은 알림이 생기면 여기에 쌓여요.</p> : (
        <ul className="a2-history">
          {history.map((l) => (
            <li key={l.id} className={`a2-hist a2-hist-${l.severity}`}>
              <span className="a2-hist-dot" aria-hidden="true" />
              <div className="min-w-0">
                <p><b>{l.title ?? '알림'}</b> <time>{l.timestamp}</time></p>
                <small className="a2-label">{l.content.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 140)}</small>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};

// ---------- 메뉴 ----------
export const MenuSheet: React.FC<{ onClose: () => void; onSettings: () => void; onEmergencyStop: () => void; canControl: boolean; items: AppMenuItem[]; canSignOut: boolean; onSignOut: () => void }> = ({
  onClose, onSettings, onEmergencyStop, canControl, items, canSignOut, onSignOut,
}) => (
  <div className="a2-sheet-back" role="presentation" onClick={onClose}>
    <div className="a2-sheet" role="dialog" aria-modal="true" aria-label="메뉴" onClick={(e) => e.stopPropagation()}>
      <div className="a2-row-between"><h3>메뉴</h3><button type="button" className="a2-round a2-round-sm" onClick={onClose} aria-label="닫기"><X aria-hidden="true" /></button></div>
      <button type="button" className="a2-menu-item" onClick={onSettings}><IconCircle icon={Settings} tone="soft" size="sm" />재배 설정<small>작물 · 시작일 · 목표 환경</small></button>
      {items.map((x) => <Link key={x.key} className="a2-menu-item" to={x.to}><IconCircle icon={ChevronRight} tone="soft" size="sm" />{x.label}<small>{x.sub}</small></Link>)}
      <button type="button" className="a2-menu-item is-danger" onClick={onEmergencyStop} disabled={!canControl}><IconCircle icon={OctagonAlert} tone="warn" size="sm" />긴급 정지<small>조명·팬·급수를 모두 꺼요</small></button>
      {canSignOut && <button type="button" className="a2-menu-item" onClick={onSignOut}><IconCircle icon={LogOut} tone="soft" size="sm" />로그아웃</button>}
    </div>
  </div>
);

// ---------- 재배 설정 ----------
export const SettingsSheet: React.FC<{ ctx: Ctx; crops: CropProfile[]; onClose: () => void; onSetCrop: (id: string | null) => void }> = ({ ctx, crops, onClose, onSetCrop }) => {
  const { zone } = ctx;
  const [local, setLocal] = useState<LocalData>(() => readLocal(zone.id));
  const [pick, setPick] = useState<string>(zone.cropId ?? '');
  const crop = crops.find((c) => c.id === pick);
  const r = (x: { min: number; max: number } | undefined, unit: string, scale = 1) => (x ? `${Math.round(x.min * scale * 10) / 10}~${Math.round(x.max * scale * 10) / 10}${unit}` : '—');
  return (
    <div className="a2-sheet-back" role="presentation" onClick={onClose}>
      <div className="a2-sheet a2-settings" role="dialog" aria-modal="true" aria-labelledby="a2-set-title" onClick={(e) => e.stopPropagation()}>
        <div className="a2-row-between"><h3 id="a2-set-title">재배 설정</h3><button type="button" className="a2-round a2-round-sm" onClick={onClose} aria-label="닫기"><X aria-hidden="true" /></button></div>
        <label className="a2-field">작물
          <select value={pick} disabled={!ctx.canControl} onChange={(e) => setPick(e.target.value)}>
            <option value="">고르지 않음 (일반 기준)</option>
            {crops.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
        <label className="a2-field">재배 시작일
          <input type="date" value={local.startDate ?? ''} max={today()} onChange={(e) => setLocal(writeLocal(zone.id, { startDate: e.target.value || undefined }))} />
          <small className="a2-label">시작일은 이 기기에만 저장돼요.</small>
        </label>
        <div className="a2-targets">
          <p className="a2-label">목표 환경 {crop ? `· ${crop.name} 프로필 값` : '· 작물을 고르면 그 작물의 값으로 채워져요'}</p>
          <ul>
            <li><span>낮 온도</span><b>{r(crop?.temp_day, '°C')}</b></li>
            <li><span>밤 온도</span><b>{r(crop?.temp_night, '°C')}</b></li>
            <li><span>습도(낮)</span><b>{r(crop?.humidity_day, '%')}</b></li>
            <li><span>흙 수분</span><b>{r(crop?.soil_moisture, '%')}</b></li>
            <li><span>빛(낮)</span><b>{r(crop?.lux_day, 'klx', 1 / 1000)}</b></li>
            <li><span>조명 켜고 끄기</span><b>{crop ? `${crop.photoperiod.on} ~ ${crop.photoperiod.off}` : '—'}</b></li>
          </ul>
          <small className="a2-label">목표 값을 직접 바꾸는 기능은 아직 서버에 없어요. 지금은 작물 프로필 값을 그대로 써요.</small>
        </div>
        <div className="a2-sheet-btns">
          <button type="button" className="a2-btn-soft" onClick={onClose}>닫기</button>
          <button type="button" className="a2-btn-lime" disabled={!ctx.canControl || pick === (zone.cropId ?? '')} onClick={() => { onSetCrop(pick || null); onClose(); }}>작물 저장</button>
        </div>
      </div>
    </div>
  );
};
