// fetch 기반 SSE 읽기 (2026-09-28).
//
// 브라우저 EventSource 는 요청 헤더를 붙일 수 없어 Authorization: Bearer 를 못 보낸다.
// 그래서 fetch 로 스트림을 열어 직접 파싱한다(사용자 결정: fetch 스트림 + 헤더).
// 끊기면 1초 → 최대 15초 간격으로 스스로 다시 붙는다.

export interface SseMessage {
  event?: string;
  data: string;
  id?: string;
}

// 스트림 텍스트를 조각(chunk)으로 받아 이벤트 단위로 잘라 준다. 순수 함수라 node 시험이 된다.
export function createSseParser(onMessage: (message: SseMessage) => void): (chunk: string) => void {
  let buffer = '';
  let dataLines: string[] = [];
  let event: string | undefined;
  let id: string | undefined;

  const dispatch = () => {
    if (dataLines.length > 0) onMessage({ event, id, data: dataLines.join('\n') });
    dataLines = [];
    event = undefined;
    id = undefined;
  };

  return (chunk: string) => {
    buffer += chunk;
    let idx: number;
    // 줄 끝은 \n, \r\n 모두 허용
    while ((idx = buffer.search(/\r\n|\n|\r/)) >= 0) {
      const line = buffer.slice(0, idx);
      buffer = buffer.slice(idx + (buffer[idx] === '\r' && buffer[idx + 1] === '\n' ? 2 : 1));
      if (line === '') {
        dispatch();
        continue;
      }
      if (line.startsWith(':')) continue; // keepalive 주석
      const colon = line.indexOf(':');
      const field = colon < 0 ? line : line.slice(0, colon);
      let value = colon < 0 ? '' : line.slice(colon + 1);
      if (value.startsWith(' ')) value = value.slice(1);
      if (field === 'data') dataLines.push(value);
      else if (field === 'event') event = value;
      else if (field === 'id') id = value;
      // retry 등 나머지는 무시
    }
  };
}

export interface FetchStreamOptions {
  getToken?: () => Promise<string | null>;
  onState?: (open: boolean) => void;
  minRetryMs?: number;
  maxRetryMs?: number;
}

// url 을 열고 data 이벤트마다 onData(문자열). 돌려주는 함수로 닫는다.
export function openFetchStream(url: string, onData: (data: string) => void, options: FetchStreamOptions = {}): () => void {
  if (typeof fetch === 'undefined') return () => {};
  const minRetry = options.minRetryMs ?? 1000;
  const maxRetry = options.maxRetryMs ?? 15000;
  let closed = false;
  let controller: AbortController | null = null;
  let retry = minRetry;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const scheduleReconnect = () => {
    if (closed) return;
    timer = setTimeout(() => void connect(), retry);
    retry = Math.min(retry * 2, maxRetry);
  };

  const connect = async () => {
    if (closed) return;
    controller = new AbortController();
    const headers: Record<string, string> = { Accept: 'text/event-stream' };
    try {
      const token = options.getToken ? await options.getToken() : null;
      if (token) headers.Authorization = `Bearer ${token}`;
      const response = await fetch(url, { headers, signal: controller.signal, cache: 'no-store' });
      if (!response.ok || !response.body) {
        options.onState?.(false);
        // 401/403 은 재시도해도 같으므로 천천히
        if (response.status === 401 || response.status === 403) retry = maxRetry;
        scheduleReconnect();
        return;
      }
      options.onState?.(true);
      retry = minRetry;
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      const feed = createSseParser((m) => onData(m.data));
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        feed(decoder.decode(value, { stream: true }));
      }
      options.onState?.(false);
      scheduleReconnect();
    } catch {
      if (closed) return;
      options.onState?.(false);
      scheduleReconnect();
    }
  };

  void connect();

  return () => {
    closed = true;
    if (timer) clearTimeout(timer);
    controller?.abort();
  };
}
