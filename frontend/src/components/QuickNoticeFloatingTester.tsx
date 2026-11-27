import React, { useState } from 'react';
import { X } from 'lucide-react';
import { ZoneConfig } from '../types/smartfarm';
import { AG, EdgeNotice } from '../lib/agToolkit';

interface QuickNoticeFloatingTesterProps {
  zones: ZoneConfig[];
  onSimulateAlert: (zoneId: string, alertType: string) => void;
}

// 개발자 도구: 알림 API와 큐브 경보를 한 번에 띄워 보는 떠 있는 패널
export const QuickNoticeFloatingTester: React.FC<QuickNoticeFloatingTesterProps> = ({ zones = [], onSimulateAlert }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [zoneId, setZoneId] = useState<string>(zones[0]?.id ?? 'zone-1');

  return (
    <aside aria-label="테스트 알림 도구" className="fixed bottom-6 left-6 z-50">
      {isOpen ? (
        <div className="panel p-4 w-80 space-y-3 shadow-lg">
          <div className="flex items-center justify-between border-b border-line pb-2">
            <h4 className="text-sm font-semibold text-ink-strong">테스트 알림</h4>
            <button type="button" onClick={() => setIsOpen(false)} className="btn btn-ghost btn-sm" aria-label="닫기">
              <X className="w-4 h-4" aria-hidden="true" />
            </button>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <button type="button" onClick={() => AG.Notification.Toast('간단 알림. 3초 뒤 사라집니다.', 3000)} className="btn btn-secondary btn-sm">
              Toast
            </button>
            <button
              type="button"
              onClick={() => AG.Notification.InfoMessage('정보 알림', '센서 측정 주기를 10초로 맞췄습니다.', 3000)}
              className="btn btn-secondary btn-sm"
            >
              InfoMessage
            </button>
            <button
              type="button"
              onClick={() =>
                EdgeNotice.dangerAlert(
                  'SC-01 고온 경보',
                  '내부 온도가 35°C를 넘어 작물 열해 위험이 있습니다. 배기팬을 3단으로 올릴까요?',
                  () => EdgeNotice.toast('SC-01 배기팬을 3단으로 가동했습니다.'),
                  '3단 가동'
                )
              }
              className="btn btn-secondary btn-sm"
            >
              긴급 확인
            </button>
            <button
              type="button"
              onClick={() =>
                AG.Notification.DiscoverMessage({
                  clientId: 'NODE-SN-882',
                  name: 'SC-02 배지 수분 센서',
                  type: 'RS-485 배지 센서',
                })
              }
              className="btn btn-secondary btn-sm"
            >
              장치 발견
            </button>
          </div>

          <div className="border-t border-line pt-3 space-y-2">
            <label className="flex items-center gap-2 text-sm">
              <span className="text-ink-muted">큐브 경보</span>
              <select value={zoneId} onChange={(e) => setZoneId(e.target.value)} className="flex-1">
                {zones.map((z) => (
                  <option key={z.id} value={z.id}>
                    {z.code} {z.crop}
                  </option>
                ))}
              </select>
            </label>
            <div className="grid grid-cols-3 gap-2">
              <button type="button" onClick={() => onSimulateAlert(zoneId, 'high_temp')} className="btn btn-secondary btn-sm">
                고온
              </button>
              <button type="button" onClick={() => onSimulateAlert(zoneId, 'humidity_trap')} className="btn btn-secondary btn-sm">
                과습
              </button>
              <button type="button" onClick={() => onSimulateAlert(zoneId, 'ec_spike')} className="btn btn-secondary btn-sm">
                EC 급상승
              </button>
            </div>
          </div>
        </div>
      ) : (
        <button type="button" onClick={() => setIsOpen(true)} className="btn btn-secondary shadow-lg">
          테스트 알림
        </button>
      )}
    </aside>
  );
};
