// 설치형 앱(PWA)용 서비스 워커.
// 항상 인터넷의 최신 파일을 먼저 쓰고, 인터넷이 안 될 때만 마지막으로 받아 둔 파일을 보여준다.
const CACHE = 'academy-v1';
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== self.location.origin) return;   // 그 밖의 요청은 손대지 않는다
  const key = url.origin + url.pathname;   // ?t=... 같은 꼬리표는 떼고 저장 (파일마다 한 벌만 보관)
  e.respondWith(
    fetch(req).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(key, copy)); }
      return res;
    }).catch(() => caches.match(key).then((hit) => hit || Response.error()))
  );
});
