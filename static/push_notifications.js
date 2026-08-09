(() => {
  const root = document.querySelector("[data-push-settings]");
  if (!root || root.dataset.ready !== "true") return;

  const status = root.querySelector("[data-push-status]");
  const enable = root.querySelector("[data-push-enable]");
  const disable = root.querySelector("[data-push-disable]");
  const test = root.querySelector("[data-push-test]");
  const csrfToken = root.querySelector("[name=csrfmiddlewaretoken]").value;
  let registration;

  const setStatus = (message, kind = "") => {
    status.textContent = message;
    status.className = `notification-status ${kind}`;
  };

  const post = async (url, body) => {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRFToken": csrfToken},
      body: JSON.stringify(body)
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "Cererea nu a reușit.");
    return result;
  };

  const applicationServerKey = (value) => {
    const padding = "=".repeat((4 - value.length % 4) % 4);
    const raw = atob((value + padding).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from([...raw].map((character) => character.charCodeAt(0)));
  };

  const refresh = async () => {
    const subscription = await registration.pushManager.getSubscription();
    enable.hidden = Boolean(subscription);
    disable.hidden = !subscription;
    test.hidden = !subscription;
    setStatus(subscription ? "Notificările sunt active pe acest dispozitiv." : "Notificările nu sunt active pe acest dispozitiv.", subscription ? "success" : "");
  };

  const initialize = async () => {
    if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
      setStatus("Browserul acesta nu oferă suport Web Push.", "error");
      enable.hidden = true;
      disable.hidden = true;
      test.hidden = true;
      return;
    }
    registration = await navigator.serviceWorker.register("/service-worker.js", {scope: "/"});
    await refresh();
  };

  enable.addEventListener("click", async () => {
    try {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") throw new Error("Permisiunea pentru notificări nu a fost acordată.");
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: applicationServerKey(root.dataset.publicKey)
      });
      await post(root.dataset.subscribeUrl, subscription.toJSON());
      await refresh();
    } catch (error) {
      setStatus(error.message, "error");
    }
  });

  disable.addEventListener("click", async () => {
    try {
      const subscription = await registration.pushManager.getSubscription();
      if (subscription) {
        await post(root.dataset.unsubscribeUrl, {endpoint: subscription.endpoint});
        await subscription.unsubscribe();
      }
      await refresh();
    } catch (error) {
      setStatus(error.message, "error");
    }
  });

  test.addEventListener("click", async () => {
    try {
      await post(root.dataset.testUrl, {});
      setStatus("Notificarea de test a fost trimisă.", "success");
    } catch (error) {
      setStatus(error.message, "error");
    }
  });

  initialize().catch((error) => setStatus(error.message, "error"));
})();
