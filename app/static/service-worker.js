'use strict';

const safeLocalUrl = (value) => {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//')) return '/month';
  try {
    const parsed = new URL(value, self.location.origin);
    return parsed.origin === self.location.origin ? `${parsed.pathname}${parsed.search}${parsed.hash}` : '/month';
  } catch (_) {
    return '/month';
  }
};

self.addEventListener('push', (event) => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch (_) { payload = {}; }
  event.waitUntil(self.registration.showNotification(payload.title || 'Re-Deputy', {
    body: payload.body || 'Your roster has an update.',
    icon: '/static/favicon.svg',
    badge: '/static/favicon.svg',
    data: { url: safeLocalUrl(payload.url) },
  }));
});

const openNotificationTarget = async (windows, target) => {
  for (const client of windows) {
    if (new URL(client.url).origin !== self.location.origin) continue;
    try {
      const navigated = await client.navigate(target);
      if (!navigated) return clients.openWindow(target);
      return await navigated.focus();
    } catch (_) {
      return clients.openWindow(target);
    }
  }
  return clients.openWindow(target);
};

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = safeLocalUrl(event.notification?.data?.url);
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then((windows) => openNotificationTarget(windows, target)),
  );
});
