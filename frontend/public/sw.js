// SMART CUBE 서비스 워커 (2026-11-24, PWA)
//
// 원칙
//   - 센서 값·장치 명령·이력·카메라·인증은 절대 캐시하지 않는다(항상 서버로). 오래된 값을 보여 주면 안 된다.
//   - 화면 파일(HTML·JS·CSS·그림·글꼴)은 "네트워크 먼저, 안 되면 저장본"으로 쓴다. 새 버전이 바로 보이고,
//     연결이 끊겨도 앱 껍데기는 열려서 "큐브와 연락이 안 돼요"를 보여 줄 수 있다.
const CACHE = 'smartcube-shell-v1';
const PRECACHE = ['/', '/manifest.webmanifest', '/smart-cube-front.webp', '/icons/icon-192.png', '/icons/icon-512.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

// 캐시하면 안 되는 요청: api(인스턴스 경로 포함), SSE·MJPEG 스트림, 사진, 개발 서버 내부 요청
function neverCache(url) {
  return url.pathname.startsWith('/api/')
    || /^\/i\/[^/]+\/api\//.test(url.pathname)
    || url.pathname.includes('/stream')
    || url.pathname.includes('/snapshots')
    || url.pathname.startsWith('/@')
    || url.pathname.includes('/node_modules/')
    || url.search.includes('t=') // 개발 서버의 갱신용 요청
    || url.hostname.endsWith('supabase.co');
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin && !url.hostname.endsWith('jsdelivr.net') && !url.hostname.endsWith('gstatic.com') && !url.hostname.endsWith('googleapis.com')) return;
  if (neverCache(url)) return;

  // 페이지 이동: 네트워크 먼저, 끊기면 저장해 둔 앱 첫 화면(SPA)으로
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then((res) => { const copy = res.clone(); caches.open(CACHE).then((c) => c.put('/', copy)); return res; })
        .catch(() => caches.match('/').then((r) => r || new Response('오프라인이에요. 연결되면 다시 열어 주세요.', { headers: { 'Content-Type': 'text/plain; charset=utf-8' } }))),
    );
    return;
  }

  // 화면 파일: 네트워크 먼저, 실패하면 저장본
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok && (res.type === 'basic' || res.type === 'cors')) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
        return res;
      })
      .catch(() => caches.match(req).then((r) => r || Response.error())),
  );
});
