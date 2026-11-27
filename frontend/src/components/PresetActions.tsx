import { useEffect, useRef, useState } from 'react';
import { ActuatorState, ZoneConfig } from '../types/smartfarm';
import { EdgeNotice } from '../lib/agToolkit';

import { STORAGE_KEY, SavedPreset, readPresets, pickSettings } from '../lib/presets';

export function PresetActions({ zone, actuator, onLoad }: {
  zone: ZoneConfig;
  actuator: ActuatorState;
  onLoad: (zoneId: string, settings: Partial<ActuatorState>) => void;
}) {
  const [initial] = useState(() => {
    try { return { presets: readPresets(), error: '' }; }
    catch { return { presets: [] as SavedPreset[], error: '저장된 설정을 읽지 못했습니다. 브라우저 저장소를 확인해 주세요.' }; }
  });
  const [presets, setPresets] = useState(initial.presets);
  const [error, setError] = useState(initial.error);
  const [name, setName] = useState('');
  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialogRef.current?.close();
    setName('');
  }, [zone.id]);
  const zonePresets = presets.filter((preset) => preset.zoneId === zone.id);

  const save = () => {
    try {
      const previous = readPresets();
      const number = previous.filter((preset) => preset.zoneId === zone.id).length + 1;
      const preset: SavedPreset = {
        id: crypto.randomUUID(), zoneId: zone.id,
        name: name.trim() || `${zone.code} 설정 ${number}`,
        settings: pickSettings(actuator),
      };
      const next = [...previous, preset];
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      setPresets(next);
      setError('');
      dialogRef.current?.close();
      EdgeNotice.toast(`${preset.name}을 저장했습니다.`);
    } catch {
      setError('설정을 저장하지 못했습니다. 브라우저 저장소를 확인해 주세요.');
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2 min-w-0">
      <button type="button" className="btn btn-primary btn-sm" onClick={() => {
        setName('');
        dialogRef.current?.showModal();
      }}>설정 저장</button>
      <dialog ref={dialogRef} aria-labelledby="preset-save-title" className="panel m-auto p-5 w-full max-w-sm backdrop:bg-ink/40">
        <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); save(); }}>
          <h3 id="preset-save-title" className="text-lg font-semibold text-ink-strong">설정 저장</h3>
          <div className="field">
            <label htmlFor="preset-name">프리셋 이름 (선택)</label>
            <input id="preset-name" type="text" autoFocus maxLength={80} value={name}
              onChange={(event) => setName(event.target.value)} placeholder={`${zone.code} 설정 ${zonePresets.length + 1}`} />
            <p className="text-sm text-ink-muted mt-2">비워두면 이름을 자동으로 지정합니다.</p>
          </div>
          {error && <p role="alert" className="text-sm text-danger">{error}</p>}
          <div className="flex justify-end gap-2">
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => dialogRef.current?.close()}>취소</button>
            <button type="submit" className="btn btn-primary btn-sm">저장</button>
          </div>
        </form>
      </dialog>
      <select
        aria-label="저장된 설정 불러오기"
        className="min-w-0 max-w-full text-sm"
        value=""
        disabled={zonePresets.length === 0}
        onChange={(event) => {
          const preset = zonePresets.find((item) => item.id === event.target.value);
          if (!preset) return;
          onLoad(zone.id, { ...pickSettings(preset.settings), activePresetName: preset.name, activePresetId: preset.id });
          EdgeNotice.toast(`${preset.name}을 불러왔습니다.`);
        }}
      >
        <option value="">{zonePresets.length ? '설정 불러오기' : '저장된 설정 없음'}</option>
        {zonePresets.map((preset) => <option key={preset.id} value={preset.id}>{preset.name}</option>)}
      </select>
      {error && <p role="alert" className="w-full text-sm text-danger">{error}</p>}
    </div>
  );
}
