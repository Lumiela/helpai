import React, { useState } from 'react';
import { ZoneConfig, ActuatorState } from '../types/smartfarm';
import { EdgeNotice, AG, AGIcon, formatKoreanTime } from '../lib/agToolkit';
import { PresetActions } from './PresetActions';
import { DamperControl } from './DamperControl';
import { DEFAULT_ACTUATOR } from '../lib/deviceMap';

interface ActuatorControlProps {
  zones: ZoneConfig[];
  actuators: Record<string, ActuatorState>;
  onUpdateActuator: (zoneId: string, updates: Partial<ActuatorState>) => void;
}

const Row: React.FC<{ title: string; desc: string; children: React.ReactNode }> = ({ title, desc, children }) => (
  <div className="flex items-center justify-between gap-3 bg-surface border border-line rounded-lg px-3 py-2.5">
    <div>
      <div className="text-sm font-semibold text-ink">{title}</div>
      <div className="text-xs text-ink-muted">{desc}</div>
    </div>
    {children}
  </div>
);

export const ActuatorControl: React.FC<ActuatorControlProps> = ({ zones, actuators, onUpdateActuator }) => {
  const [chosenZoneId, setSelectedZoneId] = useState<string>('');
  const activeZone = zones.find((z) => z.id === chosenZoneId) || zones[0];
  if (!activeZone) {
    return (
      <div className="space-y-6">
        <h2 className="text-lg font-semibold text-ink-strong">프리셋</h2>
        <p className="panel p-6 text-sm text-ink-muted">연결된 큐브가 없습니다.</p>
      </div>
    );
  }
  const selectedZoneId = activeZone.id;
  const a = actuators[selectedZoneId] ?? DEFAULT_ACTUATOR;

  const applyMaxVentilation = () => {
    onUpdateActuator(selectedZoneId, { outerFan: true, outerFanSpeed: 3, innerFan: true, innerFanSpeed: 3, activePresetName: '최대 환기' });
    EdgeNotice.toast(`${activeZone.code} 배기팬과 순환팬을 3단으로 가동했습니다.`);
  };

  const applyMaxLight = () => {
    onUpdateActuator(selectedZoneId, { ledMode: 'day', ledGrowLight: true, ledIntensity: 100, activePresetName: '최대 광량' });
    EdgeNotice.info(`${activeZone.code} 최대 광량`, '주간 모드로 전환하고 LED 출력을 100%로 올렸습니다.');
  };

  const applyNightPreset = () => {
    const markup = `
      <group-row class="background-white">
        <row-start class="background-indigo-light padding-4">
          <span class="ag-icon">${AGIcon.moon}</span>
          <div class="text-bold">${activeZone.code} 야간 모드 전환</div>
          <span class="margin-left-auto text-time">${formatKoreanTime()}</span>
        </row-start>
        <div class="padding-6">
          <p class="message-body">배기팬 정지, 광 주기 야간, LED 소등, 순환팬 1단으로 한 번에 바꿉니다.</p>
          <row-between class="margin-top-4 justify-end">
            <button type="button" data-action="close" class="button button-small color-gray">취소</button>
            <button type="button" data-action="apply-preset" class="button button-small color-indigo">야간 모드 적용</button>
          </row-between>
        </div>
      </group-row>
    `;

    AG.Notification.Message(markup, function () {
      const item = this;
      const dismiss = () => {
        item.classList.add('slide-out');
        setTimeout(() => item.remove(), 200);
      };
      item.querySelector('[data-action="apply-preset"]')?.addEventListener(
        'click',
        () => {
          onUpdateActuator(selectedZoneId, {
            outerFan: false,
            outerFanSpeed: 0,
            ledMode: 'night',
            ledGrowLight: false,
            ledIntensity: 0,
            innerFan: true,
            innerFanSpeed: 1,
            activePresetName: '야간 모드',
          });
          dismiss();
          EdgeNotice.info(`${activeZone.code} 야간 모드 적용`, '배기팬을 정지하고 LED를 소등했습니다. 순환팬은 1단으로 유지합니다.');
        },
        { once: true }
      );
      item.querySelector('[data-action="close"]')?.addEventListener('click', dismiss, { once: true });
    });
  };

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex flex-wrap items-center gap-3 min-w-0">
          <h2 className="text-lg font-semibold text-ink-strong">프리셋</h2>
          <PresetActions zone={activeZone} actuator={a} onLoad={onUpdateActuator} />
        </div>
        <div className="seg max-w-full overflow-x-auto" role="group" aria-label="큐브 선택">
          {zones.map((zone) => (
            <button key={zone.id} type="button" aria-pressed={selectedZoneId === zone.id} onClick={() => setSelectedZoneId(zone.id)}>
              {zone.code} {zone.crop}
            </button>
          ))}
        </div>
      </header>

      <section aria-label="한 번에 적용" className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <button type="button" onClick={applyNightPreset} className="panel p-4 text-left hover:bg-inset">
          <div className="text-sm font-semibold text-ink-strong">야간 모드</div>
          <div className="text-sm text-ink-muted mt-1">배기팬 정지, LED 소등, 순환팬 1단</div>
        </button>
        <button type="button" onClick={applyMaxVentilation} className="panel p-4 text-left hover:bg-inset">
          <div className="text-sm font-semibold text-ink-strong">최대 환기</div>
          <div className="text-sm text-ink-muted mt-1">배기팬과 순환팬 3단</div>
        </button>
        <button type="button" onClick={applyMaxLight} className="panel p-4 text-left hover:bg-inset">
          <div className="text-sm font-semibold text-ink-strong">최대 광량</div>
          <div className="text-sm text-ink-muted mt-1">주간 모드, LED 100%</div>
        </button>
      </section>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
        <section className="panel p-5 space-y-3" aria-labelledby="fans-title">
          <div className="flex items-center justify-between">
            <h3 id="fans-title" className="text-base font-semibold text-ink-strong">
              팬
            </h3>
          </div>
          <Row title="배기팬" desc="바깥 공기와 교환">
            <div className="seg" role="group" aria-label="배기팬 단수">
              <button type="button" aria-pressed={!a.outerFan} onClick={() => onUpdateActuator(selectedZoneId, { outerFan: false, outerFanSpeed: 0 })}>
                정지
              </button>
              {[1, 2, 3].map((n) => (
                <button
                  key={n}
                  type="button"
                  aria-pressed={a.outerFan && a.outerFanSpeed === n}
                  onClick={() => onUpdateActuator(selectedZoneId, { outerFan: true, outerFanSpeed: n })}
                >
                  {n}단
                </button>
              ))}
            </div>
          </Row>
          <Row title="순환팬" desc="내부 온습도 편차 해소">
            <div className="seg" role="group" aria-label="순환팬 단수">
              <button type="button" aria-pressed={!a.innerFan} onClick={() => onUpdateActuator(selectedZoneId, { innerFan: false, innerFanSpeed: 0 })}>
                정지
              </button>
              {[1, 2, 3].map((n) => (
                <button
                  key={n}
                  type="button"
                  aria-pressed={a.innerFan && a.innerFanSpeed === n}
                  onClick={() => onUpdateActuator(selectedZoneId, { innerFan: true, innerFanSpeed: n })}
                >
                  {n}단
                </button>
              ))}
            </div>
          </Row>
        </section>

        <section className="panel p-5 space-y-3" aria-labelledby="light-title">
          <h3 id="light-title" className="text-base font-semibold text-ink-strong">
            조명
          </h3>
          <Row title="광 주기" desc="작물 일장 제어">
            <div className="seg" role="group" aria-label="광 주기">
              <button type="button" aria-pressed={a.ledMode === 'day'} onClick={() => onUpdateActuator(selectedZoneId, { ledMode: 'day' })}>
                주간
              </button>
              <button type="button" aria-pressed={a.ledMode === 'night'} onClick={() => onUpdateActuator(selectedZoneId, { ledMode: 'night' })}>
                야간
              </button>
            </div>
          </Row>
          <div className="bg-surface border border-line rounded-lg px-3 py-2.5 space-y-2">
            <div className="flex justify-between text-sm">
              <label htmlFor="led-intensity" className="font-semibold text-ink">
                LED 출력
              </label>
              <span className="font-semibold text-ink-strong">{a.ledGrowLight ? `${a.ledIntensity}%` : '소등'}</span>
            </div>
            <input
              id="led-intensity"
              type="range"
              min="0"
              max="100"
              step="5"
              value={a.ledIntensity}
              onChange={(e) => {
                const val = parseInt(e.target.value, 10);
                onUpdateActuator(selectedZoneId, { ledIntensity: val, ledGrowLight: val > 0 });
              }}
              className="w-full"
            />
          </div>
        </section>

        <section className="panel p-5 space-y-3" aria-labelledby="misc-title">
          <h3 id="misc-title" className="text-base font-semibold text-ink-strong">
            댐퍼, CO₂, 급수
          </h3>
          <DamperControl actuator={a} onUpdate={(updates) => onUpdateActuator(selectedZoneId, updates)} />
          <Row title="CO₂ 공급" desc="주간 광합성 촉진, 800~1000 ppm">
            <button
              type="button"
              aria-pressed={a.co2Generator}
              className="btn btn-secondary btn-sm"
              onClick={() => {
                const next = !a.co2Generator;
                onUpdateActuator(selectedZoneId, { co2Generator: next });
                EdgeNotice.toast(next ? `${activeZone.code} CO₂ 공급을 시작했습니다.` : `${activeZone.code} CO₂ 공급을 정지했습니다.`);
              }}
            >
              {a.co2Generator ? '공급 중' : '정지'}
            </button>
          </Row>
          <Row title="관수 펌프" desc="양액 급수">
            <button
              type="button"
              aria-pressed={a.irrigationPump}
              className="btn btn-secondary btn-sm"
              onClick={() => {
                const next = !a.irrigationPump;
                onUpdateActuator(selectedZoneId, { irrigationPump: next, nutrientValve: next });
                EdgeNotice.toast(next ? `${activeZone.code} 급수를 시작했습니다.` : `${activeZone.code} 급수를 정지했습니다.`);
              }}
            >
              {a.irrigationPump ? '급수 중' : '정지'}
            </button>
          </Row>
        </section>
      </div>
    </div>
  );
};
