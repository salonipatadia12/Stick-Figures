// Stick Figures service worker: shows the server's push notifications (webapp/push.py) and opens the episode when
// one is tapped. Served from /sw.js so it covers the whole site. It caches nothing: every page stays live.
'use strict';

self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) { event.waitUntil(self.clients.claim()); });

self.addEventListener('push', function (event) {
  var data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { body: event.data ? event.data.text() : '' }; }
  var title = data.title || 'Stick Figures';
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || '',
    tag: data.tag || 'stick',
    renotify: true,
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    data: { url: data.url || '/' }
  }));
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var url = new URL((event.notification.data && event.notification.data.url) || '/', self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
    for (var i = 0; i < list.length; i++) {
      if (list[i].url === url && 'focus' in list[i]) return list[i].focus();
    }
    return self.clients.openWindow(url);
  }));
});
