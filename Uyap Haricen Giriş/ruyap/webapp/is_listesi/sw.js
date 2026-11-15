// İş Listesi service worker: yalnız bildirimler (çevrimdışı önbellek YOK — veriler her zaman sunucudan,
// eski/yetkisiz içerik cihazda kalmasın). Kapsam: /is-listesi (Service-Worker-Allowed başlığıyla).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch (_) { d = { title: "İş Listesi", body: event.data ? event.data.text() : "" }; }
  event.waitUntil(self.registration.showNotification(d.title || "İş Listesi", {
    body: d.body || "",
    tag: d.tag || "zs",
    renotify: true,
    icon: "/is-listesi/static/icon-192.png",
    badge: "/is-listesi/static/badge-72.png",
    lang: "tr",
    data: { url: d.url || "/is-listesi" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL((event.notification.data && event.notification.data.url) || "/is-listesi", self.location.origin).href;
  event.waitUntil((async () => {
    const list = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of list) {
      if (new URL(c.url).pathname.startsWith("/is-listesi")) {
        await c.focus();
        c.postMessage({ type: "zs-open", url });
        return;
      }
    }
    await self.clients.openWindow(url);
  })());
});

// Tarayıcı aboneliği kendiliğinden yenilerse sayfa açıldığında yeniden kaydedilir (zs.js).
self.addEventListener("pushsubscriptionchange", () => {});
