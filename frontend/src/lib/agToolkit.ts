import { soundManager } from './sound';
import { EdgeDevice, NotificationLogItem } from '../types/smartfarm';

// 신뢰할 수 없는 문자열을 HTML에 넣기 전에 이스케이프한다.
export function escapeForHtml(value: unknown): string {
  if (typeof document === 'undefined') return String(value ?? '');
  const holder = document.createElement('div');
  holder.textContent = String(value ?? '');
  return holder.innerHTML;
}

// 아라온실 기술문서 규격: 오전/오후 H:mm
export function formatKoreanTime(date: Date = new Date()): string {
  const hours = date.getHours();
  const minutes = date.getMinutes().toString().padStart(2, '0');
  const period = hours >= 12 ? '오후' : '오전';
  const displayHours = hours % 12 === 0 ? 12 : hours % 12;
  return `${period} ${displayHours}:${minutes}`;
}

// 알림 마크업에 쓰는 인라인 아이콘. 외부 아이콘 폰트에 의존하지 않는다.
const svg = (paths: string, size = 16) =>
  `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;

export const AGIcon = {
  info: svg('<circle cx="12" cy="12" r="9"/><line x1="12" y1="11" x2="12" y2="16"/><line x1="12" y1="8" x2="12.01" y2="8"/>'),
  alert: svg('<path d="M12 3 2 20h20L12 3z"/><line x1="12" y1="10" x2="12" y2="14"/><line x1="12" y1="17" x2="12.01" y2="17"/>'),
  device: svg('<rect x="3" y="13" width="18" height="7" rx="2"/><line x1="7" y1="16.5" x2="7.01" y2="16.5"/><path d="M8 9a6 6 0 0 1 8 0"/><path d="M5.5 6.5a10 10 0 0 1 13 0"/>'),
  close: svg('<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>', 14),
  water: svg('<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"/>'),
  moon: svg('<path d="M21 13A9 9 0 1 1 11 3a7 7 0 0 0 10 10z"/>'),
};

export type NotificationEventListener = (log: NotificationLogItem) => void;
export type NotificationHandledListener = (logId: string) => void;

// 구독자 목록은 window에 둔다. 개발 서버의 핫 리로드로 이 모듈이 다시 실행돼도
// 화면(App)이 등록해 둔 구독이 끊기지 않게 하기 위해서다.
interface AGRegistry {
  logListeners: Set<NotificationEventListener>;
  handledListeners: Set<NotificationHandledListener>;
  deviceListeners: Set<(device: EdgeDevice) => void>;
}
const registry: AGRegistry = (() => {
  const g = globalThis as unknown as { __agNotificationRegistry?: AGRegistry };
  if (!g.__agNotificationRegistry) {
    g.__agNotificationRegistry = { logListeners: new Set(), handledListeners: new Set(), deviceListeners: new Set() };
  }
  g.__agNotificationRegistry.deviceListeners ??= new Set();
  return g.__agNotificationRegistry;
})();
const listeners = registry.logListeners;
const handledListeners = registry.handledListeners;

export function onDeviceRegistered(listener: (device: EdgeDevice) => void): () => void {
  registry.deviceListeners.add(listener);
  return () => registry.deviceListeners.delete(listener);
}

export function onNotificationLog(listener: NotificationEventListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function broadcastLog(log: NotificationLogItem) {
  listeners.forEach((fn) => fn(log));
}

// 확인 요청이 닫히면(사용자가 처리하면) 이력의 handled를 갱신할 수 있게 알린다.
export function onNotificationHandled(listener: NotificationHandledListener): () => void {
  handledListeners.add(listener);
  return () => handledListeners.delete(listener);
}

function broadcastHandled(logId: string) {
  handledListeners.forEach((fn) => fn(logId));
}

// item이 DOM에서 제거되는 순간을 감지해 처리 완료로 표시한다.
function watchRemoval(container: HTMLElement, item: HTMLElement, logId: string) {
  if (typeof MutationObserver === 'undefined') return;
  const observer = new MutationObserver(() => {
    if (!container.contains(item)) {
      observer.disconnect();
      broadcastHandled(logId);
    }
  });
  observer.observe(container, { childList: true });
}

interface LogMeta {
  type: NotificationLogItem['type'];
  severity: NotificationLogItem['severity'];
  handled: boolean;
  title?: string;
}

function newId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).substring(2, 6)}`;
}

// -------------------------------------------------------------
// AG.Notification (아라온실 기술문서 규격 구현)
// -------------------------------------------------------------

export interface AGNamespace {
  Notification: {
    Toast: (html: string, duration?: number) => HTMLElement;
    Message: (
      html: string,
      callback?: ((this: HTMLElement, item: HTMLElement) => void) | null,
      duration?: number
    ) => HTMLElement;
    InfoMessage: (title: string, message: string, duration?: number) => HTMLElement;
    DiscoverMessage: (data: {
      clientId: string;
      name?: string;
      type?: string;
      message?: string;
      [key: string]: unknown;
    }) => HTMLElement;
  };
}

declare global {
  interface Window {
    AG?: AGNamespace;
  }
}

interface ModalOptions {
  title: string;
  message: string;
  confirmLabel: string;
  danger: boolean;
  onConfirm?: () => void;
}

class AraNotificationEngine {
  private toastContainer: HTMLElement | null = null;
  private messageContainer: HTMLElement | null = null;

  private getToastContainer(): HTMLElement {
    if (!this.toastContainer || !document.body.contains(this.toastContainer)) {
      let el = document.querySelector('notification-toast') as HTMLElement | null;
      if (!el) {
        el = document.createElement('notification-toast');
        el.setAttribute('id', 'ag-notification-toast-container');
        el.setAttribute('role', 'status');
        el.setAttribute('aria-live', 'polite');
        document.body.appendChild(el);
      }
      this.toastContainer = el;
    }
    return this.toastContainer;
  }

  private getMessageContainer(): HTMLElement {
    if (!this.messageContainer || !document.body.contains(this.messageContainer)) {
      let el = document.querySelector('notification-message') as HTMLElement | null;
      if (!el) {
        el = document.createElement('notification-message');
        el.setAttribute('id', 'ag-notification-message-container');
        el.setAttribute('aria-live', 'polite');
        document.body.appendChild(el);
      }
      this.messageContainer = el;
    }
    return this.messageContainer;
  }

  public Toast(html: string, duration = 3000, record = true): HTMLElement {
    const container = this.getToastContainer();
    const item = document.createElement('toast-item');
    item.className = 'ag-toast-item';
    item.innerHTML = html;
    container.appendChild(item);

    if (record) broadcastLog({
      id: newId('toast'),
      type: 'toast',
      content: item.textContent || html,
      timestamp: formatKoreanTime(),
      at: Date.now(),
      severity: 'info',
      handled: true,
    });

    if (duration > 0) {
      setTimeout(() => {
        if (item.parentNode) {
          item.classList.add('fade-out');
          setTimeout(() => item.remove(), 220);
        }
      }, duration);
    }
    return item;
  }

  public Message(
    html: string,
    callback: ((this: HTMLElement, item: HTMLElement) => void) | null = null,
    duration = 0
  ): HTMLElement {
    // 규격 API: 버튼이 붙은 확인 요청. 사용자가 닫을 때까지 "확인 대기"로 집계한다.
    return this.emitMessage(html, callback, duration, { type: 'message', severity: 'warning', handled: false });
  }

  private emitMessage(
    html: string,
    callback: ((this: HTMLElement, item: HTMLElement) => void) | null,
    duration: number,
    meta: LogMeta
  ): HTMLElement {
    const container = this.getMessageContainer();
    const item = document.createElement('message-item');
    item.className = 'ag-message-item';
    item.innerHTML = html;
    container.appendChild(item);
    soundManager.playBeep('info');

    // 기술문서에서 확인한 대로 콜백의 this는 생성된 message-item이다.
    if (typeof callback === 'function') {
      try {
        callback.call(item, item);
      } catch (err) {
        console.error('[AG.Notification.Message] callback error:', err);
      }
    }

    const logId = newId('msg');
    const bodyText = (item.querySelector('.message-body')?.textContent || item.textContent || html).replace(/\s+/g, ' ').trim();
    broadcastLog({
      id: logId,
      type: meta.type,
      title: meta.title,
      content: bodyText.slice(0, 160),
      timestamp: formatKoreanTime(),
      at: Date.now(),
      severity: meta.severity,
      handled: meta.handled,
    });
    if (!meta.handled) watchRemoval(container, item, logId);

    if (duration > 0) {
      setTimeout(() => {
        if (item.parentNode) {
          item.classList.add('slide-out');
          setTimeout(() => item.remove(), 220);
        }
      }, duration);
    }
    return item;
  }

  public InfoMessage(title: string, message: string, duration = 3000): HTMLElement {
    const timeStr = formatKoreanTime();
    const markup = `
      <group-row class="background-white info-message-box">
        <row-start class="background-gray-light padding-4 message-header">
          <span class="ag-icon">${AGIcon.info}</span>
          <div class="text-bold message-title">${title}</div>
          <span class="margin-left-auto text-time">${timeStr}</span>
          <button type="button" data-action="close" class="close-icon-btn" aria-label="닫기">${AGIcon.close}</button>
        </row-start>
        <row-start class="padding-6 message-body">${message}</row-start>
      </group-row>
    `;

    return this.emitMessage(
      markup,
      function () {
        const item = this;
        const closeBtn = item.querySelector('[data-action="close"]');
        closeBtn?.addEventListener(
          'click',
          (e) => {
            e.stopPropagation();
            item.classList.add('slide-out');
            setTimeout(() => item.remove(), 200);
          },
          { once: true }
        );
      },
      duration,
      { type: 'info', severity: 'info', handled: true, title: title.replace(/<[^>]+>/g, '') }
    );
  }

  public DiscoverMessage(data: {
    clientId: string;
    name?: string;
    type?: string;
    message?: string;
    [key: string]: unknown;
  }): HTMLElement {
    const timeStr = formatKoreanTime();
    const safeClientId = escapeForHtml(data.clientId || 'UNKNOWN-NODE');
    const safeDeviceName = escapeForHtml(data.name || `센서 노드 ${safeClientId}`);
    const safeType = escapeForHtml(data.type || '복합환경 센서');
    const device: EdgeDevice = {
      id: data.clientId || newId('NODE-SN'),
      name: data.name || `센서 노드 ${data.clientId}`,
      type: 'sensor_node',
      ipAddress: typeof data.ipAddress === 'string' ? data.ipAddress : '192.168.1.182',
      macAddress: typeof data.macAddress === 'string' ? data.macAddress : '미확인',
      firmwareVersion: typeof data.firmwareVersion === 'string' ? data.firmwareVersion : '미확인',
      status: 'connected', signalStrength: -60, chipTempC: null, lastPing: '방금 전',
    };

    const markup = `
      <group-row class="background-white discover-message-box">
        <row-start class="background-indigo-light padding-4 message-header">
          <span class="ag-icon">${AGIcon.device}</span>
          <div class="text-bold message-title">새 장치 발견</div>
          <span class="margin-left-auto text-time">${timeStr}</span>
          <button type="button" data-action="close" class="close-icon-btn" aria-label="닫기">${AGIcon.close}</button>
        </row-start>
        <div class="padding-6 message-body">
          <div class="discover-card">
            <div class="device-badge">ID <code>${safeClientId}</code></div>
            <div class="device-title">${safeDeviceName}</div>
            <div class="device-meta">${safeType}, IP ${escapeForHtml(device.ipAddress)}</div>
          </div>
          <div class="discover-actions">
            <button type="button" data-action="ignore" class="btn-ag-secondary button button-small">무시</button>
            <button type="button" data-action="register" class="btn-ag-primary button button-small">장치 등록</button>
          </div>
        </div>
      </group-row>
    `;

    return this.emitMessage(
      markup,
      function () {
        const item = this;
        const dismiss = () => {
          item.classList.add('slide-out');
          setTimeout(() => item.remove(), 200);
        };
        item.querySelector('[data-action="close"]')?.addEventListener('click', dismiss, { once: true });
        item.querySelector('[data-action="ignore"]')?.addEventListener('click', dismiss, { once: true });
        item.querySelector('[data-action="register"]')?.addEventListener(
          'click',
          () => {
            registry.deviceListeners.forEach((listener) => listener(device));
            dismiss();
            window.AG?.Notification?.Toast?.(`${safeDeviceName} 장치를 등록했습니다.`, 3000);
          },
          { once: true }
        );
      },
      0,
      { type: 'discover', severity: 'info', handled: true, title: `새 장치 발견 ${safeDeviceName}` }
    );
  }

  // 화면 가운데 확인 대화상자. 긴급 경보와 되돌릴 수 없는 작업에 쓴다.
  public Modal(opts: ModalOptions): HTMLElement {
    const timeStr = formatKoreanTime();
    const titleId = newId('ag-modal-title');
    const bodyId = newId('ag-modal-body');
    const backdrop = document.createElement('div');
    backdrop.className = 'ag-modal-backdrop';
    backdrop.innerHTML = `
      <div class="ag-modal${opts.danger ? ' is-danger' : ''}" role="alertdialog" aria-modal="true" aria-labelledby="${titleId}" aria-describedby="${bodyId}">
        <div class="ag-modal-head">
          <span class="ag-icon">${opts.danger ? AGIcon.alert : AGIcon.info}</span>
          <h2 id="${titleId}">${opts.title}</h2>
          <span class="text-time">${timeStr}</span>
        </div>
        <p id="${bodyId}" class="ag-modal-body">${opts.message}</p>
        <div class="ag-modal-actions">
          <button type="button" data-action="close" class="btn btn-secondary">닫기</button>
          <button type="button" data-action="confirm" class="btn ${opts.danger ? 'btn-danger' : 'btn-primary'}">${opts.confirmLabel}</button>
        </div>
      </div>
    `;

    const logId = newId('modal');
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const close = () => {
      document.removeEventListener('keydown', onKey);
      backdrop.remove();
      previouslyFocused?.focus?.();
      broadcastHandled(logId);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    document.addEventListener('keydown', onKey);
    backdrop.addEventListener('click', (e) => {
      if (e.target === backdrop) close();
    });
    backdrop.querySelector('[data-action="close"]')?.addEventListener('click', close, { once: true });
    backdrop.querySelector('[data-action="confirm"]')?.addEventListener(
      'click',
      () => {
        opts.onConfirm?.();
        close();
      },
      { once: true }
    );

    document.body.appendChild(backdrop);
    (backdrop.querySelector('[data-action="confirm"]') as HTMLElement | null)?.focus();

    broadcastLog({
      id: logId,
      type: 'message',
      title: backdrop.querySelector('h2')?.textContent || undefined,
      content: backdrop.querySelector('p')?.textContent || '',
      timestamp: timeStr,
      at: Date.now(),
      severity: opts.danger ? 'danger' : 'warning',
      handled: false,
    });

    return backdrop;
  }
}

const engine = new AraNotificationEngine();

export const AG: AGNamespace = {
  Notification: {
    Toast: (html: string, duration = 3000) => engine.Toast(html, duration),
    Message: (html: string, callback = null, duration = 0) => engine.Message(html, callback, duration),
    InfoMessage: (title: string, message: string, duration = 3000) => engine.InfoMessage(title, message, duration),
    DiscoverMessage: (data) => engine.DiscoverMessage(data),
  },
};

// 기술문서 규격대로 window.AG로도 노출한다.
if (typeof window !== 'undefined') {
  (window as unknown as { AG: AGNamespace }).AG = AG;
}

// -------------------------------------------------------------
// 팀 권장 래퍼 (EdgeNotice): 텍스트는 이스케이프한다. persistent만 신뢰된 마크업을 받는다.
// -------------------------------------------------------------
export const EdgeNotice = {
  DEFAULT_DURATION: 3000,

  text(value: unknown): string {
    return escapeForHtml(value);
  },

  toast(message: string, duration: number = this.DEFAULT_DURATION, options: { record?: boolean } = {}): HTMLElement {
    return engine.Toast(this.text(message), duration, options.record ?? true);
  },

  info(title: string, message: string, duration: number = this.DEFAULT_DURATION): HTMLElement {
    return AG.Notification.InfoMessage(this.text(title), this.text(message), duration);
  },

  persistent(markup: string, initialize?: (item: HTMLElement) => void): HTMLElement {
    return AG.Notification.Message(markup, function () {
      initialize?.(this);
    });
  },

  // 되돌릴 수 없는 작업 확인 (소리 없음)
  confirm(title: string, message: string, onConfirm?: () => void, confirmLabel = '실행'): HTMLElement {
    return engine.Modal({
      title: this.text(title),
      message: this.text(message),
      confirmLabel: this.text(confirmLabel),
      danger: false,
      onConfirm,
    });
  },

  // 긴급 경보: 경보음 + 붉은 확인 대화상자
  dangerAlert(title: string, message: string, onConfirm?: () => void, confirmLabel = '조치 실행'): HTMLElement {
    soundManager.playBeep('danger');
    return engine.Modal({
      title: this.text(title),
      message: this.text(message),
      confirmLabel: this.text(confirmLabel),
      danger: true,
      onConfirm,
    });
  },
};
