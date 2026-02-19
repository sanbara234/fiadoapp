const CACHE = 'fiadoapp-v1';
const STATIC = ['/', '/static/index.html', '/manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(STATIC)).catch(()=>{})
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  // Solo cachear GET, no las llamadas a la API
  if(e.request.method !== 'GET') return;
  if(e.request.url.includes('/auth/') || 
     e.request.url.includes('/contactos') ||
     e.request.url.includes('/ventas') ||
     e.request.url.includes('/stock') ||
     e.request.url.includes('/negocios') ||
     e.request.url.includes('/resumen')) return;

  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});
