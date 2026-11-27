import React, { useState } from 'react';
import { Copy, Check } from 'lucide-react';
import { AG, EdgeNotice, AGIcon, escapeForHtml, formatKoreanTime } from '../lib/agToolkit';

export async function tryCopyCode(text: string, write = (value: string) => navigator.clipboard.writeText(value)): Promise<boolean> {
  try {
    await write(text);
    return true;
  } catch {
    return false;
  }
}

const CopyButton = ({ copied, onCopy }: { copied: boolean; onCopy: () => void }) => (
  <button type="button" onClick={onCopy} className="btn btn-ghost btn-sm" aria-label="호출 코드 복사">
    {copied ? <Check className="w-4 h-4" aria-hidden="true" /> : <Copy className="w-4 h-4" aria-hidden="true" />}
    <span>{copied ? '복사됨' : '복사'}</span>
  </button>
);

// 개발자 도구: 아라온실 AG.Notification API 4종과 EdgeNotice 래퍼를 직접 실행해 본다.
export const NotificationConsole: React.FC = () => {
  const [copiedSection, setCopiedSection] = useState<string | null>(null);
  const [copyError, setCopyError] = useState('');

  const [toastText, setToastText] = useState('환경 데이터 동기화를 마쳤습니다.');
  const [toastDuration, setToastDuration] = useState(3000);

  const [infoTitle, setInfoTitle] = useState('SC-01 온도');
  const [infoMessage, setInfoMessage] = useState('온도가 목표 범위를 벗어났습니다.');
  const [infoDuration, setInfoDuration] = useState(3000);

  const [discoverClientId, setDiscoverClientId] = useState('NODE-AG-0941');
  const [discoverDeviceName, setDiscoverDeviceName] = useState('SC-01 광량 센서 노드');

  const [decisionTitle, setDecisionTitle] = useState('자동 환기 수동 전환');
  const [decisionBody, setDecisionBody] = useState('풍속 10 m/s 이상이 감지되어 천창을 30%로 고정합니다.');

  const handleCopy = async (text: string, id: string) => {
    setCopiedSection(null);
    setCopyError('');
    if (await tryCopyCode(text)) {
      setCopiedSection(id + text);
      EdgeNotice.toast('호출 코드를 복사했습니다.', 2000);
    } else {
      setCopyError('복사하지 못했습니다. 브라우저의 클립보드 권한을 확인하거나 표시된 코드를 직접 복사해 주세요.');
    }
  };

  const runDecisionMessage = () => {
    const markup = `
      <group-row class="background-white">
        <row-start class="background-gray-light padding-4">
          <span class="ag-icon">${AGIcon.alert}</span>
          <div class="text-bold">${escapeForHtml(decisionTitle)}</div>
          <span class="margin-left-auto text-time">${formatKoreanTime()}</span>
        </row-start>
        <row-start class="padding-6 message-body">${escapeForHtml(decisionBody)}</row-start>
        <row-between class="padding-2 justify-end">
          <button type="button" data-action="close" class="button button-small color-gray">닫기</button>
          <button type="button" data-action="confirm" class="button button-small color-indigo">확인</button>
        </row-between>
      </group-row>
    `;

    AG.Notification.Message(markup, function () {
      const item = this;
      item.querySelector('[data-action="confirm"]')?.addEventListener(
        'click',
        () => {
          EdgeNotice.toast(`"${decisionTitle}" 요청을 확인했습니다.`);
          item.remove();
        },
        { once: true }
      );
      item.querySelector('[data-action="close"]')?.addEventListener('click', () => item.remove(), { once: true });
    });
  };

  const toastCode = `AG.Notification.Toast(${JSON.stringify(toastText)}, ${toastDuration});`;
  const infoCode = `AG.Notification.InfoMessage(${JSON.stringify(infoTitle)}, ${JSON.stringify(infoMessage)}, ${infoDuration});`;
  const discoverCode = `AG.Notification.DiscoverMessage(${JSON.stringify({ clientId: discoverClientId, name: discoverDeviceName, type: '광량 센서' })});`;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-ink-strong">알림 API 테스트</h2>
          <p className="text-sm text-ink-muted">
            아라온실 AG.Notification 4종(Toast, InfoMessage, Message, DiscoverMessage)과 팀 래퍼 EdgeNotice를 직접 실행합니다. 운영 화면에는 나타나지
            않는 개발자 도구입니다.
          </p>
        </div>
        <span className="badge badge-ok">window.AG 연결됨</span>
      </header>

      {copyError && <p role="alert" className="text-sm text-danger">{copyError}</p>}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <section className="panel p-5 space-y-4" aria-labelledby="api-toast">
          <div>
            <h3 id="api-toast" className="text-base font-semibold text-ink-strong font-mono">
              Toast(html, duration)
            </h3>
            <p className="text-sm text-ink-muted">화면 아래 가운데. 기본 3,000 ms 뒤 사라짐.</p>
          </div>
          <div className="field">
            <label htmlFor="toast-text">메시지</label>
            <input id="toast-text" type="text" value={toastText} onChange={(e) => setToastText(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="toast-duration">유지 시간 {toastDuration} ms</label>
            <input
              id="toast-duration"
              type="range"
              min="1000"
              max="6000"
              step="500"
              value={toastDuration}
              onChange={(e) => setToastDuration(parseInt(e.target.value, 10))}
            />
          </div>
          <div className="flex items-start gap-2">
            <pre className="code flex-1">{toastCode}</pre>
            <CopyButton copied={copiedSection === 'toast' + toastCode} onCopy={() => { void handleCopy(toastCode, 'toast'); }} />
          </div>
          <div className="flex gap-2">
            <button type="button" onClick={() => AG.Notification.Toast(toastText, toastDuration)} className="btn btn-primary flex-1">
              Toast 실행
            </button>
            <button type="button" onClick={() => EdgeNotice.toast(toastText, toastDuration)} className="btn btn-secondary">
              EdgeNotice.toast 실행
            </button>
          </div>
        </section>

        <section className="panel p-5 space-y-4" aria-labelledby="api-info">
          <div>
            <h3 id="api-info" className="text-base font-semibold text-ink-strong font-mono">
              InfoMessage(title, message, duration)
            </h3>
            <p className="text-sm text-ink-muted">화면 오른쪽 아래. 제목, 시각, 닫기 버튼 포함.</p>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="field">
              <label htmlFor="info-title">제목</label>
              <input id="info-title" type="text" value={infoTitle} onChange={(e) => setInfoTitle(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="info-duration">유지 시간 (ms)</label>
              <input
                id="info-duration"
                type="number"
                value={infoDuration}
                step={1000}
                onChange={(e) => setInfoDuration(parseInt(e.target.value, 10) || 0)}
              />
            </div>
          </div>
          <div className="field">
            <label htmlFor="info-message">본문</label>
            <textarea id="info-message" rows={2} value={infoMessage} onChange={(e) => setInfoMessage(e.target.value)} />
          </div>
          <div className="flex items-start gap-2">
            <pre className="code flex-1">{infoCode}</pre>
            <CopyButton copied={copiedSection === 'info' + infoCode} onCopy={() => { void handleCopy(infoCode, 'info'); }} />
          </div>
          <div className="flex gap-2">
            <button type="button" onClick={() => AG.Notification.InfoMessage(infoTitle, infoMessage, infoDuration)} className="btn btn-primary flex-1">
              InfoMessage 실행
            </button>
            <button type="button" onClick={() => EdgeNotice.info(infoTitle, infoMessage, infoDuration)} className="btn btn-secondary">
              EdgeNotice.info 실행
            </button>
          </div>
        </section>

        <section className="panel p-5 space-y-4" aria-labelledby="api-message">
          <div>
            <h3 id="api-message" className="text-base font-semibold text-ink-strong font-mono">
              Message(html, callback, duration = 0)
            </h3>
            <p className="text-sm text-ink-muted">버튼이나 입력을 포함한 확인 요청. duration 0이면 사용자가 닫을 때까지 남는다.</p>
          </div>
          <div className="field">
            <label htmlFor="decision-title">제목</label>
            <input id="decision-title" type="text" value={decisionTitle} onChange={(e) => setDecisionTitle(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="decision-body">본문</label>
            <input id="decision-body" type="text" value={decisionBody} onChange={(e) => setDecisionBody(e.target.value)} />
          </div>
          <div className="bg-surface border border-line rounded-lg p-3 text-sm text-ink space-y-1">
            <p>콜백 안의 this는 생성된 message-item 요소다. 공식 문서의 "return true가 필요하다"는 설명과 달리 반환값은 무시되며 this.remove()로 닫는다.</p>
          </div>
          <button type="button" onClick={runDecisionMessage} className="btn btn-primary w-full">
            확인 요청 실행
          </button>
        </section>

        <section className="panel p-5 space-y-4" aria-labelledby="api-discover">
          <div>
            <h3 id="api-discover" className="text-base font-semibold text-ink-strong font-mono">
              DiscoverMessage(data)
            </h3>
            <p className="text-sm text-ink-muted">새 장치 발견 알림. 등록과 무시 버튼이 붙는다.</p>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="field">
              <label htmlFor="discover-id">장치 ID</label>
              <input id="discover-id" type="text" value={discoverClientId} onChange={(e) => setDiscoverClientId(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="discover-name">장치 이름</label>
              <input id="discover-name" type="text" value={discoverDeviceName} onChange={(e) => setDiscoverDeviceName(e.target.value)} />
            </div>
          </div>
          <div className="flex items-start gap-2">
            <pre className="code flex-1">{discoverCode}</pre>
            <CopyButton copied={copiedSection === 'discover' + discoverCode} onCopy={() => { void handleCopy(discoverCode, 'discover'); }} />
          </div>
          <button
            type="button"
            onClick={() => AG.Notification.DiscoverMessage({ clientId: discoverClientId, name: discoverDeviceName, type: '광량 센서' })}
            className="btn btn-primary w-full"
          >
            DiscoverMessage 실행
          </button>
        </section>
      </div>

      <section className="panel" aria-labelledby="compat-title">
        <div className="px-5 py-3 border-b border-line">
          <h3 id="compat-title" className="text-base font-semibold text-ink-strong">
            기술문서와 실제 동작 비교
          </h3>
        </div>
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">항목</th>
                <th scope="col">문서 설명</th>
                <th scope="col">공개 번들 실제 동작</th>
                <th scope="col">이 화면의 대응</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="font-semibold text-ink-strong">Message 닫기</td>
                <td>콜백에서 return true 필요</td>
                <td>반환값을 무시함</td>
                <td>콜백 안에서 this.remove() 호출</td>
              </tr>
              <tr>
                <td className="font-semibold text-ink-strong">Toast 시간</td>
                <td>duration 임의 지정 가능</td>
                <td>CSS 애니메이션이 3초로 고정</td>
                <td>JS 타이머로 제거하고 CSS는 등장만 담당</td>
              </tr>
              <tr>
                <td className="font-semibold text-ink-strong">보안 (XSS)</td>
                <td>언급 없음</td>
                <td>모든 API가 innerHTML에 직접 대입</td>
                <td>EdgeNotice가 문자열을 항상 이스케이프</td>
              </tr>
              <tr>
                <td className="font-semibold text-ink-strong">InfoMessage 시각</td>
                <td>언급 없음</td>
                <td>오전/오후 H:mm 형식 내장</td>
                <td>같은 형식 사용</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
};
