const root = document.querySelector("[data-barcode-scanner]");

if (root) {
  const video = document.getElementById("barcode-video");
  const start = document.getElementById("start-scanner");
  const stop = document.getElementById("stop-scanner");
  const status = document.getElementById("scanner-status");
  const codeInput = document.getElementById("barcode-code");
  const result = document.getElementById("barcode-result");
  const product = document.getElementById("barcode-product");
  const expectedProduct = root.dataset.expectedProduct || "";
  let stream = null;
  let timer = null;
  let detector = null;

  const lookup = async (code) => {
    codeInput.value = code;
    const response = await fetch(`${root.dataset.lookupUrl}?code=${encodeURIComponent(code)}`, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
    const data = await response.json();
    if (data.found) {
      if (expectedProduct && String(data.product.id) !== expectedProduct) {
        result.textContent = `Conflict: codul aparține deja produsului ${data.product.name}. Nu a fost schimbată selecția.`;
        result.className = "barcode-result warning";
        return;
      }
      product.value = String(data.product.id);
      result.textContent = `Găsit: ${data.product.name}${data.product.brand ? ` · ${data.product.brand}` : ""}`;
      result.className = "barcode-result success";
    } else {
      result.textContent = expectedProduct
        ? "Cod nou. Va fi atribuit produsului selectat după salvare."
        : "Cod nou. Alege produsul căruia vrei să-i atribui EAN-ul.";
      result.className = "barcode-result warning";
    }
  };

  const stopCamera = () => {
    if (timer) window.clearInterval(timer);
    if (stream) stream.getTracks().forEach((track) => track.stop());
    timer = null;
    stream = null;
    video.srcObject = null;
    start.disabled = false;
    stop.disabled = true;
    status.textContent = "Camera este oprită.";
  };

  start.addEventListener("click", async () => {
    if (!("BarcodeDetector" in window)) {
      status.textContent = "Browserul nu oferă BarcodeDetector. Introdu codul manual sau folosește Chrome pe Android.";
      return;
    }
    try {
      detector = new BarcodeDetector({ formats: ["ean_13", "ean_8", "upc_a", "upc_e", "itf"] });
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false });
      video.srcObject = stream;
      await video.play();
      start.disabled = true;
      stop.disabled = false;
      status.textContent = "Caut un cod de bare…";
      timer = window.setInterval(async () => {
        try {
          const codes = await detector.detect(video);
          if (codes.length) {
            await lookup(codes[0].rawValue);
            status.textContent = `Cod citit: ${codes[0].rawValue}`;
            stopCamera();
          }
        } catch (_) {
          status.textContent = "Camera pornește; ține codul nemișcat și bine luminat.";
        }
      }, 650);
    } catch (error) {
      status.textContent = `Camera nu poate fi pornită: ${error.message}`;
      stopCamera();
    }
  });

  stop.addEventListener("click", stopCamera);
  codeInput.addEventListener("change", () => {
    const code = codeInput.value.replace(/\D/g, "");
    if (code) lookup(code);
  });
  window.addEventListener("pagehide", stopCamera);
}
