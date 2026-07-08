const CACHE_NAME = 'lotto649-ironlaw-20260709011304';
const APP_SHELL = [
  './',
  './index.html',
  './offline.html',
  './latest_analysis.json',
  './latest_battle_report.html',
  './latest_battle_report.md',
  './prediction_history.json',
  './self_test_report.json',
  './system_health.json',
  './manifest.webmanifest'
];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key)))));
  self.clients.claim();
});
self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  event.respondWith(fetch(request).then(response => {
    const copy = response.clone();
    caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
    return response;
  }).catch(() => caches.match(request).then(cached => cached || caches.match('./offline.html'))));
});
