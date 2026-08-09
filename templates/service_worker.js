self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (_error) {
    payload = {body: "Ai o alertă nouă în PriceMatch."};
  }
  event.waitUntil(self.registration.showNotification(payload.title || "PriceMatch", {
    body: payload.body || "Ai o alertă nouă.",
    tag: payload.tag || "pricematch-alert",
    renotify: true,
    data: {url: payload.url || "/app/alerte/"}
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || "/app/alerte/", self.location.origin).href;
  event.waitUntil(clients.matchAll({type: "window", includeUncontrolled: true}).then((windows) => {
    for (const client of windows) {
      if (client.url === target && "focus" in client) return client.focus();
    }
    return clients.openWindow ? clients.openWindow(target) : undefined;
  }));
});
